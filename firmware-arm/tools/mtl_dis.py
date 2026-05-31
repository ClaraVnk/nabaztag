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
    name: str | None = None  # resolved from .mtl source if --src supplied


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
# Name resolution: parse a .mtl source for top-level `fun NAME` declarations.
# ---------------------------------------------------------------------------
import re as _re

_MTL_COMMENT = _re.compile(r"//[^\n]*|/\*.*?\*/", _re.DOTALL)
_MTL_STRING  = _re.compile(r'"(?:\\.|[^"\\])*"', _re.DOTALL)


def _strip_mtl_lexical(src: str) -> str:
    """Strip comments + string literals from .mtl source so a top-level
    keyword scan doesn't trip on `// fun foo`-in-a-comment or `"fun"`.
    Replaces stripped regions with spaces to preserve byte offsets (so
    line numbers stay correct for any subsequent error report).
    """
    def _blank(m):
        return " " * (m.end() - m.start())
    # Strings first (they may contain // or /*), then comments.
    src = _MTL_STRING.sub(_blank, src)
    src = _MTL_COMMENT.sub(_blank, src)
    return src


_FUN_DECL   = _re.compile(r"(?m)^[ \t]*fun[ \t]+([A-Za-z_][A-Za-z_0-9]*)\b")
_PROTO_DECL = _re.compile(r"(?m)^[ \t]*proto[ \t]+([A-Za-z_][A-Za-z_0-9]*)\b")


def extract_fun_names(src: str) -> list[str]:
    """Walk the source, return a list of names in funtable-index order.

    Compiler semantics (cross-referenced against mtl_linux source):
      - `proto NAME ARITY;;` reserves a funtable slot at the *current*
        next-free index. So `proto main 0;;` at the top of boot.mtl
        burns index 0 for `main`.
      - `fun NAME args = body;;` either fills a proto-reserved slot
        (same NAME) or, if no matching proto, takes a new index.
      - `ifdef X { ... } else { ... }` — only the active branch's funs
        are emitted. We can't tell which branch is active without
        running preproc.pl. For best results, feed the PREPROCESSED
        source (the same blob mtl_compiler consumes); the linter will
        warn if the count is off.

    Strategy:
      1. Walk the source linearly, recording protos (reserves an index)
         and fun-defs (fills a proto slot OR takes a new index).
      2. Order: protos take their index at the moment they appear; fun
         definitions either complete the proto or get the next free
         index.
    """
    src = _strip_mtl_lexical(src)
    # We walk linearly, finding both protos and funs in source order.
    events = []  # list of (offset, kind, name)
    for m in _PROTO_DECL.finditer(src):
        events.append((m.start(), "proto", m.group(1)))
    for m in _FUN_DECL.finditer(src):
        events.append((m.start(), "fun", m.group(1)))
    events.sort()

    # Allocate indices in source order.
    name_by_idx: dict[int, str] = {}
    next_idx = 0
    proto_idx_by_name: dict[str, int] = {}
    for _off, kind, name in events:
        if kind == "proto":
            proto_idx_by_name[name] = next_idx
            name_by_idx[next_idx] = name
            next_idx += 1
        else:  # fun
            if name in proto_idx_by_name:
                # Already reserved — definition fills it.
                name_by_idx[proto_idx_by_name[name]] = name
            else:
                name_by_idx[next_idx] = name
                next_idx += 1
    if not name_by_idx:
        return []
    return [name_by_idx.get(i, f"_unknown_{i}") for i in range(max(name_by_idx) + 1)]


def attach_function_names(bc: Bytecode, src_path: Path) -> int:
    """Read .mtl source, parse top-level `fun NAME`s, attach to bc.functions
    by index. Returns the number of names matched.

    If the count mismatches nbfun by ±1 we still attach what we can, and
    print a warning to stderr. A larger mismatch usually means the source
    isn't the preprocessed form — flag it.
    """
    src = src_path.read_text()
    names = extract_fun_names(src)
    if not names:
        print(f"warn: no `fun NAME` declarations found in {src_path}", file=sys.stderr)
        return 0
    if abs(len(names) - bc.nbfun) > 5:
        print(
            f"warn: source has {len(names)} `fun` decls but bin has {bc.nbfun} "
            f"functions — likely not the preprocessed source. Annotation "
            f"is best-effort and may be off.",
            file=sys.stderr,
        )
    for fn in bc.functions:
        if fn.index < len(names):
            fn.name = names[fn.index]
    return min(len(names), bc.nbfun)


