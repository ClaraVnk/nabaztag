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
import re as _re
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
# Text assembly parser — small line-oriented format.
# ---------------------------------------------------------------------------
#
#   ; comments are //, #, or ;
#   .global int N                ; or:  .global nil
#   .global string "..."         ; or:  .global bytes 0xff 0xff 0x00
#   .global tuple
#     int 1
#     string "foo"
#   .end                         ; closes tuple
#
#   .fun NAME nargs nlocals
#       OPintb 42
#       OPelse else_branch
#       OPintb 1
#       OPgoto end
#     else_branch:
#       OPintb 99
#     end:
#       OPret
#   .end
#
# Opcodes use their full mnemonic ("OPintb"), the operand is an integer
# literal except for jumps which take a label name. Labels are `name:`
# at the start of a line (optionally indented). Function names in .fun
# are informational only — the funtable index is the order of .fun
# declarations in the source.
#
# This isn't .mtl — it's the bytecode IR exposed as text. A future full
# .mtl parser would lower to this representation.

_MNEM_TO_OP = {name: code for code, name in
               # importing the disassembler table would be cleaner; copy here
               # to keep mtl_asm.py standalone.
               {
                   0: "OPexec", 1: "OPret", 2: "OPintb", 3: "OPint", 4: "OPnil",
                   5: "OPdrop", 6: "OPdup", 7: "OPgetlocalb", 8: "OPgetlocal",
                   9: "OPadd", 10: "OPsub", 11: "OPmul", 12: "OPdiv", 13: "OPmod",
                   14: "OPand", 15: "OPor", 16: "OPeor", 17: "OPshl", 18: "OPshr",
                   19: "OPneg", 20: "OPnot", 21: "OPnon", 22: "OPeq", 23: "OPne",
                   24: "OPlt", 25: "OPgt", 26: "OPle", 27: "OPge",
                   28: "OPgoto", 29: "OPelse",
                   30: "OPmktabb", 31: "OPmktab", 32: "OPdeftabb", 33: "OPdeftab",
                   34: "OPfetchb", 35: "OPfetch",
                   36: "OPgetglobalb", 37: "OPgetglobal",
                   38: "OPSecho", 39: "OPIecho",
                   40: "OPsetlocalb", 41: "OPsetlocal", 42: "OPsetglobal",
                   43: "OPsetstructb", 44: "OPsetstruct",
                   45: "OPhd", 46: "OPtl", 47: "OPsetlocal2", 48: "OPstore",
                   49: "OPcall", 50: "OPcallrb", 51: "OPcallr", 52: "OPfirst",
                   53: "OPtime_ms", 54: "OPtabnew", 55: "OPfixarg",
                   56: "OPabs", 57: "OPmax", 58: "OPmin",
                   59: "OPrand", 60: "OPsrand", 61: "OPtime",
                   62: "OPstrnew", 63: "OPstrset", 64: "OPstrcpy", 65: "OPvstrcmp",
                   66: "OPstrfind", 67: "OPstrfindrev", 68: "OPstrlen",
                   69: "OPstrget", 70: "OPstrsub", 71: "OPstrcat",
                   72: "OPtablen", 73: "OPstrcatlist",
                   74: "OPled", 75: "OPmotorset", 76: "OPmotorget",
                   77: "OPbutton2", 78: "OPbutton3",
                   79: "OPplayStart", 80: "OPplayFeed", 81: "OPplayStop",
                   82: "OPload",
                   83: "OPudpStart", 84: "OPudpCb", 85: "OPudpStop", 86: "OPudpSend",
                   87: "OPgc",
                   88: "OPtcpOpen", 89: "OPtcpClose", 90: "OPtcpSend",
                   91: "OPtcpCb", 92: "OPsave", 93: "OPbytecode", 94: "OPloopcb",
                   95: "OPIecholn", 96: "OPSecholn",
                   97: "OPtcpListen", 98: "OPenvget", 99: "OPenvset",
                   100: "OPsndVol", 101: "OPrfidGet", 102: "OPplayTime",
                   103: "OPnetCb", 104: "OPnetSend", 105: "OPnetState",
                   106: "OPnetMac", 107: "OPnetChk", 108: "OPnetSetmode",
                   109: "OPnetScan", 110: "OPnetAuth",
                   111: "OPrecStart", 112: "OPrecStop", 113: "OPrecVol",
                   114: "OPnetSeqAdd",
                   115: "OPstrgetword", 116: "OPstrputword",
                   117: "OPatoi", 118: "OPhtoi", 119: "OPitoa", 120: "OPctoa",
                   121: "OPitoh", 122: "OPctoh", 123: "OPitobin2",
                   124: "OPlistswitch", 125: "OPlistswitchstr",
                   126: "OPsndRefresh", 127: "OPsndWrite", 128: "OPsndRead",
                   129: "OPsndFeed", 130: "OPsndAmpli",
                   131: "OPcorePP", 132: "OPcorePush", 133: "OPcorePull",
                   134: "OPcoreBit0", 135: "OPtcpEnable", 136: "OPreboot",
                   137: "OPstrcmp", 138: "OPadp2wav", 139: "OPwav2adp",
                   140: "OPalaw2wav", 141: "OPwav2alaw",
                   142: "OPnetPmk", 143: "OPflashFirmware",
                   144: "OPcrypt", 145: "OPuncrypt",
                   146: "OPnetRssi", 147: "OPrfidGetList",
                   148: "OPrfidRead", 149: "OPrfidWrite",
                   150: "OPi2cRead", 151: "OPi2cWrite", 152: "OPverifySig",
               }.items()}


