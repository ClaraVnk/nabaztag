#!/usr/bin/env python3
"""Metal (VLISP) bytecode disassembler.

Reads a `.bin` produced by `mtl_compiler` (RedoXyde/mtl_linux) — the
exact same blob the on-device VM loads via `loaderInit()` — and prints
a human-readable disassembly.

Format references (lifted from the canonical implementation):
  - File layout      : nabgcc/src/vm/vloader.c::loaderInit / loaderSizeBC
  - Globals encoding : vloader.c::loaderInitRec
  - Instruction set  : nabgcc/inc/vm/vbc.h + nabgcc/src/vm/vinterp.c
  - Stdlib table     : mtl_linux/src/vcomp/stdlib_core.cpp

Output:
  - File header (globals size, code size, function count)
  - Globals (decoded recursively as nested lists/strings/ints)
  - Each function (nargs, nlocals, byte range, disassembly)
  - Cross-references between OPint <idx> + OPexec pairs (function calls)

Usage:
    python3 mtl_dis.py BYTECODE.bin
    python3 mtl_dis.py BYTECODE.bin --json       # machine-readable
    python3 mtl_dis.py BYTECODE.bin --no-globals # skip globals dump

Stand-alone — stdlib only. Drop anywhere.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Opcode table — verbatim from inc/vm/vbc.h.
# ---------------------------------------------------------------------------
OPCODES: dict[int, str] = {
    0: "OPexec", 1: "OPret", 2: "OPintb", 3: "OPint", 4: "OPnil",
    5: "OPdrop", 6: "OPdup", 7: "OPgetlocalb", 8: "OPgetlocal", 9: "OPadd",
    10: "OPsub", 11: "OPmul", 12: "OPdiv", 13: "OPmod", 14: "OPand",
    15: "OPor", 16: "OPeor", 17: "OPshl", 18: "OPshr", 19: "OPneg",
    20: "OPnot", 21: "OPnon", 22: "OPeq", 23: "OPne", 24: "OPlt",
    25: "OPgt", 26: "OPle", 27: "OPge", 28: "OPgoto", 29: "OPelse",
    30: "OPmktabb", 31: "OPmktab", 32: "OPdeftabb", 33: "OPdeftab",
    34: "OPfetchb", 35: "OPfetch", 36: "OPgetglobalb", 37: "OPgetglobal",
    38: "OPSecho", 39: "OPIecho", 40: "OPsetlocalb", 41: "OPsetlocal",
    42: "OPsetglobal", 43: "OPsetstructb", 44: "OPsetstruct", 45: "OPhd",
    46: "OPtl", 47: "OPsetlocal2", 48: "OPstore", 49: "OPcall",
    50: "OPcallrb", 51: "OPcallr", 52: "OPfirst", 53: "OPtime_ms",
    54: "OPtabnew", 55: "OPfixarg", 56: "OPabs", 57: "OPmax", 58: "OPmin",
    59: "OPrand", 60: "OPsrand", 61: "OPtime", 62: "OPstrnew",
    63: "OPstrset", 64: "OPstrcpy", 65: "OPvstrcmp", 66: "OPstrfind",
    67: "OPstrfindrev", 68: "OPstrlen", 69: "OPstrget", 70: "OPstrsub",
    71: "OPstrcat", 72: "OPtablen", 73: "OPstrcatlist", 74: "OPled",
    75: "OPmotorset", 76: "OPmotorget", 77: "OPbutton2", 78: "OPbutton3",
    79: "OPplayStart", 80: "OPplayFeed", 81: "OPplayStop", 82: "OPload",
    83: "OPudpStart", 84: "OPudpCb", 85: "OPudpStop", 86: "OPudpSend",
    87: "OPgc", 88: "OPtcpOpen", 89: "OPtcpClose", 90: "OPtcpSend",
    91: "OPtcpCb", 92: "OPsave", 93: "OPbytecode", 94: "OPloopcb",
    95: "OPIecholn", 96: "OPSecholn", 97: "OPtcpListen", 98: "OPenvget",
    99: "OPenvset", 100: "OPsndVol", 101: "OPrfidGet", 102: "OPplayTime",
    103: "OPnetCb", 104: "OPnetSend", 105: "OPnetState", 106: "OPnetMac",
    107: "OPnetChk", 108: "OPnetSetmode", 109: "OPnetScan", 110: "OPnetAuth",
    111: "OPrecStart", 112: "OPrecStop", 113: "OPrecVol", 114: "OPnetSeqAdd",
    115: "OPstrgetword", 116: "OPstrputword", 117: "OPatoi", 118: "OPhtoi",
    119: "OPitoa", 120: "OPctoa", 121: "OPitoh", 122: "OPctoh",
    123: "OPitobin2", 124: "OPlistswitch", 125: "OPlistswitchstr",
    126: "OPsndRefresh", 127: "OPsndWrite", 128: "OPsndRead", 129: "OPsndFeed",
    130: "OPsndAmpli", 131: "OPcorePP", 132: "OPcorePush", 133: "OPcorePull",
    134: "OPcoreBit0", 135: "OPtcpEnable", 136: "OPreboot", 137: "OPstrcmp",
    138: "OPadp2wav", 139: "OPwav2adp", 140: "OPalaw2wav", 141: "OPwav2alaw",
    142: "OPnetPmk", 143: "OPflashFirmware", 144: "OPcrypt", 145: "OPuncrypt",
    146: "OPnetRssi", 147: "OPrfidGetList", 148: "OPrfidRead", 149: "OPrfidWrite",
    150: "OPi2cRead", 151: "OPi2cWrite", 152: "OPverifySig",
    # OPstrright also = 152 in upstream vbc.h — collision; OPverifySig wins
    # since it's the live VM case in our patched vinterp.c. The compiler-side
    # `strright/strcrypt8/loadf/savef` builtins all share opcode 152.
}


# Operand decoders. Each entry is (size_in_bytes, formatter).
# `formatter(code, pc, pcbase) -> (operand_text, advance_pc)`.
def _opb(code: bytes, pc: int, _pcbase: int) -> tuple[str, int]:
    """One-byte unsigned operand."""
    v = code[pc] & 0xFF
    return f"{v}", pc + 1


def _opi(code: bytes, pc: int, _pcbase: int) -> tuple[str, int]:
    """Four-byte signed int operand (little-endian, encoded as two shorts)."""
    v = struct.unpack_from("<i", code, pc)[0]
    return f"{v}", pc + 4


def _opjmp(code: bytes, pc: int, pcbase: int) -> tuple[str, int]:
    """Two-byte signed jump offset, relative to the function's pcbase."""
    off = struct.unpack_from("<H", code, pc)[0]
    target = pcbase + off
    return f"-> 0x{target:04X}", pc + 2