# ---------------------------------------------------------------------------
# Annotations: look at OPint <idx>; OPexec sequences -> "call fun#<idx>"
# ---------------------------------------------------------------------------
def annotate_calls(functions: list[Function]) -> dict[tuple[int, int], str]:
    """Return {(fn_index, pc): annotation} for OPexec sites whose preceding
    instruction was OPint or OPintb pushing a function index.

    When function names have been attached (--src), use them in the
    annotation: `; → MACecho` instead of `; → call fun#56`.
    """
    name_by_idx = {f.index: f.name for f in functions if f.name}
    notes: dict[tuple[int, int], str] = {}
    for fn in functions:
        for i, (pc, op, _, _) in enumerate(fn.insns):
            if op == 0:  # OPexec
                if i > 0:
                    prev_pc, prev_op, _, prev_operand = fn.insns[i - 1]
                    if prev_op in (2, 3) and prev_operand:  # OPintb or OPint
                        try:
                            idx = int(prev_operand)
                            name = name_by_idx.get(idx)
                            if name:
                                notes[(fn.index, pc)] = f"; → {name}"
                            else:
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
        name_part = f" {fn.name}" if fn.name else ""
        out.append("")
        out.append(
            f"--- fun#{fn.index}{name_part}  nargs={fn.nargs} nlocals={fn.nlocals}  "
            f"@ 0x{header_pc:04X}..0x{fn.pc_end:04X} ({size} bytes) ---"
        )
        for pc, op, mnem, operand in fn.insns:
            note = notes.get((fn.index, pc), "")
            op_str = operand if operand else ""
            out.append(f"  0x{pc:04X}  {mnem:<14} {op_str:<14}  {note}")
    out.append("")
    return "\n".join(out)