def _parse_int_literal(token: str) -> int:
    token = token.strip()
    if token.lower().startswith("0x"):
        return int(token, 16)
    if token.startswith("'") and token.endswith("'") and len(token) >= 3:
        # Character literal like 'A' — handle simple cases only.
        inner = token[1:-1]
        if len(inner) == 1:
            return ord(inner)
        if inner == r"\n":
            return 10
        if inner == r"\t":
            return 9
        if inner == r"\\":
            return ord("\\")
    return int(token, 10)


def _parse_string_literal(token: str) -> bytes:
    """Parse `"..."` (with backslash-escapes) into raw bytes."""
    if not (token.startswith('"') and token.endswith('"')):
        raise ValueError(f"not a string literal: {token!r}")
    out = bytearray()
    i = 1
    while i < len(token) - 1:
        c = token[i]
        if c == "\\" and i + 1 < len(token) - 1:
            nxt = token[i + 1]
            if nxt == "n":   out.append(10); i += 2; continue
            if nxt == "t":   out.append(9);  i += 2; continue
            if nxt == "r":   out.append(13); i += 2; continue
            if nxt == "\\":  out.append(ord("\\")); i += 2; continue
            if nxt == '"':   out.append(ord('"')); i += 2; continue
            if nxt == "x" and i + 3 < len(token) - 1:
                out.append(int(token[i + 2:i + 4], 16)); i += 4; continue
        out.append(ord(c))
        i += 1
    return bytes(out)


_STRING_RE = _re.compile(r'"(?:\\.|[^"\\])*"')


def _tokenize_line(line: str) -> list[str]:
    """Split a line into tokens. Respects double-quoted strings."""
    tokens: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            i += 1
            continue
        if c in "#;" or line[i:i + 2] == "//":
            break  # rest of line is a comment
        if c == '"':
            m = _STRING_RE.match(line, i)
            if not m:
                raise ValueError(f"unterminated string starting at col {i}")
            tokens.append(m.group(0))
            i = m.end()
            continue
        # Plain token: read until whitespace/comment.
        j = i
        while j < n and line[j] not in " \t#;" and line[j:j + 2] != "//":
            j += 1
        tokens.append(line[i:j])
        i = j
    return tokens