# Map of opcode -> operand decoder. Unmapped = no operand.
OPERAND_DECODERS: dict[int, Any] = {
    2:  _opb,    # OPintb       u8
    3:  _opi,    # OPint        i32
    7:  _opb,    # OPgetlocalb  local idx
    28: _opjmp,  # OPgoto       jump
    29: _opjmp,  # OPelse       conditional jump
    30: _opb,    # OPmktabb     size
    32: _opb,    # OPdeftabb    size
    34: _opb,    # OPfetchb     struct idx
    36: _opb,    # OPgetglobalb global idx
    40: _opb,    # OPsetlocalb  local idx
    43: _opb,    # OPsetstructb struct idx
    50: _opb,    # OPcallrb     n args
}


# Builtin opcode → human-friendly name (lifted from stdlib_core.cpp).
# Only those that the compiler emits as direct opcodes. Used for inline
# annotation of e.g. OPSecho -> "; Secho".
BUILTIN_NAMES: dict[int, str] = {
    38: "Secho", 39: "Iecho", 45: "hd", 46: "tl",
    53: "time_ms", 54: "tabnew", 56: "abs", 57: "max", 58: "min",
    59: "rand", 60: "srand", 61: "time",
    62: "strnew", 63: "strset", 64: "strcpy", 65: "vstrcmp",
    66: "strfind", 67: "strfindrev", 68: "strlen", 69: "strget",
    70: "strsub", 71: "strcat", 72: "tablen", 73: "strcatlist",
    74: "led", 75: "motorset", 76: "motorget",
    77: "button2", 78: "button3",
    79: "playStart", 80: "playFeed", 81: "playStop",
    82: "load", 83: "udpStart", 84: "udpCb", 85: "udpStop", 86: "udpSend",
    87: "gc", 88: "tcpOpen", 89: "tcpClose", 90: "tcpSend", 91: "tcpCb",
    92: "save", 93: "bytecode", 94: "loopcb",
    95: "Iecholn", 96: "Secholn", 97: "tcpListen",
    98: "envget", 99: "envset", 100: "sndVol",
    101: "rfidGet", 102: "playTime",
    103: "netCb", 104: "netSend", 105: "netState", 106: "netMac",
    107: "netChk", 108: "netSetmode", 109: "netScan", 110: "netAuth",
    111: "recStart", 112: "recStop", 113: "recVol",
    114: "netSeqAdd",
    115: "strgetword", 116: "strputword",
    117: "atoi", 118: "htoi", 119: "itoa", 120: "ctoa",
    121: "itoh", 122: "ctoh", 123: "itobin2",
    124: "listswitch", 125: "listswitchstr",
    126: "sndRefresh", 127: "sndWrite", 128: "sndRead",
    129: "sndFeed", 130: "sndAmpli",
    131: "corePP", 132: "corePush", 133: "corePull", 134: "coreBit0",
    135: "tcpEnable", 136: "reboot", 137: "strcmp",
    138: "adp2wav", 139: "wav2adp", 140: "alaw2wav", 141: "wav2alaw",
    142: "netPmk", 143: "flashFirmware",
    144: "crypt", 145: "uncrypt",
    146: "netRssi", 147: "rfidGetList", 148: "rfidRead", 149: "rfidWrite",
    150: "i2cRead", 151: "i2cWrite", 152: "verifySig",
    55: "fixarg",
}