def render_masm(bc: Bytecode) -> str:
    """Emit a re-parseable .masm source: ASM text that mtl_asm.py can
    feed back to the encoder to reproduce the same .bin. Round-trips
    cleanly for the validated subset (no float globals).
    """
    out: list[str] = []
    out.append("// Auto-generated by mtl_dis.py --format masm")
    out.append("// Round-trip via:  mtl_asm.py THIS.masm  →  cmp original.bin")
    out.append("")

    # Globals — one per line.
    def _g_to_masm(v: Any) -> list[str]:
        if v is NIL:
            return [".global nil"]
        if isinstance(v, int):
            return [f".global int {v >> 1}"]  # un-tag the on-disk shift
        if isinstance(v, bytes):
            # Always emit as `bytes` literal — safe for any byte content
            # (including null bytes that string literal escaping would
            # need to handle specially). Hex-byte form is verbose but
            # round-trip-correct.
            hex_bytes = " ".join(f"0x{b:02x}" for b in v)
            return [f".global bytes {hex_bytes}" if hex_bytes else ".global string \"\""]
        if isinstance(v, tuple):
            lines = [".global tuple"]
            for item in v:
                inner = _g_to_masm(item)
                # Strip the `.global ` prefix from nested values — inside a
                # tuple, items are listed without `.global`. (mtl_asm.parse_text
                # currently DOES use `.global TYPE VAL` for tuple items, so
                # keep them as-is for now.)
                lines += ["    " + line for line in inner]
            lines.append(".end")
            return lines
        return [f"// UNSUPPORTED GLOBAL: {v!r}"]

    if bc.globals:
        out.append("// Globals (in load order — index = source order).")
        for g in bc.globals:
            out += _g_to_masm(g)
        out.append("")

    # Functions. For each, pre-collect jump targets to assign label names.
    for fn in bc.functions:
        targets: dict[int, str] = {}  # pc -> label name
        for pc, op, _mnem, operand in fn.insns:
            if op in (28, 29) and operand and operand.startswith("-> 0x"):
                try:
                    t = int(operand[5:], 16)
                except ValueError:
                    continue
                if t not in targets:
                    targets[t] = f"L_0x{t:04X}"

        body_start = fn.pc_start + 3
        label_part = fn.name or f"fn{fn.index}"
        out.append(f".fun {label_part} {fn.nargs} {fn.nlocals}")
        for pc, op, mnem, operand in fn.insns:
            if pc in targets:
                out.append(f"  {targets[pc]}:")
            line = f"    {mnem}"
            if op in (28, 29) and operand and operand.startswith("-> 0x"):
                try:
                    t = int(operand[5:], 16)
                    line += f" {targets[t]}"
                except ValueError:
                    line += f" {operand}"
            elif operand is not None:
                line += f" {operand}"
            out.append(line)
        # If a label points one past the last insn (jump to function exit),
        # emit it after the last instruction so back-patching has a target.
        last_end = fn.pc_end
        if last_end in targets:
            out.append(f"  {targets[last_end]}:")
        out.append(".end")
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
                    "name": f.name,
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
# Structural validation
# ---------------------------------------------------------------------------
def check_bytecode(bc: Bytecode) -> list[str]:
    """Walk the bytecode and return a list of problem strings. Empty list
    means the bin looks structurally sane (every opcode is known, every
    operand fits, every jump lands inside its function, every OPexec is
    preceded by a push of something fun-index-shaped).

    This is the kind of check that should run on every patched .mtl
    BEFORE we flash a rabbit. It can't catch semantic bugs (e.g. the
    page_a marker mismatch we shipped) but it catches the bytecode-level
    foot-guns the compiler would otherwise leave for runtime.
    """
    problems: list[str] = []

    for fn in bc.functions:
        body_start = fn.pc_start + 3
        body_end = fn.pc_end
        last_op = None
        last_operand = None
        for pc, op, mnem, operand in fn.insns:
            # 1. Unknown opcode.
            if op not in OPCODES:
                problems.append(
                    f"fun#{fn.index}@0x{pc:04X}: unknown opcode {op}"
                )
            # 2. OPgoto/OPelse target lands inside the function body.
            if op in (28, 29) and operand and operand.startswith("-> 0x"):
                try:
                    target = int(operand[5:], 16)
                except ValueError:
                    problems.append(
                        f"fun#{fn.index}@0x{pc:04X}: malformed jump target {operand!r}"
                    )
                    continue
                if not (body_start <= target < body_end):
                    problems.append(
                        f"fun#{fn.index} ({fn.name or '?'})@0x{pc:04X}: "
                        f"{mnem} target 0x{target:04X} is outside the function "
                        f"body [0x{body_start:04X}..0x{body_end:04X})"
                    )
            # 3. OPexec must be preceded by a push of a function index
            #    (OPint or OPintb, OR a stacked value from OPgetglobal etc).
            #    We can only check the IMMEDIATE precursor — anything stacked
            #    via earlier instructions is opaque without simulating.
            if op == 0:  # OPexec
                if last_op is None:
                    problems.append(
                        f"fun#{fn.index}@0x{pc:04X}: OPexec is the first "
                        f"instruction (no function index on the stack)"
                    )
                # OPint/OPintb (immediate push) — bounds-check the value.
                elif last_op in (2, 3) and last_operand:
                    try:
                        idx = int(last_operand)
                        if idx < 0 or idx >= bc.nbfun:
                            problems.append(
                                f"fun#{fn.index}@0x{pc:04X}: OPexec calls "
                                f"fun#{idx} which is out of range [0..{bc.nbfun})"
                            )
                    except ValueError:
                        pass
            last_op = op
            last_operand = operand

    # 4. Funtable entries land inside the code section.
    for i, fn_start in enumerate(bc.funtable):
        if fn_start < 0 or fn_start + 3 > bc.code_size:
            problems.append(
                f"funtable[{i}] = 0x{fn_start:04X} is out of the code "
                f"section [0..0x{bc.code_size:04X})"
            )

    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("path", help="path to a Metal .bin (mtl_compiler output)")
    ap.add_argument("--json", action="store_true", help="emit JSON, not text")
    ap.add_argument("--format", choices=("text", "masm", "json"), default="text",
                    help="output format. masm = mtl_asm-parseable assembly "
                    "(round-trips via mtl_asm.py). json is also reachable "
                    "via --json.")
    ap.add_argument("--no-globals", action="store_true",
                    help="skip globals dump (text only)")
    ap.add_argument("--src", help="path to the preprocessed .mtl source — used"
                    " to resolve function names. Best results with the same"
                    " file that mtl_compiler consumed (post-preproc.pl).")
    ap.add_argument("--check", action="store_true",
                    help="run structural validation only; exit nonzero if any"
                    " problems are found. No disassembly output.")
    args = ap.parse_args()
    bc = parse(Path(args.path))
    if args.src:
        attach_function_names(bc, Path(args.src))

    if args.check:
        problems = check_bytecode(bc)
        if not problems:
            print(
                f"OK — {bc.nbfun} functions, {bc.code_size} bytes of code, "
                f"no structural issues."
            )
            return 0
        for p in problems:
            print(p, file=sys.stderr)
        print(f"FAIL — {len(problems)} structural issue(s).", file=sys.stderr)
        return 1
    fmt = args.format
    if args.json:
        fmt = "json"
    if fmt == "json":
        sys.stdout.write(render_json(bc))
        sys.stdout.write("\n")
    elif fmt == "masm":
        sys.stdout.write(render_masm(bc))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(bc, show_globals=not args.no_globals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