def parse_text(source: str) -> Encoder:
    """Parse the .masm text format and return a populated Encoder."""
    enc = Encoder()
    lines = source.splitlines()

    state = "top"          # top / .fun / .global tuple
    current_fn: FunctionBody | None = None
    tuple_stack: list[list[Any]] = []  # nested tuple builders
    function_index_counter = 0

    def _build_tuple_top() -> tuple:
        return tuple(tuple_stack[-1])

    for lineno, raw_line in enumerate(lines, start=1):
        tokens = _tokenize_line(raw_line)
        if not tokens:
            continue
        try:
            head = tokens[0]
            # Labels: token ending in ':' (and only one token).
            if head.endswith(":") and len(tokens) == 1 and state == ".fun":
                current_fn.label(head[:-1])  # type: ignore[union-attr]
                continue

            if head == ".fun":
                if state == ".fun":
                    raise SyntaxError("nested .fun is not supported")
                if state == ".global-tuple":
                    raise SyntaxError(".fun inside a tuple is not supported")
                # .fun NAME nargs nlocals (name is informational)
                if len(tokens) < 4:
                    raise SyntaxError(".fun needs NAME nargs nlocals")
                nargs = _parse_int_literal(tokens[2])
                nlocals = _parse_int_literal(tokens[3])
                current_fn = enc.new_function(nargs=nargs, nlocals=nlocals)
                function_index_counter += 1
                state = ".fun"
                continue

            if head == ".global":
                if state == ".fun":
                    raise SyntaxError(".global after .fun isn't allowed — "
                                      "declare all globals first")
                if len(tokens) < 2:
                    raise SyntaxError(".global needs a type")
                kind = tokens[1]
                if kind == "nil":
                    if state == ".global-tuple":
                        tuple_stack[-1].append(NIL)
                    else:
                        enc.add_global(NIL)
                elif kind == "int":
                    if len(tokens) < 3:
                        raise SyntaxError(".global int needs a value")
                    val = _parse_int_literal(tokens[2])
                    if state == ".global-tuple":
                        tuple_stack[-1].append(val)
                    else:
                        enc.add_global(val)
                elif kind == "string":
                    if len(tokens) < 3:
                        raise SyntaxError(".global string needs a value")
                    val = _parse_string_literal(tokens[2])
                    if state == ".global-tuple":
                        tuple_stack[-1].append(val)
                    else:
                        enc.add_global(val)
                elif kind == "bytes":
                    raw = bytes(_parse_int_literal(t) for t in tokens[2:])
                    if state == ".global-tuple":
                        tuple_stack[-1].append(raw)
                    else:
                        enc.add_global(raw)
                elif kind == "tuple":
                    tuple_stack.append([])
                    state = ".global-tuple"
                else:
                    raise SyntaxError(f"unknown .global type: {kind}")
                continue

            if head == ".end":
                if state == ".fun":
                    current_fn = None
                    state = "top"
                elif state == ".global-tuple":
                    finished = tuple(tuple_stack.pop())
                    if tuple_stack:
                        # nested tuple — append to parent
                        tuple_stack[-1].append(finished)
                    else:
                        enc.add_global(finished)
                        state = "top"
                else:
                    raise SyntaxError(".end with nothing to close")
                continue

            # In a function body — must be an opcode.
            if state == ".fun":
                mnem = head
                if mnem not in _MNEM_TO_OP:
                    raise SyntaxError(f"unknown opcode: {mnem}")
                opcode = _MNEM_TO_OP[mnem]
                if opcode in _OP_JMP:
                    if len(tokens) < 2:
                        raise SyntaxError(f"{mnem} needs a label name")
                    current_fn.opjmp(opcode, tokens[1])  # type: ignore[union-attr]
                elif opcode in _OP_I32:
                    if len(tokens) < 2:
                        raise SyntaxError(f"{mnem} needs an i32 operand")
                    current_fn.opi(opcode, _parse_int_literal(tokens[1]))  # type: ignore[union-attr]
                elif opcode in _OP_U8:
                    if len(tokens) < 2:
                        raise SyntaxError(f"{mnem} needs a u8 operand")
                    current_fn.opb(opcode, _parse_int_literal(tokens[1]))  # type: ignore[union-attr]
                else:
                    current_fn.op(opcode)  # type: ignore[union-attr]
                continue

            raise SyntaxError(f"unexpected token at top-level: {head!r}")
        except (SyntaxError, ValueError) as e:
            raise SyntaxError(f"line {lineno}: {e}") from None

    if state != "top":
        raise SyntaxError(f"unclosed {state!r} at end of input")
    return enc


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
    ap.add_argument("input", nargs="?",
                    help="path to a .masm text source to assemble (omit to "
                    "use --self-test).")
    ap.add_argument("-o", "--output", help="output .bin path (default: "
                    "INPUT with .masm replaced by .bin)")
    ap.add_argument("--self-test", action="store_true",
                    help="build a tiny program in-memory and round-trip "
                    "through mtl_dis to verify the encoder is sane.")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.input:
        print("usage: mtl_asm.py [INPUT.masm] [-o OUTPUT.bin] | --self-test",
              file=sys.stderr)
        return 2
    src_path = Path(args.input)
    out_path = Path(args.output) if args.output else src_path.with_suffix(".bin")
    enc = parse_text(src_path.read_text())
    blob = enc.emit()
    out_path.write_bytes(blob)
    print(f"wrote {len(blob)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
