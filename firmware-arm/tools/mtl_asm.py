#!/usr/bin/env python3
"""Metal (VLISP) bytecode assembler / encoder.

The inverse of `mtl_dis.py`: takes a structured description of a Metal
program (globals + functions + instructions) and emits a `.bin` byte-for-
byte compatible with what `mtl_compiler` produces — the same blob the
on-device VM loads via `loaderInit()`.

This is the BASE LAYER of a future full Phase 7a Python `mtl_compiler`:
the parser/codegen produces the structured form, this module turns it
into the actual `.bin`. Splitting the two means we can validate the
encoder NOW (round-trip a hand-built program through the disassembler)
without needing the whole parser.

Format reference (cross-checked against `nabgcc/src/vm/vloader.c`):

    .bin layout:
        [globals_size:i32]
        [globals_data:bytes]          (globals_size − 4 bytes)
        [code_size:i32]
        [code:bytes]                  (code_size bytes)
        [nbfun:i16]
        [funtable:i32 × nbfun]        (start offset of each fun, into code)

    globals encoding (recursive; mirrors `vloader.c::loaderInitRec`):
        nil      -> int32(-1)
        int N    -> int32(N << 1)               # bit 0 == 0
        string B -> int32(((len(B) << 1) | 1) << 1 | 1), then raw bytes
                    # wait — see the read side:
                    #   l = i32;  l>>=1; if (l&1): tuple, else: string
                    # so encoding is:
                    #   string:  l = ((len << 1) | 0) << 1 | 1
                    #         =  (len << 2) | 1
                    #   tuple:   l = ((count << 1) | 1) << 1 | 1
                    #         =  (count << 2) | 3
        tuple T  -> int32((count << 2) | 3), then each item recursively

    function body header (per function, stored inside the `code` section):
        [nargs:u8]
        [nlocals:u16]
        [body:bytes]                  (a sequence of instructions)

    instruction encoding:
        op            ::= u8 (opcode 0..152, see vbc.h)
        with operand types depending on op:
            OPintb       : op u8(value)
            OPint        : op i32(value)
            OPgetlocalb  : op u8(idx)
            OPmktabb     : op u8(size)
            OPdeftabb    : op u8(size)
            OPfetchb     : op u8(idx)
            OPgetglobalb : op u8(idx)
            OPsetlocalb  : op u8(idx)
            OPsetstructb : op u8(idx)
            OPcallrb     : op u8(narg)
            OPgoto       : op i16(offset from function body start)
            OPelse       : op i16(offset from function body start)
            everything else  : op only (stack-based)

Usage (as a library):

    >>> from mtl_asm import Encoder, Op
    >>> enc = Encoder()
    >>> enc.add_global(0)
    >>> fn = enc.new_function(nargs=0, nlocals=0)
    >>> fn.intb(42)             # push 42
    >>> fn.op(Op.OPret)         # return
    >>> bin_bytes = enc.emit()

Or as a CLI (smoke-tests the encoder by round-tripping a tiny program):

    python3 mtl_asm.py --self-test

Stand-alone — stdlib only.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Opcode constants — verbatim from inc/vm/vbc.h, mirrored from mtl_dis.py.
# ---------------------------------------------------------------------------
class Op:
    OPexec = 0;       OPret = 1;          OPintb = 2;        OPint = 3
    OPnil = 4;        OPdrop = 5;         OPdup = 6;         OPgetlocalb = 7
    OPgetlocal = 8;   OPadd = 9;          OPsub = 10;        OPmul = 11
    OPdiv = 12;       OPmod = 13;         OPand = 14;        OPor = 15
    OPeor = 16;       OPshl = 17;         OPshr = 18;        OPneg = 19
    OPnot = 20;       OPnon = 21;         OPeq = 22;         OPne = 23
    OPlt = 24;        OPgt = 25;          OPle = 26;         OPge = 27
    OPgoto = 28;      OPelse = 29;        OPmktabb = 30;     OPmktab = 31
    OPdeftabb = 32;   OPdeftab = 33;      OPfetchb = 34;     OPfetch = 35
    OPgetglobalb = 36; OPgetglobal = 37;  OPSecho = 38;      OPIecho = 39
    OPsetlocalb = 40; OPsetlocal = 41;    OPsetglobal = 42;  OPsetstructb = 43
    OPsetstruct = 44; OPhd = 45;          OPtl = 46;         OPsetlocal2 = 47
    OPstore = 48;     OPcall = 49;        OPcallrb = 50;     OPcallr = 51
    OPfirst = 52;     OPtime_ms = 53;     OPtabnew = 54;     OPfixarg = 55
    OPabs = 56;       OPmax = 57;         OPmin = 58;        OPrand = 59
    OPsrand = 60;     OPtime = 61;        OPstrnew = 62;     OPstrset = 63
    OPstrcpy = 64;    OPvstrcmp = 65;     OPstrfind = 66;    OPstrfindrev = 67
    OPstrlen = 68;    OPstrget = 69;      OPstrsub = 70;     OPstrcat = 71
    OPtablen = 72;    OPstrcatlist = 73;  OPled = 74;        OPmotorset = 75
    OPmotorget = 76;  OPbutton2 = 77;     OPbutton3 = 78;    OPplayStart = 79
    OPplayFeed = 80;  OPplayStop = 81;    OPload = 82;       OPudpStart = 83
    OPudpCb = 84;     OPudpStop = 85;     OPudpSend = 86;    OPgc = 87
    OPtcpOpen = 88;   OPtcpClose = 89;    OPtcpSend = 90;    OPtcpCb = 91
    OPsave = 92;      OPbytecode = 93;    OPloopcb = 94;     OPIecholn = 95
    OPSecholn = 96;   OPtcpListen = 97;   OPenvget = 98;     OPenvset = 99
    OPsndVol = 100;   OPrfidGet = 101;    OPplayTime = 102;  OPnetCb = 103
    OPnetSend = 104;  OPnetState = 105;   OPnetMac = 106;    OPnetChk = 107
    OPnetSetmode = 108; OPnetScan = 109;  OPnetAuth = 110;   OPrecStart = 111
    OPrecStop = 112;  OPrecVol = 113;     OPnetSeqAdd = 114; OPstrgetword = 115
    OPstrputword = 116; OPatoi = 117;     OPhtoi = 118;      OPitoa = 119
    OPctoa = 120;     OPitoh = 121;       OPctoh = 122;      OPitobin2 = 123
    OPlistswitch = 124; OPlistswitchstr = 125; OPsndRefresh = 126; OPsndWrite = 127
    OPsndRead = 128;  OPsndFeed = 129;    OPsndAmpli = 130;  OPcorePP = 131
    OPcorePush = 132; OPcorePull = 133;   OPcoreBit0 = 134;  OPtcpEnable = 135
    OPreboot = 136;   OPstrcmp = 137;     OPadp2wav = 138;   OPwav2adp = 139
    OPalaw2wav = 140; OPwav2alaw = 141;   OPnetPmk = 142;    OPflashFirmware = 143
    OPcrypt = 144;    OPuncrypt = 145;    OPnetRssi = 146;   OPrfidGetList = 147
    OPrfidRead = 148; OPrfidWrite = 149;  OPi2cRead = 150;   OPi2cWrite = 151
    OPverifySig = 152


# Opcodes that take a 1-byte unsigned operand.
_OP_U8 = {2, 7, 30, 32, 34, 36, 40, 43, 50}
# Opcode that takes a 4-byte signed int.
_OP_I32 = {3}
# Opcodes that take a 2-byte unsigned offset (relative to function body start).
_OP_JMP = {28, 29}


# Globals encoding — sentinel + helpers.
NIL = object()


# ---------------------------------------------------------------------------
# Function body builder
# ---------------------------------------------------------------------------
@dataclass
class FunctionBody:
    """Builder for one function's instruction stream. Tracks pending
    forward jumps so a goto/else target can be back-patched once the
    landing site's offset is known.
    """
    nargs: int = 0
    nlocals: int = 0
    code: bytearray = field(default_factory=bytearray)
    # Map of label_name -> bytecode offset (resolved labels).
    labels: dict[str, int] = field(default_factory=dict)
    # List of (operand_offset, label_name) for pending forward jumps.
    pending_jumps: list[tuple[int, str]] = field(default_factory=list)

    # ---- raw opcode emit -------------------------------------------------
    def op(self, opcode: int) -> "FunctionBody":
        """Emit a stack-based opcode with no operand."""
        if opcode in _OP_U8 or opcode in _OP_I32 or opcode in _OP_JMP:
            raise ValueError(
                f"opcode {opcode} requires an operand; use .opb/.opi/.opjmp"
            )
        self.code.append(opcode & 0xFF)
        return self

    def opb(self, opcode: int, operand: int) -> "FunctionBody":
        """Emit an opcode with a 1-byte unsigned operand."""
        if opcode not in _OP_U8:
            raise ValueError(f"opcode {opcode} does not take a u8 operand")
        if not (0 <= operand <= 0xFF):
            raise ValueError(f"u8 operand out of range: {operand}")
        self.code.append(opcode & 0xFF)
        self.code.append(operand & 0xFF)
        return self

    def opi(self, opcode: int, operand: int) -> "FunctionBody":
        """Emit an opcode with a 4-byte signed int operand."""
        if opcode not in _OP_I32:
            raise ValueError(f"opcode {opcode} does not take an i32 operand")
        if not (-(2**31) <= operand < 2**31):
            raise ValueError(f"i32 operand out of range: {operand}")
        self.code.append(opcode & 0xFF)
        self.code += struct.pack("<i", operand)
        return self

    def opjmp(self, opcode: int, label: str) -> "FunctionBody":
        """Emit a jump opcode whose target is `label`. If the label is
        already defined, encodes the offset immediately; otherwise records
        a pending fix-up.
        """
        if opcode not in _OP_JMP:
            raise ValueError(f"opcode {opcode} is not a jump")
        self.code.append(opcode & 0xFF)
        operand_off = len(self.code)
        if label in self.labels:
            offset = self.labels[label]
            self.code += struct.pack("<H", offset)
        else:
            self.code += b"\x00\x00"
            self.pending_jumps.append((operand_off, label))
        return self

    def label(self, name: str) -> "FunctionBody":
        """Mark the current position as `name`. Resolves any pending jumps
        to this label.
        """
        if name in self.labels:
            raise ValueError(f"label {name!r} already defined")
        pos = len(self.code)
        self.labels[name] = pos
        # Back-patch any earlier forward jumps to this label.
        still_pending = []
        for operand_off, lbl in self.pending_jumps:
            if lbl == name:
                struct.pack_into("<H", self.code, operand_off, pos)
            else:
                still_pending.append((operand_off, lbl))
        self.pending_jumps = still_pending
        return self

    # ---- convenience wrappers (most-used cases) --------------------------
    def intb(self, v: int) -> "FunctionBody":
        """Push a small int (one byte)."""
        return self.opb(Op.OPintb, v)

    def int_(self, v: int) -> "FunctionBody":
        """Push an int (four bytes)."""
        return self.opi(Op.OPint, v)

    def nil(self) -> "FunctionBody":
        return self.op(Op.OPnil)

    def ret(self) -> "FunctionBody":
        return self.op(Op.OPret)

    def drop(self) -> "FunctionBody":
        return self.op(Op.OPdrop)

    def goto(self, label: str) -> "FunctionBody":
        return self.opjmp(Op.OPgoto, label)

    def else_(self, label: str) -> "FunctionBody":
        return self.opjmp(Op.OPelse, label)

    def call_fun(self, fun_index: int) -> "FunctionBody":
        """Push the function index then OPexec."""
        if 0 <= fun_index < 256:
            self.intb(fun_index)
        else:
            self.int_(fun_index)
        return self.op(Op.OPexec)

    def header_bytes(self) -> bytes:
        """The 3-byte function header [nargs:u8][nlocals:u16]."""
        if not (0 <= self.nargs <= 0xFF):
            raise ValueError(f"nargs out of range: {self.nargs}")
        if not (0 <= self.nlocals <= 0xFFFF):
            raise ValueError(f"nlocals out of range: {self.nlocals}")
        return struct.pack("<BH", self.nargs, self.nlocals)

    def finalize(self) -> bytes:
        if self.pending_jumps:
            unresolved = ", ".join(sorted({lbl for _, lbl in self.pending_jumps}))
            raise ValueError(f"unresolved jump labels: {unresolved}")
        return bytes(self.code)


# ---------------------------------------------------------------------------
# Top-level encoder
# ---------------------------------------------------------------------------
class Encoder:
    """Builds a Metal `.bin` blob. Pattern:

        enc = Encoder()
        enc.add_global(0)               # globals appear in stack order
        enc.add_global("hello")
        fn = enc.new_function(nargs=0, nlocals=0)
        fn.intb(42).ret()
        out_bytes = enc.emit()
    """

    def __init__(self) -> None:
        self._globals: list[Any] = []
        self._functions: list[FunctionBody] = []

    # ---- globals ---------------------------------------------------------
    def add_global(self, value: Any) -> int:
        """Append a global. Returns its index.

        Accepted Python types:
          - `NIL` sentinel       -> nil
          - `int`                -> int
          - `bytes` / `str`      -> string
          - `tuple` of the above -> tuple (recursive)
        """
        self._globals.append(value)
        return len(self._globals) - 1

    def _encode_global(self, v: Any) -> bytes:
        if v is NIL or v is None:
            return struct.pack("<i", -1)
        if isinstance(v, int):
            # Compiler tags ints with bit-0=0 by shifting left; on the read
            # side, vloader.c pushes the raw l (no shift), and the runtime's
            # VAL representation does the tagging. So on disk the int is
            # written as the already-tagged form `n << 1`. Mirror that.
            tagged = v << 1
            if not (-(2**31) <= tagged < 2**31):
                raise ValueError(f"int global doesn't fit in 32 bits: {v}")
            return struct.pack("<i", tagged)
        if isinstance(v, str):
            v = v.encode("utf-8")
        if isinstance(v, bytes):
            tag = (len(v) << 2) | 1  # string tag: bit0=1, bit1=0
            return struct.pack("<i", tag) + v
        if isinstance(v, tuple):
            tag = (len(v) << 2) | 3  # tuple tag: bit0=1, bit1=1
            out = bytearray(struct.pack("<i", tag))
            for item in v:
                out += self._encode_global(item)
            return bytes(out)
        raise TypeError(f"cannot encode global of type {type(v).__name__}: {v!r}")

    def _emit_globals_section(self) -> bytes:
        """Returns [globals_size:i32][data:globals_size-4]."""
        data = bytearray()
        for g in self._globals:
            data += self._encode_global(g)
        # The header includes itself: total section length = 4 + len(data).
        total = 4 + len(data)
        return struct.pack("<i", total) + bytes(data)

    # ---- functions -------------------------------------------------------
    def new_function(self, *, nargs: int = 0, nlocals: int = 0) -> FunctionBody:
        fn = FunctionBody(nargs=nargs, nlocals=nlocals)
        self._functions.append(fn)
        return fn

    def _emit_code_section(self) -> tuple[bytes, list[int]]:
        """Returns ([code_size:i32][code], [funtable_starts])."""
        code = bytearray()
        starts: list[int] = []
        for fn in self._functions:
            starts.append(len(code))
            code += fn.header_bytes()
            code += fn.finalize()
        return struct.pack("<i", len(code)) + bytes(code), starts

    def _emit_funtable_section(self, starts: list[int]) -> bytes:
        if not (0 <= len(starts) <= 0xFFFF):
            raise ValueError(f"too many functions: {len(starts)}")
        out = bytearray(struct.pack("<H", len(starts)))
        for s in starts:
            out += struct.pack("<i", s)
        return bytes(out)

    # ---- main entry ------------------------------------------------------
    def emit(self) -> bytes:
        globals_section = self._emit_globals_section()
        code_section, starts = self._emit_code_section()
        funtable_section = self._emit_funtable_section(starts)
        return globals_section + code_section + funtable_section


# ---------------------------------------------------------------------------
# Self-test: build a tiny program, round-trip through the disassembler.
# ---------------------------------------------------------------------------
def self_test() -> int:
    """Build a tiny .bin, round-trip through mtl_dis.parse, verify shape.

    Equivalent (conceptually) to:
        var g=0;;
        var s="hello";;
        fun foo=42;;            // returns 42
        fun bar=foo+1;;         // calls foo, adds 1
        fun main=bar;;          // main returns bar()

    NOTE: this exercises the FORMAT encoder, not the (unwritten) parser/
    codegen. It proves that what we emit is structurally valid for the
    VM's loader, by feeding the result to our own disassembler + checker.
    """
    enc = Encoder()
    enc.add_global(0)
    enc.add_global("hello")
    enc.add_global(("nested", 7, NIL))
    enc.add_global(("\xff\xff\xff\xff".encode("latin-1")))

    # fun#0 main = bar (calls fun#2)
    main_ = enc.new_function(nargs=0, nlocals=0)
    main_.call_fun(2).ret()

    # fun#1 foo = 42
    foo = enc.new_function(nargs=0, nlocals=0)
    foo.intb(42).ret()

    # fun#2 bar = foo + 1  ->  push foo()'s result, push 1, add, ret
    bar = enc.new_function(nargs=0, nlocals=0)
    bar.call_fun(1)         # call foo -> stack has 42
    bar.intb(1)             # push 1
    bar.op(Op.OPadd)        # 42 + 1 -> 43
    bar.ret()

    blob = enc.emit()

    # Round-trip via mtl_dis as a subprocess (avoids importlib quirks on
    # newer Python — and exercises mtl_dis exactly as a user would).
    import subprocess
    import tempfile
    import json as _json

    here = Path(__file__).resolve().parent
    mtl_dis_path = here / "mtl_dis.py"
    if not mtl_dis_path.is_file():
        print(f"FAIL: missing {mtl_dis_path}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(blob)
        tmp = Path(f.name)
    try:
        # --check first: structural sanity.
        check = subprocess.run(
            [sys.executable, str(mtl_dis_path), str(tmp), "--check"],
            capture_output=True, text=True,
        )
        if check.returncode != 0:
            print("FAIL — mtl_dis --check rejected the blob:", file=sys.stderr)
            print(check.stdout, file=sys.stderr)
            print(check.stderr, file=sys.stderr)
            return 1
        # --json: confirm fun count + globals count.
        js = subprocess.run(
            [sys.executable, str(mtl_dis_path), str(tmp), "--json"],
            capture_output=True, text=True, check=True,
        )
        data = _json.loads(js.stdout)
        print(f"emitted {len(blob)} bytes:")
        print(f"  globals : {len(enc._globals)} -> disasm sees {len(data['globals'])}")
        print(f"  code    : {data['code_size']} bytes")
        print(f"  funtable: {data['nbfun']} funs")
        if len(data["globals"]) != len(enc._globals):
            print(
                f"FAIL — globals count mismatch "
                f"({len(data['globals'])} != {len(enc._globals)})",
                file=sys.stderr,
            )
            return 1
        if data["nbfun"] != len(enc._functions):
            print(
                f"FAIL — function count mismatch "
                f"({data['nbfun']} != {len(enc._functions)})",
                file=sys.stderr,
            )
            return 1
        print("OK — round-trips cleanly through mtl_dis --check + --json.")
        return 0
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--self-test", action="store_true",
                    help="build a tiny program in-memory and round-trip "
                    "through mtl_dis to verify the encoder is sane.")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    print("nothing to do — pass --self-test or import as a library.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