# ---------------------------------------------------------------------------
# File parser
# ---------------------------------------------------------------------------

NIL = ("nil",)  # sentinel for nil/empty


def _i32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def _i16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<h", buf, off)[0]


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _read_global_rec(buf: bytes, off: int) -> tuple[Any, int]:
    """Recursive globals decoder, mirrors vloader.c::loaderInitRec.

    The first int32 is a tag word with bit-packed semantics:
        l == -1         → nil
        l & 1 == 1
            l>>1 & 1 == 1 → tuple, count = l>>2, then count recursive items
            l>>1 & 1 == 0 → string, length = l>>2, then `length` raw bytes
        l & 1 == 0       → int, value = l (signed, untagged)
    """
    l = _i32(buf, off)
    off += 4
    if l == -1:
        return NIL, off
    if l & 1:
        if (l >> 1) & 1:
            count = l >> 2
            items = []
            for _ in range(count):
                v, off = _read_global_rec(buf, off)
                items.append(v)
            return tuple(items), off
        else:
            length = l >> 2
            s = bytes(buf[off:off + length])
            off += length
            return s, off
    else:
        # raw int — VLISP tags integers in the upper bits at runtime,
        # the on-disk value is already the tagged form.
        return l, off


def _format_value(v: Any, max_len: int = 80) -> str:
    """Pretty-print a global value for display."""
    if v is NIL:
        return "nil"
    if isinstance(v, int):
        # VLISP tagged int: shift right by 1 to get the actual value
        # (only matters for display — bit 0 = type tag).
        return f"{v >> 1}"
    if isinstance(v, bytes):
        # Try utf-8 / ascii first, fall back to byte literal.
        try:
            s = v.decode("ascii")
            printable = all(32 <= ord(c) < 127 or c in "\t\n\r" for c in s)
            if printable:
                return f'"{s}"'
        except UnicodeDecodeError:
            pass
        if len(v) <= 12:
            return "b\"" + "".join(f"\\x{b:02x}" for b in v) + "\""
        return f"<{len(v)} bytes>"
    if isinstance(v, tuple):
        parts = [_format_value(x, max_len // max(1, len(v))) for x in v]
        full = "[" + ", ".join(parts) + "]"
        if len(full) <= max_len:
            return full
        return f"<tuple len={len(v)}>"
    return repr(v)


@dataclass
class Function:
    index: int
    nargs: int
    nlocals: int
    pc_start: int   # offset into code section
    pc_end: int     # exclusive
    insns: list = field(default_factory=list)  # [(pc, op, mnemonic, operand_text)]


@dataclass
class Bytecode:
    raw: bytes
    globals_size: int
    code_offset: int      # absolute offset of code section in file
    code_size: int
    code: bytes
    nbfun: int
    funtable: list[int]
    globals: list[Any]
    functions: list[Function]


def _strip_amber_wrapper(raw: bytes) -> bytes:
    """The HTTP wire format the rabbit fetches from /vl/bc.jsp wraps the
    raw mtl_compiler output as `"amber" + size_hex(8) + body + "Mind"`.
    boot.mtl::getbytecode reads `strsub content 13 len`. If we detect that
    wrapper, peel it; otherwise return as-is.
    """
    if len(raw) >= 17 and raw[:5] == b"amber":
        try:
            size = int(raw[5:13].decode("ascii"), 16)
        except ValueError:
            return raw
        if 13 + size + 4 <= len(raw) and raw[13 + size:13 + size + 4] == b"Mind":
            return raw[13:13 + size]
    return raw


def parse(path: Path) -> Bytecode:
    raw_full = path.read_bytes()
    raw = _strip_amber_wrapper(raw_full)
    # Section 1 — globals: [size:i32] [data:size-4]
    globals_size = _i32(raw, 0)
    if globals_size < 4 or globals_size > len(raw):
        raise ValueError(f"globals_size out of range: {globals_size}")
    globals_data_end = globals_size  # globals_size INCLUDES the 4-byte header per loaderInit's bookkeeping

    # Section 2 — code: [size:i32] [bytes:size]
    code_size = _i32(raw, globals_data_end)
    code_offset = globals_data_end + 4
    code_end = code_offset + code_size
    if code_end > len(raw):
        raise ValueError(f"code section overruns file ({code_end} > {len(raw)})")
    code = bytes(raw[code_offset:code_end])

    # Section 3 — funtable: [nbfun:i16] [entry:i32 * nbfun]
    nbfun = _u16(raw, code_end)
    funtable_end = code_end + 2 + nbfun * 4
    if funtable_end > len(raw):
        raise ValueError(f"funtable overruns file ({funtable_end} > {len(raw)})")
    funtable = [
        _i32(raw, code_end + 2 + i * 4)
        for i in range(nbfun)
    ]

    # Decode globals.
    g: list[Any] = []
    off = 4  # skip the 4-byte size header
    while off < globals_data_end:
        v, off = _read_global_rec(raw, off)
        g.append(v)

    # Each function: [nargs:u8] [nlocals:u16] then bytecode until next function start.
    sorted_starts = sorted(funtable)
    fn_end_by_start = {}
    for i, s in enumerate(sorted_starts):
        nxt = sorted_starts[i + 1] if i + 1 < len(sorted_starts) else code_size
        fn_end_by_start[s] = nxt

    functions: list[Function] = []
    for fn_idx, fn_start in enumerate(funtable):
        nargs = code[fn_start] & 0xFF
        nlocals = _u16(code, fn_start + 1)
        body_start = fn_start + 3
        body_end = fn_end_by_start[fn_start]
        functions.append(Function(
            index=fn_idx,
            nargs=nargs,
            nlocals=nlocals,
            pc_start=fn_start,
            pc_end=body_end,
            insns=_disassemble_body(code, body_start, body_end),
        ))

    return Bytecode(
        raw=raw,
        globals_size=globals_size,
        code_offset=code_offset,
        code_size=code_size,
        code=code,
        nbfun=nbfun,
        funtable=funtable,
        globals=g,
        functions=functions,
    )


def _disassemble_body(code: bytes, pc: int, end: int) -> list:
    """Walk one function's body, decode each instruction.

    The "pcbase" for relative jumps is the START of the function body
    (right after the [nargs:u8 nlocals:u16] header). This matches
    vinterp.c where `pcbase=pc=npc` after `npc+=3`.

    Returns a list of (pc, opcode, mnemonic, operand_text_or_None).
    """
    insns = []
    pcbase = pc
    while pc < end:
        start = pc
        op = code[pc]
        mnem = OPCODES.get(op, f"OP_UNKNOWN_{op}")
        pc += 1
        decoder = OPERAND_DECODERS.get(op)
        if decoder is not None:
            try:
                operand, pc = decoder(code, pc, pcbase)
            except struct.error:
                operand = "<truncated>"
                pc = end
        else:
            operand = None
        insns.append((start, op, mnem, operand))
    return insns


# ---------------------------------------------------------------------------
# Annotations: look at OPint <idx>; OPexec sequences -> "call fun#<idx>"
# ---------------------------------------------------------------------------
def annotate_calls(functions: list[Function]) -> dict[tuple[int, int], str]:
    """Return {(fn_index, pc): annotation} for OPexec sites whose preceding
    instruction was OPint or OPintb pushing a function index.
    """
    notes: dict[tuple[int, int], str] = {}
    for fn in functions:
        for i, (pc, op, _, _) in enumerate(fn.insns):
            if op == 0:  # OPexec
                if i > 0:
                    prev_pc, prev_op, _, prev_operand = fn.insns[i - 1]
                    if prev_op in (2, 3) and prev_operand:  # OPintb or OPint
                        try:
                            idx = int(prev_operand)
                            notes[(fn.index, pc)] = f"; → call fun#{idx}"
                        except ValueError:
                            pass
            elif op in BUILTIN_NAMES:
                notes[(fn.index, pc)] = f"; {BUILTIN_NAMES[op]}"
    return notes


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------
def render_text(bc: Bytecode, show_globals: bool = True) -> str:
    out: list[str] = []
    out.append(f"=== bytecode summary ===")
    out.append(f"  file size       : {len(bc.raw)} bytes")
    out.append(f"  globals section : {bc.globals_size} bytes ({len(bc.globals)} entries)")
    out.append(f"  code section    : {bc.code_size} bytes @ file offset 0x{bc.code_offset:X}")
    out.append(f"  functions       : {bc.nbfun}")
    out.append("")

    if show_globals:
        out.append(f"=== globals ({len(bc.globals)} entries) ===")
        for i, g in enumerate(bc.globals):
            out.append(f"  [{i:3d}] {_format_value(g)}")
        out.append("")

    notes = annotate_calls(bc.functions)

    out.append(f"=== code ({bc.nbfun} functions) ===")
    for fn in bc.functions:
        header_pc = fn.pc_start
        body_pc = fn.pc_start + 3
        size = fn.pc_end - fn.pc_start
        out.append("")
        out.append(
            f"--- fun#{fn.index}  nargs={fn.nargs} nlocals={fn.nlocals}  "
            f"@ 0x{header_pc:04X}..0x{fn.pc_end:04X} ({size} bytes) ---"
        )
        for pc, op, mnem, operand in fn.insns:
            note = notes.get((fn.index, pc), "")
            op_str = operand if operand else ""
            out.append(f"  0x{pc:04X}  {mnem:<14} {op_str:<14}  {note}")
    out.append("")
    return "\n".join(out)


def render_json(bc: Bytecode) -> str:
    def _g(v):
        if v is NIL:
            return None
        if isinstance(v, bytes):
            try:
                return v.decode("ascii")
            except UnicodeDecodeError:
                return {"_bytes_hex": v.hex()}
        if isinstance(v, tuple):
            return [_g(x) for x in v]
        if isinstance(v, int):
            return v
        return repr(v)

    return json.dumps(
        {
            "file_size": len(bc.raw),
            "globals_size": bc.globals_size,
            "code_offset": bc.code_offset,
            "code_size": bc.code_size,
            "nbfun": bc.nbfun,
            "globals": [_g(g) for g in bc.globals],
            "functions": [
                {
                    "index": f.index,
                    "nargs": f.nargs,
                    "nlocals": f.nlocals,
                    "pc_start": f.pc_start,
                    "pc_end": f.pc_end,
                    "instructions": [
                        {"pc": pc, "op": op, "mnem": mnem, "operand": operand}
                        for pc, op, mnem, operand in f.insns
                    ],
                }
                for f in bc.functions
            ],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("path", help="path to a Metal .bin (mtl_compiler output)")
    ap.add_argument("--json", action="store_true", help="emit JSON, not text")
    ap.add_argument("--no-globals", action="store_true",
                    help="skip globals dump (text only)")
    args = ap.parse_args()
    bc = parse(Path(args.path))
    if args.json:
        sys.stdout.write(render_json(bc))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(bc, show_globals=not args.no_globals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
