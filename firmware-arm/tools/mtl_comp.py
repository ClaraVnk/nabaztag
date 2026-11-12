#!/usr/bin/env python3
"""Phase 7a: Metal source compiler — `.mtl` -> `.bin`, byte-identical to the
C++ `mtl_compiler`, and typed the way the C++ types.

This is the frontend that completes the Phase 7 stack we've already
built:

    .mtl source  ──(this module)──→  .bin
                                     │
                                     ▼
                                   mtl_dis.py  (decode)
                                     │
                                     ▼
                                   .masm  ──(mtl_asm.py)──→  .bin

The decoder + encoder are byte-identical to the C++ on production
bytecode. This frontend mirrors the C++ parser (compiler_file.cpp,
compiler_prog.cpp, compiler_term.cpp, compiler_var.cpp) and emits the same
opcodes/operands the C++ compiler would emit, byte-for-byte — and runs the
same type inference at the same points (mtl_types.py is the port of
compiler_type.cpp), so a program this compiler refuses is one the C++
refuses, and `--types` prints the `Compiler : name : type` line the C++
prints for every declaration.

### Supported subset

Top-level:
    proto NAME ARITY;;   proto NAME = TYPE;;
    var NAME [= VALUE];;
    const NAME = VALUE;;
    fun NAME [arg1 arg2 ...] = body;;
    type NAME;;   type NAME = Cons | Cons _ | Cons TYPE;;   type NAME = [f1 f2: TYPE];;
    ifdef NAME { ... } [else { ... }]   ifndef NAME { ... }

Expressions: the whole of compiler_term.cpp except `update … with` and
the `(a :: b)` list pattern in a `let`.

Usage:

    python3 mtl_comp.py program.mtl -o program.bin
    python3 mtl_comp.py program.mtl --types      # the C++'s type lines too
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# We re-use the bytecode encoder shipped earlier.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mtl_asm import Encoder, FunctionBody, Op, NIL  # noqa: E402
from mtl_types import (  # noqa: E402
    MtlError, TNode, TypeDef, TYPENAME_UNDEF, TYPENAME_FUN, TYPENAME_LIST,
    TYPENAME_TUPLE, TYPENAME_TAB, TYPENAME_WEAK,
    createnodetype, createnodetypecore, createnodetuple, createnodefun,
    actualtype, creategraph, creategraph_str, echograph, copytype, unif,
    unif_argfun, unifbigger, argsfromfun, isdecimal,
)


# memory.h: REF_CODE values below zero; >= 0 is a function's arity.
CODE_TYPE = -1
CODE_VAR = -2
CODE_SUM = -3
CODE_STRUCT = -4
CODE_EMPTYTYPE = -5
CODE_FIELD = -6
CODE_CONS = -7
CODE_CONS0 = -8
CODE_CONST = -9


# ---------------------------------------------------------------------------
# The system package — stdlib_core.cpp `corename[]` × `coreval[]` ×
# `corecode[]` × `coretype[]`, in the C++'s row order, plus the opcodes the
# firmware adds through apply-mods.py (verifySig is already in the stock
# table at this commit; netSaePw/netSaeMs from the wpa3 steps and pageRender
# from rom_pages, with the type strings those steps splice in).
#
# (name, opcode name, nargs or CODE_*, type string). An opcode name mtl_asm
# does not know (OPstrright: the four file natives the rabbit VM lacks, and
# whichever of the two 153s this checkout's Op table was built without) is
# still a known name to the type checker — calling it is a compile error
# at codegen, as the C++ would emit an opcode this VM has no case for.
# ---------------------------------------------------------------------------
NATIVES: list[tuple[str | None, str, int, str]] = [
    (None, "0", CODE_TYPE, "I"),
    (None, "0", CODE_TYPE, "S"),
    (None, "0", CODE_TYPE, "F"),
    (None, "0", CODE_TYPE, "Env"),
    (None, "0", CODE_TYPE, "Xml"),
    ("hd", "OPhd", 1, "fun[list u0]u0"),
    ("tl", "OPtl", 1, "fun[list u0]list u0"),
    ("Secholn", "OPSecholn", 1, "fun[S]S"),
    ("Secho", "OPSecho", 1, "fun[S]S"),
    ("Iecholn", "OPIecholn", 1, "fun[u0]u0"),
    ("Iecho", "OPIecho", 1, "fun[u0]u0"),
    ("time_ms", "OPtime_ms", 0, "fun[]I"),
    ("tabnew", "OPtabnew", 2, "fun[u0 I]tab u0"),
    ("abs", "OPabs", 1, "fun[I]I"),
    ("min", "OPmin", 2, "fun[I I]I"),
    ("max", "OPmax", 2, "fun[I I]I"),
    ("rand", "OPrand", 0, "fun[]I"),
    ("srand", "OPsrand", 1, "fun[I]I"),
    ("time", "OPtime", 0, "fun[]I"),
    ("strnew", "OPstrnew", 1, "fun[I]S"),
    ("strset", "OPstrset", 3, "fun[S I I]S"),
    ("strcpy", "OPstrcpy", 5, "fun[S I S I I]S"),
    ("vstrcmp", "OPvstrcmp", 5, "fun[S I S I I]I"),
    ("strfind", "OPstrfind", 5, "fun[S I S I I]I"),
    ("strfindrev", "OPstrfindrev", 5, "fun[S I S I I]I"),
    ("strlen", "OPstrlen", 1, "fun[S]I"),
    ("strget", "OPstrget", 2, "fun[S I]I"),
    ("strsub", "OPstrsub", 3, "fun[S I I]S"),
    ("strcat", "OPstrcat", 2, "fun[S S]S"),
    ("tablen", "OPtablen", 1, "fun[tab u0]I"),
    ("strcatlist", "OPstrcatlist", 1, "fun[list S]S"),
    ("led", "OPled", 2, "fun[I I]I"),
    ("motorset", "OPmotorset", 2, "fun[I I]I"),
    ("motorget", "OPmotorget", 1, "fun[I]I"),
    ("fixarg1", "OPfixarg", 2, "fun[fun[u0]u100 u0]fun[]u100"),
    ("fixarg2", "OPfixarg", 2, "fun[fun[u1 u0]u100 u0]fun[u1]u100"),
    ("fixarg3", "OPfixarg", 2, "fun[fun[u1 u2 u0]u100 u0]fun[u1 u2]u100"),
    ("fixarg4", "OPfixarg", 2, "fun[fun[u1 u2 u3 u0]u100 u0]fun[u1 u2 u3]u100"),
    ("fixarg5", "OPfixarg", 2, "fun[fun[u1 u2 u3 u4 u0]u100 u0]fun[u1 u2 u3 u4]u100"),
    ("fixarg6", "OPfixarg", 2, "fun[fun[u1 u2 u3 u4 u5 u0]u100 u0]fun[u1 u2 u3 u4 u5]u100"),
    ("fixarg7", "OPfixarg", 2, "fun[fun[u1 u2 u3 u4 u5 u6 u0]u100 u0]fun[u1 u2 u3 u4 u5 u6]u100"),
    ("fixarg8", "OPfixarg", 2, "fun[fun[u1 u2 u3 u4 u5 u6 u7 u0]u100 u0]fun[u1 u2 u3 u4 u5 u6 u7]u100"),
    ("button2", "OPbutton2", 0, "fun[]I"),
    ("button3", "OPbutton3", 0, "fun[]I"),
    ("playStart", "OPplayStart", 2, "fun[I fun[I]I]I"),
    ("playFeed", "OPplayFeed", 3, "fun[S I I]I"),
    ("playStop", "OPplayStop", 0, "fun[]I"),
    ("recStart", "OPrecStart", 3, "fun[I I fun[S]I]I"),
    ("recStop", "OPrecStop", 0, "fun[]I"),
    ("recVol", "OPrecVol", 2, "fun[S I]I"),
    ("load", "OPload", 5, "fun[S I S I I]I"),
    ("gc", "OPgc", 0, "fun[]I"),
    ("save", "OPsave", 5, "fun[S I S I I]I"),
    ("bytecode", "OPbytecode", 1, "fun[S]S"),
    ("loopcb", "OPloopcb", 1, "fun[fun[]I]fun[]I"),
    ("udpStart", "OPudpStart", 1, "fun[I]I"),
    ("udpCb", "OPudpCb", 1, "fun[fun[I S S]I]fun[I S S]I"),
    ("udpStop", "OPudpStop", 1, "fun[I]I"),
    ("udpSend", "OPudpSend", 6, "fun[I S I S I I]I"),
    ("tcpOpen", "OPtcpOpen", 2, "fun[S I]I"),
    ("tcpClose", "OPtcpClose", 1, "fun[I]I"),
    ("tcpSend", "OPtcpSend", 4, "fun[I S I I]I"),
    ("tcpCb", "OPtcpCb", 1, "fun[fun[I I S]I]fun[I I S]I"),
    ("tcpListen", "OPtcpListen", 1, "fun[I]I"),
    ("tcpEnable", "OPtcpEnable", 2, "fun[I I]I"),
    ("envget", "OPenvget", 0, "fun[]S"),
    ("envset", "OPenvset", 1, "fun[S]S"),
    ("sndVol", "OPsndVol", 1, "fun[I]I"),
    ("rfidGet", "OPrfidGet", 0, "fun[]S"),
    ("playTime", "OPplayTime", 0, "fun[]I"),
    ("sndRefresh", "OPsndRefresh", 0, "fun[]I"),
    ("sndWrite", "OPsndWrite", 2, "fun[I I]I"),
    ("sndRead", "OPsndRead", 1, "fun[I]I"),
    ("sndFeed", "OPsndFeed", 3, "fun[S I I]I"),
    ("sndAmpli", "OPsndAmpli", 1, "fun[I]I"),
    ("netCb", "OPnetCb", 1, "fun[fun[S S]I]fun[S S]I"),
    ("netSend", "OPnetSend", 6, "fun[S I I S I I]I"),
    ("netState", "OPnetState", 0, "fun[]I"),
    ("netMac", "OPnetMac", 0, "fun[]S"),
    ("netChk", "OPnetChk", 4, "fun[S I I I]I"),
    ("netSetmode", "OPnetSetmode", 3, "fun[I S I]I"),
    ("netScan", "OPnetScan", 1, "fun[S]list[S S S I I I I]"),
    ("netAuth", "OPnetAuth", 4, "fun[[S S S I I I I] I I S][S S S I I I I]"),
    ("netPmk", "OPnetPmk", 2, "fun[S S]S"),
    ("netRssi", "OPnetRssi", 0, "fun[]I"),
    ("netSeqAdd", "OPnetSeqAdd", 2, "fun[S I]S"),
    ("strgetword", "OPstrgetword", 2, "fun[S I]I"),
    ("strputword", "OPstrputword", 3, "fun[S I I]S"),
    ("atoi", "OPatoi", 1, "fun[S]I"),
    ("htoi", "OPhtoi", 1, "fun[S]I"),
    ("itoa", "OPitoa", 1, "fun[I]S"),
    ("ctoa", "OPctoa", 1, "fun[I]S"),
    ("itoh", "OPitoh", 1, "fun[I]S"),
    ("ctoh", "OPctoh", 1, "fun[I]S"),
    ("itobin2", "OPitobin2", 1, "fun[I]S"),
    ("listswitch", "OPlistswitch", 2, "fun[list[u0 u1] u0]u1"),
    ("listswitchstr", "OPlistswitchstr", 2, "fun[list[S u1] S]u1"),
    ("corePP", "OPcorePP", 0, "fun[]I"),
    ("corePush", "OPcorePush", 1, "fun[u0]u0"),
    ("corePull", "OPcorePull", 1, "fun[I]I"),
    ("coreBit0", "OPcoreBit0", 2, "fun[u0 I]u0"),
    ("reboot", "OPreboot", 2, "fun[I I]I"),
    ("flashFirmware", "OPflashFirmware", 3, "fun[S I I]I"),
    ("strcmp", "OPstrcmp", 2, "fun[S S]I"),
    ("adp2wav", "OPadp2wav", 5, "fun[S I S I I]S"),
    ("wav2adp", "OPwav2adp", 5, "fun[S I S I I]S"),
    ("alaw2wav", "OPalaw2wav", 6, "fun[S I S I I I]S"),
    ("wav2alaw", "OPwav2alaw", 6, "fun[S I S I I I]S"),
    ("crypt", "OPcrypt", 5, "fun[S I I I I]I"),
    ("uncrypt", "OPuncrypt", 5, "fun[S I I I I]I"),
    ("rfidGetList", "OPrfidGetList", 0, "fun[]list S"),
    ("rfidRead", "OPrfidRead", 2, "fun[S I]S"),
    ("rfidWrite", "OPrfidWrite", 3, "fun[S I S]I"),
    ("i2cRead", "OPi2cRead", 2, "fun[I I]S"),
    ("i2cWrite", "OPi2cWrite", 3, "fun[I S I]I"),
    ("strright", "OPstrright", 2, "fun[S I]S"),
    ("strcrypt8", "OPstrright", 3, "fun[S I I][S I]"),
    ("loadf", "OPstrright", 1, "fun[S]S"),
    ("savef", "OPstrright", 2, "fun[S S]I"),
    ("verifySig", "OPverifySig", 2, "fun[S S]I"),
    # apply-mods.py wpa3_boot_pw / wpa3_sae / rom_pages
    ("netSaePw", "OPnetSaePw", 1, "fun[S]I"),
    ("netSaeMs", "OPnetSaeMs", 1, "fun[S]I"),
    ("pageRender", "OPpageRender", 2, "fun[I list[S S]]S"),
    # apply-mods.py adoption_kx / tls_psk, numbered as in lean-wpa3-tls
    ("kxBegin", "OPkxBegin", 0, "fun[]S"),
    ("kxFinish", "OPkxFinish", 1, "fun[S]S"),
    ("kxUse", "OPkxUse", 1, "fun[S]I"),
    ("kxCheck", "OPkxCheck", 2, "fun[S S]I"),
    ("tlsStart", "OPtlsStart", 1, "fun[S]S"),
    ("tlsIn", "OPtlsIn", 1, "fun[S]S"),
    ("tlsOut", "OPtlsOut", 1, "fun[S]S"),
    ("tlsState", "OPtlsState", 0, "fun[]I"),
    ("tlsClose", "OPtlsClose", 0, "fun[]S"),
]

# mnemonic name → (opcode, nargs), for mtl_pyc.py and anything else that
# wants the table without the types. Only the names this Op table can emit.
BUILTINS: dict[str, tuple[int, int]] = {
    name: (getattr(Op, opname), nargs)
    for name, opname, nargs, _t in NATIVES
    if name is not None and nargs >= 0 and hasattr(Op, opname)
}

# Keywords that terminate an expression — never lookup as identifiers.
KEYWORDS = {
    "fun", "var", "const", "proto", "type", "if", "then", "else",
    "let", "in", "set", "while", "do", "for", "match", "with",
    "nil", "call", "update", "true", "false", "ifdef", "ifndef",
}


# ---------------------------------------------------------------------------
# Lexer  —  mirrors parser.cpp::gettoken
# ---------------------------------------------------------------------------
@dataclass
class Token:
    kind: str   # 'int' | 'hex' | 'str' | 'id' | 'op' | 'eof'
    text: str
    value: Any = None
    line: int = 0
    col: int = 0


_MULTI_CHAR_OPS = {
    "&&", "||", "::", "^^", ";;", "->",
    "<<", ">>", "==", "!=", "<=", ">=",
    "</", "/>", "/*", "*/",
}
_SINGLE_OPS = set("+-*/%&|^~!<>=()[]{}#;,:.'\"")  # `\"` only as opener


def _wrap_i32(v: int) -> int:
    """Integer literals land in a C++ `int`, so anything wider than 32 bits
    is silently truncated — the accumulator just wraps.

    boot's md5bytes writes the MD5 length masks at their natural 48-bit
    width: `0xFFFFFF000000&md5len`. The reference C++ output emits
    `OPint -16777216` there (0x0D00 in reference-cxx-output.bin), i.e.
    0xFFFFFF000000 mod 2**32 read as signed. Reproduce that rather than
    rejecting the literal.
    """
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def lex(src: str) -> list[Token]:
    """Tokenize a Metal source string. Strips // and /* */ comments. Returns
    a list of tokens ending with a 'eof' marker.
    """
    out: list[Token] = []
    i, n = 0, len(src)
    line, line_start = 1, 0
    while i < n:
        c = src[i]
        # Whitespace.
        if c in " \t\r":
            i += 1
            continue
        if c == "\n":
            line += 1
            line_start = i + 1
            i += 1
            continue
        col = i - line_start + 1
        # Line comment.
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        # Block comment (nest-supporting).
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth > 0:
                if src[i] == "/" and i + 1 < n and src[i + 1] == "*":
                    depth += 1
                    i += 2
                elif src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    depth -= 1
                    i += 2
                else:
                    if src[i] == "\n":
                        line += 1
                        line_start = i + 1
                    i += 1
            continue
        # String literal.
        if c == '"':
            j = i + 1
            out_str = bytearray()
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    # MTL string escape semantics — read from parser_xml.cpp:
                    #   if escape-char is a CONTROL char (<32, i.e. \n, \r,
                    #   \t at SOURCE level), it's a line-continuation:
                    #   skip the backslash AND every consecutive control
                    #   char (which collapses indentation on the next
                    #   line). NOT an output character.
                    if ord(nxt) < 32:
                        j += 2
                        while j < n and ord(src[j]) < 32:
                            if src[j] == "\n":
                                line += 1
                                line_start = j + 1
                            j += 1
                        continue
                    # `\n` (the literal 2-char sequence) → newline 10.
                    if nxt == "n":   out_str.append(10); j += 2; continue
                    # `\z` → NUL (Metal-specific).
                    if nxt == "z":   out_str.append(0);  j += 2; continue
                    # `\$XX` → hex byte.
                    if nxt == "$" and j + 3 < n:
                        out_str.append(int(src[j + 2:j + 4], 16))
                        j += 4
                        continue
                    # `\DDD` → decimal byte (1, 2, or 3 digits).
                    if nxt.isdigit():
                        end = j + 2
                        while end < n and src[end].isdigit() and (end - (j + 1)) < 3:
                            end += 1
                        out_str.append(int(src[j + 1:end]) & 0xFF)
                        j = end
                        continue
                    # Anything else (incl. `\\`, `\"`, `\t`, `\r`,
                    # `\anything-printable`): the C++ treats this as
                    # an OUTPUT char of value `nxt`. (Tested against
                    # parser_xml.cpp::getstring: the default branch
                    # is `output->addchar(c)` where c is the second
                    # char raw.)
                    out_str.append(ord(nxt))
                    j += 2
                    continue
                if src[j] == "\n":
                    line += 1
                    line_start = j + 1
                out_str.append(ord(src[j]))
                j += 1
            if j >= n:
                raise MtlError(f"line {line}: unterminated string")
            out.append(Token("str", src[i:j + 1], bytes(out_str), line, col))
            i = j + 1
            continue
        # Identifier or number (Metal lumps both — they differ by content).
        if c.isalpha() or c == "_" or c.isdigit():
            j = i
            only_digits = True
            while j < n and (src[j].isalnum() or src[j] == "_"):
                if not src[j].isdigit():
                    only_digits = False
                j += 1
            # Float? (digit + '.' + digit) — we don't support floats yet,
            # but flag for a clean error.
            if only_digits and j < n and src[j] == ".":
                k = j + 1
                while k < n and src[k].isdigit():
                    k += 1
                if k > j + 1:
                    raise MtlError(
                        f"line {line}: float literals not yet supported"
                    )
            tok_text = src[i:j]
            if only_digits:
                out.append(Token("int", tok_text, _wrap_i32(int(tok_text)),
                                 line, col))
            elif tok_text.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in tok_text[2:]):
                out.append(Token("int", tok_text, _wrap_i32(int(tok_text, 16)),
                                 line, col))
            else:
                out.append(Token("id", tok_text, tok_text, line, col))
            i = j
            continue
        # Multi-char operator.
        if i + 1 < n and src[i:i + 2] in _MULTI_CHAR_OPS:
            out.append(Token("op", src[i:i + 2], src[i:i + 2], line, col))
            i += 2
            continue
        # Single-char operator.
        out.append(Token("op", c, c, line, col))
        i += 1
    out.append(Token("eof", "", None, line, i - line_start + 1))
    return out


# ---------------------------------------------------------------------------
# Parser + codegen — recursive descent that emits directly into a
# FunctionBody. Mirrors the precedence ladder of compiler_term.cpp:
#     program       :   expr (; expr)*
#     expression    :   arithm (:: expression)?
#     arithm        :   a1 (&& | ||) a1 ...           (short-circuit)
#     a1            :   '!' a1 | a2
#     a2            :   a3 (== != < > <= >= a3)*       (comparison)
#     a3            :   a4 (+ -)*
#     a4            :   a5 (* / %)*
#     a5            :   a6 (& | ^ << >>)*
#     a6            :   '-' int | '-' a6 | '~' a6 | term
#     term          :   ( program ) | nil | int | string |
#                       'if' parseif | 'let' parselet | 'set' parseset |
#                       identifier (ref or call)
#
# Every parse method returns the TNode the C++ leaves on its type stack.
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    """Lexical scope for `let`/`for` locals — the C++ `locals` label list.
    Each entry: name -> stack offset (a 0-based call-frame index used by
    OPgetlocalb/OPsetlocalb) and the local's type node.

    Supports shadowing — if a name is re-declared, the previous binding
    is saved and restored on unbind, so:
        let X -> i in (
            for i=0; ...   ; an INNER `i` (different local index)
            ...
            ; OUTER `i` is restored here, still bound at its original idx
        )
    """
    names: dict[str, int] = field(default_factory=dict)
    next_index: int = 0
    # Per name: stack of (index, type) that have been declared but not yet
    # unbound. `lookup` returns the top of the stack (innermost binding).
    _shadow: dict[str, list[tuple[int, TNode | None]]] = field(default_factory=dict)

    def declare(self, name: str, tnode: TNode | None = None) -> int:
        idx = self.next_index
        self._shadow.setdefault(name, []).append((idx, tnode))
        self.names[name] = idx
        self.next_index += 1
        return idx

    def lookup(self, name: str) -> int | None:
        return self.names.get(name)

    def lookup_type(self, name: str) -> TNode | None:
        stack = self._shadow.get(name)
        return stack[-1][1] if stack else None

    def unbind(self, name: str) -> None:
        """Pop the innermost binding for `name`. Restores the previous
        shadowed binding (if any). Decrements next_index by 1.
        """
        stack = self._shadow.get(name)
        if not stack:
            raise KeyError(name)
        stack.pop()
        if stack:
            self.names[name] = stack[-1][0]
        else:
            del self.names[name]
            del self._shadow[name]
        self.next_index -= 1


@dataclass(eq=False)
class Ref:
    """One package entry — memory.h's REF_* block.

    code: arity for a function, else CODE_*.
    type: the REF_TYPE graph.
    val:  REF_VAL — var/const: initial value; field: (FIELD_NUM, type Ref);
          cons: tag; struct type: field count; fun: True once it has a body
          (a proto has None until `fun` fills it), natives: True.
    package: REF_PACKAGE — fun index, or global index for var/const.
    native: the opcode for a native (FUN_NBLOCALS==NIL in the C++), or the
          opcode name when this Op table lacks it.
    """
    name: str | None
    code: int
    type: TNode
    val: Any = None
    package: int | None = None
    native: int | str | None = None


class _TokenStream:
    """The Parser next()/giveback() view of the token list that
    mtl_types.parsegraph reads a source-level type through."""

    def __init__(self, comp: "Compiler") -> None:
        self.c = comp

    def next(self) -> str | None:
        t = self.c.advance()
        if t.kind == "eof":
            return None
        return t.text

    def giveback(self) -> None:
        self.c.pos -= 1


class Compiler:
    def __init__(self) -> None:
        self.enc = Encoder()
        # The package under compilation — compiler.cpp's hash of refs. One
        # namespace for var/const/fun/proto/field/constructor (last
        # declaration wins, as the C++ pushes at the head of the bucket),
        # and the list of every ref by name for fillproto/findproto.
        self.refs: dict[str, Ref] = {}
        self.refs_all: dict[str, list[Ref]] = {}
        # Type refs (REF_NAME is NIL in the C++; found by searchtype on the
        # core node's name).
        self.types: dict[str, Ref] = {}
        self.types_all: dict[str, list[Ref]] = {}
        # The system package: natives and the basic types.
        self.sys_refs: dict[str, Ref] = {}
        self.sys_types: dict[str, Ref] = {}
        self.stdtypes: dict[str, TNode] = {}
        self._start()
        # Function-being-compiled state.
        self.fn: FunctionBody | None = None
        self.scope: Scope | None = None
        self.newref: Ref | None = None
        self.label_counter = 0
        # Internal helpers.
        self.tokens: list[Token] = []
        self.pos = 0
        # Track total nlocals declared by `let` so we can set fn.nlocals at
        # the end.
        self.nlocals_high_water = 0
        # The `Compiler : name : type` lines, in the C++'s order.
        self.type_lines: list[str] = []
        self.protos: list[Ref] = []

    # ---- compiler.cpp::start + addnative ---------------------------------
    def _start(self) -> None:
        for name, opname, code, typestr in NATIVES:
            if code == CODE_TYPE:
                tref = Ref(None, CODE_TYPE, createnodetypecore(TypeDef(typestr)))
                self.sys_types[typestr] = tref
            else:
                tnode = creategraph_str(typestr, self._searchtype)
                op: int | str = getattr(Op, opname, opname)
                self.sys_refs[name] = Ref(name, code, tnode, val=True, native=op)
        for key, s in (
            ("I", "I"), ("F", "F"), ("S", "S"), ("Env", "Env"), ("Xml", "Xml"),
            ("cons", "fun[u0 list u0]list u0"),
            ("tab", "fun[tab u0 I]u0"),
            ("call", "fun[fun u0 u1 u0]u1"),
        ):
            self.stdtypes[key] = creategraph_str(s, self._searchtype)

    # ---- compiler.cpp::searchref / searchtype / searchemptytype ----------
    def _searchref(self, name: str) -> Ref | None:
        r = self.refs.get(name)
        if r is not None:
            return r
        return self.sys_refs.get(name)

    def _searchtype(self, name: str) -> TNode | None:
        r = self.types.get(name)
        if r is None:
            r = self.sys_types.get(name)
        return r.type if r is not None else None

    def _searchemptytype(self, name: str) -> Ref | None:
        for r in reversed(self.types_all.get(name, [])):
            if r.code == CODE_EMPTYTYPE:
                return r
        return None

    def _addref(self, ref: Ref) -> None:
        self.refs[ref.name] = ref
        self.refs_all.setdefault(ref.name, []).append(ref)

    def _addtype(self, ref: Ref) -> None:
        name = ref.type.tdef.name
        self.types[name] = ref
        self.types_all.setdefault(name, []).append(ref)

    def _I(self) -> TNode:
        return self.stdtypes["I"]

    def _S(self) -> TNode:
        return self.stdtypes["S"]

    def _echo(self, line: str) -> None:
        self.type_lines.append(line)

    # ---- Token helpers ---------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.kind != "eof":
            self.pos += 1
        return t

    def expect(self, text: str) -> Token:
        t = self.peek()
        if t.text != text:
            raise MtlError(
                f"line {t.line}: expected {text!r}, got {t.text!r}"
            )
        return self.advance()

    def accept(self, text: str) -> bool:
        if self.peek().text == text:
            self.advance()
            return True
        return False

    # ---- Label helpers (forward jumps in FunctionBody) --------------------
    def new_label(self, prefix: str = "L") -> str:
        self.label_counter += 1
        return f"{prefix}{self.label_counter}"

    # ---- Top-level: parsefile ----------------------------------------------
    def compile_source(self, src: str) -> bytes:
        self.tokens = lex(src)
        self.pos = 0
        # Walk top-level declarations.
        while self.peek().kind != "eof":
            self._top_decl()
        # compiler_prog.cpp::recbc — a proto that never got its body.
        for p in self.protos:
            if p.val is None:
                raise MtlError(f"{p.name} is EMPTY !!!")
        # Realize the encoder.
        return self.enc.emit()

    def _top_decl(self) -> None:
        t = self.peek()
        if t.text == "proto":
            self._proto_decl()
        elif t.text == "var":
            self._var_decl(is_const=False)
        elif t.text == "const":
            self._var_decl(is_const=True)
        elif t.text == "fun":
            self._fun_decl()
        elif t.text == "type":
            self._type_decl()
        elif t.text == "ifdef":
            self._ifdef_decl(invert=False)
        elif t.text == "ifndef":
            self._ifdef_decl(invert=True)
        else:
            raise MtlError(f"Compiler : unknown token {t.text}")

    def _is_defined(self, name: str) -> bool:
        """compiler_file.cpp::parseifdef — `searchref || searchtype`."""
        return self._searchref(name) is not None or self._searchtype(name) is not None

    def _ifdef_decl(self, *, invert: bool) -> None:
        """`ifdef NAME { ... } [else { ... }]`  (or `ifndef NAME`).

        Top-level only. The active branch is parsed as a sequence of
        top-level decls; the inactive branch's tokens are skipped by
        matching brace nesting (just like compiler_file.cpp::skipifdef).
        """
        self.advance()  # consume ifdef/ifndef
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise MtlError(
                f"Compiler : label expected (found '{name_tok.text}')"
            )
        first = self._is_defined(name_tok.text)
        if invert:
            first = not first
        self.expect("{")
        if first:
            self._parse_block_decls()
            # `}` consumed by _parse_block_decls when it sees it.
        else:
            self._skip_braces()
        # Optional else.
        if self.peek().text == "else":
            self.advance()
            self.expect("{")
            if first:
                self._skip_braces()
            else:
                self._parse_block_decls()

    def _parse_block_decls(self) -> None:
        """Parse top-level decls until a matching `}`. Each decl can be
        any of the top-level forms, INCLUDING nested ifdef.
        """
        while True:
            t = self.peek()
            if t.text == "}":
                self.advance()
                return
            if t.kind == "eof":
                raise MtlError("Compiler : '}' expected (found EOF)")
            self._top_decl()

    def _skip_braces(self) -> None:
        """Skip tokens until the matching `}` (counting nesting)."""
        depth = 1
        while depth > 0:
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : '}' expected (found EOF)")
            if t.text == "{":
                depth += 1
            elif t.text == "}":
                depth -= 1

    # ---- compiler_file.cpp::parsetype / parsestruct / parsesum ------------
    def _type_decl(self) -> None:
        """`type Name = ...;;` — three forms:
            type Name = Cons1 | Cons2 _ | ...;;        sum (ADT)
            type Name = [field1 field2 ...];;          struct (record)
            type Name;;                                opaque (just declared)
        """
        self.expect("type")
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise MtlError(f"Compiler : name of type expected (found '{name_tok.text}')")
        name = name_tok.text
        try:
            self._parsetype(name)
        except MtlError as e:
            raise e.add(f"Compiler : error compiling type '{name}'")

    def _parsetype(self, name: str) -> None:
        labels: list[tuple[str, TNode]] = []       # the C++ `locals` label list
        newref = self._searchemptytype(name)
        mergetype = True
        if newref is not None:
            core = createnodetypecore(newref.type.tdef)
        else:
            mergetype = False
            core = createnodetypecore(TypeDef(name))
            newref = Ref(None, CODE_EMPTYTYPE, core)
            self._addtype(newref)
        # Type parameters: `type Name(a b) = …`.
        if self.peek().text == "(":
            self.advance()
            params: list[TNode] = []
            while True:
                t = self.advance()
                if t.kind == "eof":
                    raise MtlError("Compiler : parameter or ')' expected (found EOF)")
                if t.text == ")":
                    break
                if t.kind != "id":
                    raise MtlError(f"Compiler : parameter or ')' expected (found '{t.text}')")
                node = createnodetype(TYPENAME_UNDEF)
                labels.append((t.text, node))
                params.append(node)
            core.args = params
        if mergetype:
            unif(core, newref.type)
        t = self.advance()
        if t.kind == "eof":
            raise MtlError("Compiler : '=' or ';;' expected (found EOF)")
        if t.text == "=":
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : uncomplete type definition (found EOF)")
            if t.text == "[":
                return self._parsestruct(newref, labels)
            self.pos -= 1
            return self._parsesum(newref, labels)
        if t.text == ";;":
            self._echo(f"Compiler : uncompleted type : {echograph(newref.type)}")
            return
        raise MtlError(f"Compiler : '=' or ';;' expected (found '{t.text}')")

    def _parsestruct(self, newref: Ref, labels: list[tuple[str, TNode]]) -> None:
        """Struct-type body: `[field1 field2: TYPE ...];;`

        Each field becomes a CODE_FIELD ref whose type is
        `fun [Struct] fieldtype` — fieldtype a fresh weak variable unless
        annotated (compiler_file.cpp::parsestruct).
        """
        newref.val = 0
        newref.code = CODE_STRUCT
        while True:
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : field name or ']' expected (found EOF)")
            if t.kind == "id":
                fld = Ref(t.text, CODE_FIELD, None)  # type: ignore[arg-type]
                self._addref(fld)
                fld.val = (newref.val, newref)
                newref.val += 1
                args = createnodetuple([newref.type])
                if self.peek().text == ":":
                    self.advance()
                    result = creategraph(_TokenStream(self), self._searchtype, 1, labels)
                else:
                    result = createnodetype(TYPENAME_WEAK)
                fld.type = createnodefun(args, result)
                self._echo(f"Compiler : {fld.name} : {echograph(fld.type)}")
            elif t.text == "]":
                break
            else:
                raise MtlError(f"Compiler : field name or ']' expected (found '{t.text}')")
        self.expect(";;")
        self._echo(f"Compiler : {echograph(newref.type)} : struct ({newref.val})")

    def _parsesum(self, newref: Ref, labels: list[tuple[str, TNode]]) -> None:
        """Sum-type body: `Cons1 | Cons2 _ | Cons3 SomeType | ...;;`
        (compiler_file.cpp::parsesum)."""
        newref.val = 0
        newref.code = CODE_SUM
        while True:
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : constructor expected (found EOF)")
            if t.kind != "id":
                raise MtlError(f"Compiler : constructor expected (found '{t.text}')")
            cons = Ref(t.text, CODE_CONS0, None)  # type: ignore[arg-type]
            self._addref(cons)
            cons.val = newref.val
            newref.val += 1
            if self.peek().text in ("|", ";;"):
                args = createnodetuple([])
                cons.code = CODE_CONS0
            else:
                payload = creategraph(_TokenStream(self), self._searchtype, 1, labels)
                args = createnodetuple([payload])
                cons.code = CODE_CONS
            cons.type = createnodefun(args, newref.type)
            self._echo(f"Compiler : {cons.name} : {echograph(cons.type)}")
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : '|' or ';;' expected (found EOF)")
            if t.text == ";;":
                break
            if t.text != "|":
                raise MtlError(f"Compiler : '|' or ';;' expected (found '{t.text}')")
        self._echo(f"Compiler : {echograph(newref.type)} : constructor")

    # ---- compiler_file.cpp::parseproto -------------------------------------
    def _proto_decl(self) -> None:
        self.expect("proto")
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise MtlError(f"Compiler : name of function expected (found '{name_tok.text}')")
        name = name_tok.text
        try:
            self._parseproto(name)
        except MtlError as e:
            raise e.add(f"Compiler : error compiling proto '{name}'")

    def _parseproto(self, name: str) -> None:
        t = self.advance()
        if t.kind == "eof":
            raise MtlError("Compiler : integer or '=' expected (found EOF)")
        nbarg = -1
        if t.text == "=":
            tnode = creategraph(_TokenStream(self), self._searchtype, 0)
            p = actualtype(tnode)
            if p.code == TYPENAME_FUN and p.a is not None:
                a = actualtype(p.a)
                if a.code == TYPENAME_TUPLE:
                    nbarg = len(a.items)
            if nbarg < 0:
                raise MtlError("Compiler : function type expected")
        elif t.kind == "int" and isdecimal(t.text):
            nbarg = t.value
            args = createnodetuple([createnodetype(TYPENAME_WEAK) for _ in range(nbarg)])
            tnode = createnodefun(args, createnodetype(TYPENAME_WEAK))
        else:
            raise MtlError(f"Compiler : integer or '=' expected (found '{t.text}')")
        self.expect(";;")
        # The C++ hands the proto the next function index (ifuns++); the
        # matching `fun` later fills that slot.
        idx = len(self.enc._functions)
        self.enc.new_function(nargs=nbarg, nlocals=0)
        ref = Ref(name, nbarg, tnode, val=None, package=idx)
        self._addref(ref)
        self.protos.append(ref)
        self._echo(f"Compiler : proto {name} : {echograph(tnode)}")

    # ---- compiler_file.cpp::parsevar / parseconst -------------------------
    def _var_decl(self, *, is_const: bool) -> None:
        self.advance()  # var | const
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise MtlError(f"Compiler : name of reference expected (found '{name_tok.text}')")
        name = name_tok.text
        try:
            if is_const:
                self.expect("=")
                value, tnode = self._parseval()
            else:
                t = self.peek()
                if t.kind == "eof":
                    raise MtlError("Compiler : ';;' expected (found EOF)")
                if t.text == "=":
                    self.advance()
                    value, tnode = self._parseval()
                else:
                    value, tnode = NIL, createnodetype(TYPENAME_WEAK)
            self.expect(";;")
        except MtlError as e:
            raise e.add(f"Compiler : error compiling {'const' if is_const else 'var'} '{name}'")
        idx = self.enc.add_global(value)
        ref = Ref(name, CODE_CONST if is_const else CODE_VAR, tnode, val=value, package=idx)
        self._addref(ref)
        self._echo(f"Compiler : {'const' if is_const else 'var'} {name} : {echograph(tnode)}")

    # ---- compiler_var.cpp: constant expressions ---------------------------
    def _parseval(self) -> tuple[Any, TNode]:
        """parseval := val3 ( :: parseval )?  — evaluated at compile time
        into the globals section, typed like the runtime cons."""
        v, t = self._parseval3()
        if self.peek().text != "::":
            return v, t
        self.advance()
        v2, t2 = self._parseval()
        lst = createnodetype(TYPENAME_LIST)
        elem = createnodetype(TYPENAME_UNDEF)
        lst.a = elem
        unif(elem, t)
        unif(lst, t2)
        return (v, v2), lst

    def _parseval3(self) -> tuple[Any, TNode]:
        v, t = self._parseval4()
        while self.peek().text in ("+", "-"):
            op = self.advance().text
            v2, t2 = self._parseval4()
            v = _wrap_i32(v + v2 if op == "+" else v - v2)
            unif(t2, self._I())
            unif(t, self._I())
        return v, t

    def _parseval4(self) -> tuple[Any, TNode]:
        v, t = self._parseval5()
        while self.peek().text in ("*", "/", "%"):
            op = self.advance().text
            v2, t2 = self._parseval5()
            if op == "*":
                v = _wrap_i32(v * v2)
            elif op == "/":
                if v2 == 0:
                    raise MtlError("division by zero in a constant")
                v = _wrap_i32(int(v / v2))
            else:
                v = _wrap_i32(v - v2 * int(v / v2)) if v2 else 0
            unif(t2, self._I())
            unif(t, self._I())
        return v, t

    def _parseval5(self) -> tuple[Any, TNode]:
        v, t = self._parseval6()
        while self.peek().text in ("&", "|", "^", "<<", ">>"):
            op = self.advance().text
            v2, t2 = self._parseval6()
            if op == "&":
                v = _wrap_i32(v & v2)
            elif op == "|":
                v = _wrap_i32(v | v2)
            elif op == "^":
                v = _wrap_i32(v ^ v2)
            elif op == "<<":
                v = _wrap_i32(v << (v2 & 31))
            else:
                v = _wrap_i32(v >> (v2 & 31))
            unif(t2, self._I())
            unif(t, self._I())
        return v, t

    def _parseval6(self) -> tuple[Any, TNode]:
        t = self.peek()
        if t.text == "-":
            self.advance()
            if self.peek().kind == "int" and isdecimal(self.peek().text):
                return _wrap_i32(-self.advance().value), self._I()
            v, tn = self._parseval6()
            unif(tn, self._I())
            return _wrap_i32(-v), tn
        if t.text == "~":
            self.advance()
            v, tn = self._parseval6()
            unif(tn, self._I())
            return _wrap_i32(~v), tn
        return self._parseval7()

    def _parseval7(self) -> tuple[Any, TNode]:
        t = self.advance()
        if t.kind == "eof":
            raise MtlError("Compiler : term expected (found EOF)")
        if t.text == "(":
            v, tn = self._parseval()
            self.expect(")")
            return v, tn
        if t.text == "[":
            items: list[Any] = []
            types: list[TNode] = []
            while True:
                if self.peek().kind == "eof":
                    raise MtlError("Compiler : ']' expected (found EOF)")
                if self.peek().text == "]":
                    self.advance()
                    return tuple(items), createnodetuple(types)
                v, tn = self._parseval()
                items.append(v)
                types.append(tn)
        if t.text == "{":
            tab = createnodetype(TYPENAME_TAB)
            elem = createnodetype(TYPENAME_UNDEF)
            tab.a = elem
            items = []
            while True:
                if self.peek().kind == "eof":
                    raise MtlError("Compiler : '}' expected (found EOF)")
                if self.peek().text == "}":
                    self.advance()
                    return tuple(items), tab
                v, tn = self._parseval()
                unif(tn, elem)
                items.append(v)
        if t.text == "nil":
            return NIL, createnodetype(TYPENAME_WEAK)
        if t.text == "'":
            c_tok = self.advance()
            if c_tok.kind == "eof":
                raise MtlError("Compiler : 'char expected (found EOF)")
            v = ord(c_tok.text[0]) & 0xFF
            self.expect("'")
            return v, self._I()
        if t.kind == "int":
            return t.value, self._I()
        if t.kind == "str":
            return t.value, self._S()
        raise MtlError(f"Compiler : unexpected term '{t.text}'")

    # ---- compiler_file.cpp::parsefun ---------------------------------------
    def _fun_decl(self) -> None:
        self.expect("fun")
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise MtlError(f"Compiler : name of function expected (found '{name_tok.text}')")
        name = name_tok.text
        try:
            self._parsefun(name)
        except MtlError as e:
            raise e.add(f"Compiler : error compiling function '{name}'")

    def _parsefun(self, name: str) -> None:
        scope = Scope()
        argnodes: list[TNode] = []
        while True:
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : argument or '=' expected (found EOF)")
            if t.kind == "id":
                node = createnodetype(TYPENAME_UNDEF)
                scope.declare(t.text, node)
                argnodes.append(node)
            elif t.text == "=":
                break
            else:
                raise MtlError(f"Compiler : argument or '=' expected (found '{t.text}')")
        narg = len(argnodes)
        type_result = createnodetype(TYPENAME_UNDEF)
        ftype = createnodefun(createnodetuple(argnodes), type_result)

        # compiler.cpp::findproto — an unfilled proto of the same name
        # hands over its function index; otherwise take the next one.
        proto = None
        for r in reversed(self.refs_all.get(name, [])):
            if r.code >= 0 and r.val is None:
                proto = r
                break
        if proto is not None:
            idx = proto.package
            fn = self.enc._functions[idx]
            fn.nargs = narg
            fn.code = bytearray()
        else:
            fn = self.enc.new_function(nargs=narg, nlocals=0)
            idx = len(self.enc._functions) - 1

        newref = Ref(name, narg, ftype, val=None, package=idx)
        self._addref(newref)
        self.newref = newref

        self.fn = fn
        self.scope = scope
        self.nlocals_high_water = 0

        # Parse the body as a `parseprogram` (sequences with `;`).
        body_t = self._parse_program()
        self.expect(";;")
        unif(type_result, body_t)

        # Body ends — emit OPret.
        self.fn.ret()
        # nlocals = how many `let`-introduced locals beyond the args.
        self.fn.nlocals = self.nlocals_high_water
        newref.val = fn

        # compiler.cpp::fillproto — every unfilled proto of this name.
        for r in self.refs_all.get(name, []):
            if r is not newref and r.code >= 0 and r.val is None:
                try:
                    unifbigger(ftype, r.type)
                except MtlError as e:
                    raise e.add("Compiler : prototype does not match")
                r.val = fn

        self.fn = None
        self.scope = None
        self.newref = None
        self._echo(f"Compiler : {name} : {echograph(ftype)}")

    # ---- Expression parser ------------------------------------------------
    def _parse_program(self) -> TNode:
        """compiler_prog.cpp::parseprogram := expression ( ; expression )*

        Like the C++: each `;` drops the previous value (and its type),
        leaving the last expression's on the stack.
        """
        t = self._parse_expression()
        while True:
            if self.peek().text != ";":
                # `;;` is tokenized as a single token, end-of-decl.
                return t
            self.advance()  # consume `;`
            self.fn.drop()  # OPdrop on previous value
            t = self._parse_expression()

    def _parse_expression(self) -> TNode:
        """compiler_prog.cpp::parseexpression := arithm ( :: expression )?
        — cons is right-associative.

        `A :: B` builds a 2-tuple `[A, B]` (the standard cons cell), typed
        through STDTYPE_fun__u0_list_u0__list_u0.
        """
        t = self._parse_arithm()
        if self.peek().text == "::":
            self.advance()
            t2 = self._parse_expression()
            args = createnodetuple([t, t2])
            f = copytype(self.stdtypes["cons"])
            t = unif_argfun(f, args)
            self.fn.opb(Op.OPdeftabb, 2)
        return t

    def _parse_arithm(self) -> TNode:
        """compiler_prog.cpp::parsearithm := a1 ( (&& | ||) a1 )*"""
        t = self._parse_a1()
        while True:
            tok = self.peek()
            if tok.text not in ("&&", "||"):
                return t
            self.advance()
            # C++ emits OPdup, then OPnon for `||` only, then OPelse.
            self.fn.op(Op.OPdup)
            if tok.text == "||":
                self.fn.op(Op.OPnon)
            skip_label = self.new_label("L")
            self.fn.else_(skip_label)
            self.fn.drop()
            t2 = self._parse_a1()
            self.fn.label(skip_label)
            unif(t2, self._I())
            unif(t, self._I())

    def _parse_a1(self) -> TNode:
        """compiler_prog.cpp::parsea1 := '!' a1 | a2"""
        if self.peek().text == "!":
            self.advance()
            t = self._parse_a1()
            self.fn.op(Op.OPnon)
            unif(t, self._I())
            return t
        return self._parse_a2()

    def _parse_a2(self) -> TNode:
        """compiler_prog.cpp::parsea2 := a3 ( ( == != < > <= >= ) a3 )*"""
        t = self._parse_a3()
        while True:
            tok = self.peek()
            op = {
                "==": Op.OPeq, "!=": Op.OPne,
                "<": Op.OPlt, ">": Op.OPgt,
                "<=": Op.OPle, ">=": Op.OPge,
            }.get(tok.text)
            if op is None:
                return t
            self.advance()
            t2 = self._parse_a3()
            self.fn.op(op)
            unif(t2, t)
            if tok.text not in ("==", "!="):
                unif(t, self._I())
            t = self._I()

    def _parse_a3(self) -> TNode:
        """compiler_prog.cpp::parsea3 := a4 ( ( + - ) a4 )*"""
        t = self._parse_a4()
        while True:
            tok = self.peek()
            op = {"+": Op.OPadd, "-": Op.OPsub}.get(tok.text)
            if op is None:
                return t
            self.advance()
            t2 = self._parse_a4()
            self.fn.op(op)
            unif(t2, self._I())
            unif(t, self._I())

    def _parse_a4(self) -> TNode:
        """compiler_prog.cpp::parsea4 := a5 ( ( * / % ) a5 )*"""
        t = self._parse_a5()
        while True:
            tok = self.peek()
            op = {"*": Op.OPmul, "/": Op.OPdiv, "%": Op.OPmod}.get(tok.text)
            if op is None:
                return t
            self.advance()
            t2 = self._parse_a5()
            self.fn.op(op)
            unif(t2, self._I())
            unif(t, self._I())

    def _parse_a5(self) -> TNode:
        """compiler_prog.cpp::parsea5 := a6 ( ( & | ^ << >> ) a6 )*"""
        t = self._parse_a6()
        while True:
            tok = self.peek()
            op = {
                "&": Op.OPand, "|": Op.OPor, "^": Op.OPeor,
                "<<": Op.OPshl, ">>": Op.OPshr,
            }.get(tok.text)
            if op is None:
                return t
            self.advance()
            t2 = self._parse_a6()
            self.fn.op(op)
            unif(t2, self._I())
            unif(t, self._I())

    def _parse_a6(self) -> TNode:
        """compiler_prog.cpp::parsea6 := '-' int | '-' a6 | '~' a6 | term"""
        t = self.peek()
        if t.text == "-":
            # `- INT` is a NEGATIVE LITERAL (special-cased by C++ to emit
            # one OPint instead of OPintb+OPneg).
            if self.peek(1).kind == "int" and isdecimal(self.peek(1).text):
                self.advance()
                val = -self.advance().value
                self.fn.int_(val)
                return self._I()
            self.advance()
            tn = self._parse_a6()
            self.fn.op(Op.OPneg)
            unif(tn, self._I())
            return tn
        if t.text == "~":
            self.advance()
            tn = self._parse_a6()
            self.fn.op(Op.OPnot)
            unif(tn, self._I())
            return tn
        return self._parse_term()

    def _parse_term(self) -> TNode:
        """compiler_term.cpp::parseterm := ( prog ) | [ tuple ] | { array } |
        nil | int | string | char | #funptr | identifier | if | let | set |
        while | for | match | call"""
        t = self.advance()
        if t.kind == "eof":
            raise MtlError("Compiler : term expected (found EOF)")
        if t.text == "(":
            tn = self._parse_program()
            self.expect(")")
            return tn
        if t.text == "[":
            # `[a b c]` (tuple) OR `[FIELD: v ...]` (struct creation). The
            # C++ looks one token ahead: a label that resolves to a
            # CODE_FIELD ref means parsefields, whatever follows it.
            nxt = self.peek()
            if nxt.kind == "id":
                p = self._searchref(nxt.text)
                if p is not None and p.code == CODE_FIELD:
                    self.advance()
                    return self._parse_fields(p)
            types: list[TNode] = []
            while True:
                if self.peek().kind == "eof":
                    raise MtlError("Compiler : ']' expected (found EOF)")
                if self.peek().text == "]":
                    self.advance()
                    break
                types.append(self._parse_expression())
            nval = len(types)
            if 0 <= nval <= 255:
                self.fn.opb(Op.OPdeftabb, nval)
            else:
                self.fn.int_(nval)
                self.fn.op(Op.OPdeftab)
            return createnodetuple(types)
        if t.text == "{":
            tab = createnodetype(TYPENAME_TAB)
            elem = createnodetype(TYPENAME_UNDEF)
            tab.a = elem
            nval = 0
            while True:
                if self.peek().kind == "eof":
                    raise MtlError("Compiler : '}' expected (found EOF)")
                if self.peek().text == "}":
                    self.advance()
                    break
                tn = self._parse_expression()
                unif(tn, elem)
                nval += 1
            if 0 <= nval <= 255:
                self.fn.opb(Op.OPdeftabb, nval)
            else:
                self.fn.int_(nval)
                self.fn.op(Op.OPdeftab)
            return tab
        if t.text == "nil":
            self.fn.op(Op.OPnil)
            return createnodetype(TYPENAME_UNDEF)
        if t.kind == "int":
            self._emit_intb_or_int(t.value)
            return self._I()
        if t.kind == "str":
            # Strings become an anonymous global; reference it with OPgetglobal.
            idx = self.enc.add_global(t.value)
            self._emit_get_global(idx)
            return self._S()
        if t.text == "'":
            # Char literal: 'X' or '\n' etc. The C++ takes `token[0]`,
            # which means it grabs the first byte of the inner token —
            # so `'\n'` becomes the byte `\\`, not 10. Tested against
            # mtl_compiler; we match byte-for-byte.
            c_tok = self.advance()
            if c_tok.kind == "eof":
                raise MtlError("Compiler : 'char expected (found EOF)")
            ch = c_tok.text[0]
            self._emit_intb_or_int(ord(ch) & 0xFF)
            self.expect("'")
            return self._I()
        if t.text == "#":
            return self._parse_pntfun()
        if t.text == "if":
            return self._parse_if()
        if t.text == "let":
            return self._parse_let()
        if t.text == "set":
            return self._parse_set()
        if t.text == "while":
            return self._parse_while()
        if t.text == "for":
            return self._parse_for()
        if t.text == "match":
            return self._parse_match()
        if t.text == "call":
            return self._parse_call()
        if t.text == "update":
            raise MtlError("Compiler : 'update … with' is not supported by mtl_comp")
        if t.kind == "id":
            return self._parse_ref(t.text)
        raise MtlError(f"Compiler : unexpected term '{t.text}'")

    def _parse_pntfun(self) -> TNode:
        """compiler_term.cpp::parsepntfun — `#FUN`, a function pointer.

        A bytecode function is its funtable index (OPintb/OPint); a native
        is minus its opcode. Its type is an instance of the function's,
        shared when the function is the one being compiled.
        """
        name_tok = self.advance()
        if name_tok.kind == "eof":
            raise MtlError("Compiler : function name expected (found EOF)")
        p = self._searchref(name_tok.text) if name_tok.kind == "id" else None
        if p is not None and p.code >= 0:
            if p.native is None:
                v = p.package
            else:
                v = -self._native_opcode(p)
            self._emit_intb_or_int(v)
            if p is not self.newref:
                return copytype(p.type)
            return p.type
        raise MtlError(f"Compiler : function name expected (found '{name_tok.text}')")

    def _native_opcode(self, p: Ref) -> int:
        if isinstance(p.native, int):
            return p.native
        raise MtlError(
            f"Compiler : native '{p.name}' ({p.native}) has no opcode in this VM's table")

    # ---- if/while/for -----------------------------------------------------
    def _parse_if(self) -> TNode:
        """compiler_term.cpp::parseif"""
        cond = self._parse_expression()
        unif(cond, self._I())
        self.expect("then")
        else_label = self.new_label("else")
        end_label = self.new_label("endif")
        self.fn.else_(else_label)
        t1 = self._parse_expression()
        self.fn.goto(end_label)
        self.fn.label(else_label)
        if self.accept("else"):
            t2 = self._parse_expression()
        else:
            self.fn.op(Op.OPnil)
            t2 = createnodetype(TYPENAME_UNDEF)
        self.fn.label(end_label)
        unif(t2, t1)
        return t1

    def _parse_while(self) -> TNode:
        """while COND do BODY — returns the value of BODY's LAST iteration
        (or nil if it never ran).

        Codegen (matches compiler_term.cpp::parsewhile):
            OPnil                ; push initial result
            loop_start:
            <cond>
            OPelse end
            OPdrop               ; drop previous body result
            <body>
            OPgoto loop_start
            end:
        """
        loop_label = self.new_label("while")
        end_label = self.new_label("endwhile")
        self.fn.op(Op.OPnil)
        self.fn.label(loop_label)
        cond = self._parse_expression()
        unif(cond, self._I())
        self.expect("do")
        self.fn.else_(end_label)
        self.fn.drop()
        body = self._parse_expression()
        self.fn.goto(loop_label)
        self.fn.label(end_label)
        return body

    def _parse_for(self) -> TNode:
        """for VAR=INIT; COND; NEXT do BODY  — C-style for loop.

        Matches compiler_term.cpp::parsefor's emit order — the clever
        bit is that NEXT is emitted BEFORE the body in bytecode, with
        a goto-over-NEXT on the first iteration:

            <INIT>
            OPsetlocalb VAR
            OPnil                ; result accumulator
          loop_check:
            <COND>
            OPelse end
            OPgoto body          ; first iter skips NEXT
          next_block:
            <NEXT>
            OPdrop               ; drop NEXT's value
            OPgoto loop_check
          body:
            OPdrop               ; drop previous accumulator
            <BODY>
            OPgoto next_block    ; subsequent iters come back to NEXT
          end:
        """
        var_tok = self.advance()
        if var_tok.kind == "eof":
            raise MtlError("Compiler : label expected (found EOF)")
        if var_tok.kind != "id":
            raise MtlError(f"Compiler : label expected (found '{var_tok.text}')")
        self.expect("=")
        t_init = self._parse_expression()
        idx = self.scope.declare(var_tok.text, t_init)
        new_locals_after_args = (idx + 1) - self.fn.nargs
        if new_locals_after_args > self.nlocals_high_water:
            self.nlocals_high_water = new_locals_after_args
        self.fn.opb(Op.OPsetlocalb, idx)
        self.expect(";")
        # accumulator
        self.fn.op(Op.OPnil)
        loop_check = self.new_label("for_check")
        end_label = self.new_label("for_end")
        self.fn.label(loop_check)
        cond = self._parse_expression()     # condition
        unif(cond, self._I())
        self.fn.else_(end_label)

        # Two forms (matching compiler_term.cpp::parsefor):
        #   `for VAR=INIT; COND; NEXT do BODY`  (3 semicolons, Form 1)
        #   `for VAR=INIT; COND do BODY`        (2 semicolons, Form 2;
        #                                        auto-increment VAR by 1)
        if self.peek().text == ";":
            # Form 1.
            self.advance()
            body_label = self.new_label("for_body")
            next_label = self.new_label("for_next")
            self.fn.goto(body_label)
            self.fn.label(next_label)
            t_next = self._parse_expression()     # NEXT
            unif(t_next, t_init)
            # NEXT's value is STORED INTO THE LOOP VAR (i+1 → i=i+1).
            self.fn.opb(Op.OPsetlocalb, idx)
            self.fn.goto(loop_check)
            self.expect("do")
            self.fn.label(body_label)
            self.fn.drop()
            body = self._parse_expression()     # BODY
            self.fn.goto(next_label)
        else:
            # Form 2 — auto-increment by 1 after BODY.
            unif(t_init, self._I())
            self.expect("do")
            self.fn.drop()
            body = self._parse_expression()     # BODY
            self.fn.opb(Op.OPgetlocalb, idx)
            self.fn.intb(1)
            self.fn.op(Op.OPadd)
            self.fn.opb(Op.OPsetlocalb, idx)
            self.fn.goto(loop_check)

        self.fn.label(end_label)
        self.scope.unbind(var_tok.text)
        return body

    # ---- structs ----------------------------------------------------------
    def _parse_fields(self, p: Ref) -> TNode:
        """`[FIELD1: v1 FIELD2: v2 ...]` — struct creation. The opening
        `[` and the first field name have been consumed; `p` is that
        field's ref.

        Codegen (mirrors compiler_term.cpp::parsefields):
            OPmktabb N           ; create empty struct of total size N
            <v1>
            OPsetstructb idx1
            <v2>
            OPsetstructb idx2
            ...
            ; closing `]` is consumed below
        """
        type_ref: Ref = p.val[1]
        n = type_ref.val
        if 0 <= n <= 255:
            self.fn.opb(Op.OPmktabb, n)
        else:
            self.fn.int_(n)
            self.fn.op(Op.OPmktab)
        struct_t = copytype(type_ref.type)
        while True:
            ft = copytype(p.type)
            unif(struct_t, actualtype(argsfromfun(ft)).items[0])
            field_t = actualtype(ft).b
            self.expect(":")
            v = self._parse_expression()
            unif(v, field_t)
            num = p.val[0]
            if 0 <= num <= 255:
                self.fn.opb(Op.OPsetstructb, num)
            else:
                self.fn.int_(num)
                self.fn.op(Op.OPsetstruct)
            t = self.advance()
            if t.kind == "eof":
                raise MtlError("Compiler : ']' expected (found EOF)")
            if t.text == "]":
                return struct_t
            if t.kind == "id":
                q = self._searchref(t.text)
                if q is not None and q.code == CODE_FIELD:
                    p = q
                    continue
            raise MtlError(f"Compiler : ']' expected (found '{t.text}')")

    def _parse_getpoint(self, t: TNode) -> TNode:
        """compiler_term.cpp::parsegetpoint — `.X` chain after a reference:
        OPfetchb for a struct field, OPfetch for an index expression, typed
        through the field's `fun [Struct] T` or STDTYPE_fun__tab_u0_I__u0.
        """
        while self.peek().text == ".":
            self.advance()
            f = self.peek()
            if f.kind == "eof":
                raise MtlError("Compiler : expression or field name expected (found EOF)")
            p = self._searchref(f.text) if f.kind == "id" else None
            if p is not None and p.code == CODE_FIELD:
                self.advance()
                num = p.val[0]
                if 0 <= num <= 255:
                    self.fn.opb(Op.OPfetchb, num)
                else:
                    self.fn.int_(num)
                    self.fn.op(Op.OPfetch)
                ft = copytype(p.type)
                t = unif_argfun(ft, createnodetuple([t]))
            else:
                ti = self._parse_term()
                self.fn.op(Op.OPfetch)
                ft = copytype(self.stdtypes["tab"])
                t = unif_argfun(ft, createnodetuple([t, ti]))
        return t

    def _parse_call(self) -> TNode:
        """call FUN [arg1 arg2 ...]   — dynamic dispatch to a function value
           call FUN arg                — single-arg variant via OPcall

        Codegen (mirrors compiler_term.cpp::parsecall):
            <FUN>
            ; multi-arg form:
            <arg1>
            <arg2>
            ...
            OPcallrb n           (or OPint n; OPcallr if n > 255)
            ; single-arg form:
            <arg>
            OPcall
        Both typed through STDTYPE_fun__fun_u0_u1_u0__u1.
        """
        tf = self._parse_expression()                  # the function value
        if self.peek().text == "[":
            self.advance()
            types: list[TNode] = []
            while True:
                if self.peek().kind == "eof":
                    raise MtlError("Compiler : ']' expected (found EOF)")
                if self.peek().text == "]":
                    self.advance()
                    break
                types.append(self._parse_expression())
            nval = len(types)
            if 0 <= nval <= 255:
                self.fn.opb(Op.OPcallrb, nval)
            else:
                self.fn.int_(nval)
                self.fn.op(Op.OPcallr)
            args = createnodetuple([tf, createnodetuple(types)])
        else:
            ta = self._parse_expression()             # the single arg
            self.fn.op(Op.OPcall)
            args = createnodetuple([tf, ta])
        f = copytype(self.stdtypes["call"])
        return unif_argfun(f, args)

    # ---- match ------------------------------------------------------------
    def _parse_match(self) -> TNode:
        """match EXPR with (Cons1 -> body1) | (Cons2 x -> body2) | (_ -> default)

        Codegen (mirrors compiler_term.cpp::parsematch + parsematchcons):
          <EXPR>                       ; push the value-to-match
          ; For each case:
          OPfirst                      ; get tag (first elem of tuple)
          <push case tag>              ; emit OPintb tag
          OPeq
          OPelse <next_case>
          ; ---- CONS0 case: drop value entirely ----
          OPdrop
          <body>
          OPgoto <end>
          next_case:
          ; ---- CONS case (with payload): bind payload as local ----
          OPfetchb 1                   ; get payload
          OPsetlocalb i                ; bind to local
          <body>
          OPgoto <end>
          ; ---- _ wildcard case ----
          OPdrop                       ; drop the value
          <body>
          end:
        """
        src = self._parse_expression()        # value to match
        result = createnodetype(TYPENAME_UNDEF)
        self.expect("with")
        end_label = self.new_label("match_end")
        self._parse_matchcons(end_label, result, src)
        return result

    def _parse_matchcons(self, end_label: str, result: TNode, src: TNode) -> None:
        """One match case `( Cons args -> body )`, possibly followed by
        `| ( more cases )` recursively.
        """
        self.expect("(")
        head = self.advance()
        if head.kind == "eof":
            raise MtlError("Compiler : constructor expected (found EOF)")
        if head.text == "_":
            # Wildcard: drop the value and parse the body.
            self.fn.drop()
            self.expect("->")
            t = self._parse_program()
            unif(t, result)
            self.expect(")")
            self.fn.label(end_label)
            return

        p = self._searchref(head.text) if head.kind == "id" else None
        if p is None or p.code not in (CODE_CONS, CODE_CONS0):
            raise MtlError(f"Compiler : constructor expected (found '{head.text}')")
        next_case = self.new_label("match_case")
        self.fn.op(Op.OPfirst)             # get tag
        self._emit_intb_or_int(p.val)
        self.fn.op(Op.OPeq)
        self.fn.else_(next_case)

        # Inside the case: payload handling — the payload pattern is a
        # parselocals, so `Cons [a b]` and `Cons _` work as in the C++.
        bound_locals: list[str] = []
        if p.code == CODE_CONS:
            self.fn.opb(Op.OPfetchb, 1)
            tloc = self._parse_let_locals(bound_locals)
            args = createnodetuple([tloc])
        else:
            self.fn.drop()
            args = createnodetuple([])
        ft = copytype(p.type)
        tsrc = unif_argfun(ft, args)
        unif(tsrc, src)

        self.expect("->")
        t = self._parse_program()
        unif(t, result)
        self.expect(")")

        # Clean up the locals bound in this case.
        for nm in reversed(bound_locals):
            self.scope.unbind(nm)

        self.fn.goto(end_label)
        self.fn.label(next_case)

        if self.peek().text == "|":
            self.advance()
            self._parse_matchcons(end_label, result, src)
            return

        # No more cases — fall through to nil + end.
        self.fn.drop()
        self.fn.op(Op.OPnil)
        self.fn.label(end_label)

    # ---- let --------------------------------------------------------------
    def _parse_let(self) -> TNode:
        """let VALUE -> DEST in BODY — DEST is either:
            - a single NAME              (simple binding via OPsetlocalb)
            - `[a b _ c]` tuple pattern  (destructure each position)

        Matches the C++ codegen byte-for-byte (compiler_term.cpp::parselet +
        parselocals).
        """
        tsrc = self._parse_expression()
        self.expect("->")
        bound_names: list[str] = []
        tpat = self._parse_let_locals(bound_names)
        unif(tpat, tsrc)
        self.expect("in")
        t = self._parse_expression()
        # Unbind everything we declared (reverse order to keep
        # next_index decrements consistent with allocation order).
        for nm in reversed(bound_names):
            self.scope.unbind(nm)
        return t

    def _declare_local(self, name: str, tnode: TNode) -> int:
        idx = self.scope.declare(name, tnode)
        new_locals_after_args = (idx + 1) - self.fn.nargs
        if new_locals_after_args > self.nlocals_high_water:
            self.nlocals_high_water = new_locals_after_args
        return idx

    def _parse_let_locals(self, bound: list[str]) -> TNode:
        """compiler_term.cpp::parselocals — bind the value on the stack to a
        destination pattern. Appends the names introduced to `bound` (so
        the caller can unbind them) and returns the pattern's type.

        Supported destinations:
          - NAME            : single binding (OPsetlocalb), a fresh UNDEF
          - `[a b _ c]`     : tuple destructuring; each slot is OPdup
                              + OPfetchb i + recursive parselocals;
                              after all positions, OPdrop the value.
          - `_`             : drops the value (no binding)
        """
        t = self.advance()
        if t.kind == "eof":
            raise MtlError("Compiler : term expected (found EOF)")
        if t.text == "[":
            types: list[TNode] = []
            while True:
                nxt = self.advance()
                if nxt.kind == "eof":
                    raise MtlError("Compiler : ']' expected (found EOF)")
                if nxt.text == "]":
                    self.fn.drop()   # drop the original tuple
                    return createnodetuple(types)
                if nxt.text == "_":
                    types.append(createnodetype(TYPENAME_UNDEF))
                    continue
                self.pos -= 1
                n = len(types)
                self.fn.op(Op.OPdup)
                if 0 <= n <= 255:
                    self.fn.opb(Op.OPfetchb, n)
                else:
                    self.fn.int_(n)
                    self.fn.op(Op.OPfetch)
                types.append(self._parse_let_locals(bound))
        if t.text == "(":
            raise MtlError("Compiler : the (a :: b) list pattern is not supported by mtl_comp")
        if t.text == "_":
            self.fn.drop()
            return createnodetype(TYPENAME_UNDEF)
        if t.kind != "id":
            raise MtlError(f"Compiler : unexpected term '{t.text}'")
        node = createnodetype(TYPENAME_UNDEF)
        idx = self._declare_local(t.text, node)
        self.fn.opb(Op.OPsetlocalb, idx)
        bound.append(t.text)
        return node

    # ---- set --------------------------------------------------------------
    def _parse_set(self) -> TNode:
        """`set NAME = EXPR`              — simple var assignment
           `set NAME.field = EXPR`         — struct field write (OPstore)
           `set NAME.field.field = EXPR`   — chained field write

        compiler_term.cpp::parseset + parsesetpoint. Codegen for the field
        forms:
            <get NAME>              ; push the root object
            <OPfetchb field_idx>*   ; walk inner fields (n-1 of them)
            <push last_field_idx>
            <EXPR>                  ; push value
            OPstore                 ; consumes [struct, idx, value]
        """
        name_tok = self.advance()
        if name_tok.kind == "eof":
            raise MtlError("Compiler : reference expected (found EOF)")
        if name_tok.kind != "id":
            raise MtlError(f"Compiler : reference expected (found '{name_tok.text}')")
        name = name_tok.text

        if self.scope and (loc := self.scope.lookup(name)) is not None:
            target = self._parse_setpoint(True, loc, self.scope.lookup_type(name))
        else:
            p = self._searchref(name)
            if p is not None and p.code == CODE_VAR:
                target = self._parse_setpoint(False, p.package, p.type)
            elif p is not None and p.code == CODE_CONST:
                raise MtlError(f"Compiler : '{name}' is a const, it cannot be set")
            else:
                raise MtlError(f"Compiler : reference expected (found '{name}')")
        self.expect("=")
        t = self._parse_expression()
        self.fn.op(self._opstore)
        unif(t, target)
        return target

    def _parse_setpoint(self, local: bool, ind: int, t: TNode) -> TNode:
        """compiler_term.cpp::parsesetpoint — the write-side `.X` chain.
        Leaves the store opcode in self._opstore."""
        if self.peek().text != ".":
            self._emit_intb_or_int(ind)
            self._opstore = Op.OPsetlocal2 if local else Op.OPsetglobal
            return t
        if local:
            self.fn.opb(Op.OPgetlocalb, ind)
        else:
            self._emit_get_global(ind)
        self._opstore = Op.OPstore
        # Walk the .X chain. For each step but the LAST, emit a fetch
        # so the next step indexes into the inner object. For the last,
        # leave the index on the stack, then EXPR, then OPstore.
        #
        # A step is either a declared field name (static index) or a
        # computed index — `set md5bloc.(j*2) = …` (boot line 466) and
        # `set ears.i = …` (line 6160). Same single-namespace rule as
        # the read path: a step that is not a declared field name IS an
        # index expression. The reference emits that expression inline
        # with no OPintb in front (0x0DBD..0x0DDB), so a computed last
        # step needs nothing pushed after it.
        while True:
            self.advance()                      # the '.'
            f = self.peek()
            if f.kind == "eof":
                raise MtlError("Compiler : expression or field name expected (found EOF)")
            static_idx: int | None = None
            p = self._searchref(f.text) if f.kind == "id" else None
            if p is not None and p.code == CODE_FIELD:
                self.advance()
                static_idx = p.val[0]
                ft = copytype(p.type)
                t = unif_argfun(ft, createnodetuple([t]))
            else:
                ti = self._parse_term()
                ft = copytype(self.stdtypes["tab"])
                t = unif_argfun(ft, createnodetuple([t, ti]))
            if self.peek().text != ".":
                if static_idx is not None:
                    self._emit_intb_or_int(static_idx)
                return t
            if static_idx is None:
                self.fn.op(Op.OPfetch)
            elif 0 <= static_idx <= 255:
                self.fn.opb(Op.OPfetchb, static_idx)
            else:
                self.fn.int_(static_idx)
                self.fn.op(Op.OPfetch)

    # ---- identifier reference: var read OR function call ---------------
    def _parse_ref(self, name: str) -> TNode:
        """compiler_term.cpp::parseref — locals first, then the package
        (user declarations shadow natives: boot.mtl's own `fun itobin2`
        overrides the builtin opcode)."""
        if self.scope and (loc := self.scope.lookup(name)) is not None:
            self.fn.opb(Op.OPgetlocalb, loc)
            return self._parse_getpoint(self.scope.lookup_type(name))
        p = self._searchref(name)
        if p is not None:
            if p.code == CODE_CONS0:
                self._emit_intb_or_int(p.val)
                self.fn.opb(Op.OPdeftabb, 1)
                return copytype(actualtype(p.type).b)
            if p.code == CODE_CONS:
                self._emit_intb_or_int(p.val)
                t = self._parse_expression()
                self.fn.opb(Op.OPdeftabb, 2)
                ft = copytype(p.type)
                return unif_argfun(ft, createnodetuple([t]))
            if p.code >= 0:
                types: list[TNode] = []
                for _ in range(p.code):
                    try:
                        types.append(self._parse_expression())
                    except MtlError as e:
                        raise e.add(f"Compiler : function requires {p.code} arguments")
                if p.native is not None:
                    self.fn.op(self._native_opcode(p))
                else:
                    self._emit_intb_or_int(p.package)
                    self.fn.op(Op.OPexec)
                ft = copytype(p.type) if p is not self.newref else p.type
                return unif_argfun(ft, createnodetuple(types))
            if p.code in (CODE_VAR, CODE_CONST):
                self._emit_get_global(p.package)
                return self._parse_getpoint(p.type)
        raise MtlError(f"Compiler : unknown label '{name}'")

    # ---- Emitter helpers --------------------------------------------------
    def _emit_intb_or_int(self, val: int) -> None:
        if 0 <= val <= 255:
            self.fn.intb(val)
        else:
            self.fn.int_(val)

    def _emit_get_global(self, idx: int) -> None:
        if 0 <= idx <= 255:
            self.fn.opb(Op.OPgetglobalb, idx)
        else:
            self.fn.int_(idx)
            self.fn.op(Op.OPgetglobal)


def wire_wrap(blob: bytes) -> bytes:
    """Wrap a bytecode body in the envelope `mtl_compiler` writes around it:
    `"amber" + size_hex(8) + body + "Mind"`, 17 bytes in all. The reader side
    is boot.mtl::getbytecode, which does `strsub content 13 len`.

    This is the COMPILER's output format, not something the serving layer
    adds. Two measurements say so: `bytecode/build-bytecode.sh` invokes the
    C++ compiler as `"$CC" -s bootcode.mtl "$OUT"` with nothing between it
    and the file, and that file is the 105 394-byte envelope-carrying blob;
    and `proto main 0;; fun main= 42;;` compiled by the real mtl_compiler is
    37 bytes — a 20-byte body plus these 17. mtl_dis.py's _strip_amber_wrapper
    docstring blames /vl/bc.jsp for it; that attribution is wrong, the jsp
    only passes it through.

    So this is the default for mtl_comp output, and --bare is the opt-out.
    An envelope-less file dropped into /data is not what the rabbit will
    accept. mtl_dis/mtl_asm are the other way round on purpose — see the
    README's note on which tool deals in which form.
    """
    return b"amber" + b"%08x" % len(blob) + blob + b"Mind"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("input", help=".mtl source file to compile")
    ap.add_argument("-o", "--output", help="output .bin (default: INPUT.bin)")
    ap.add_argument(
        "--bare", action="store_true",
        help="emit the bytecode body alone, without the 17-byte "
             "amber/Mind envelope mtl_compiler normally writes around it. "
             "For feeding mtl_dis/mtl_asm, not for serving to a rabbit.",
    )
    ap.add_argument(
        "--types", action="store_true",
        help="print the `Compiler : name : type` line of every declaration, "
             "as the C++ mtl_compiler does while it compiles",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="type-check only: write nothing",
    )
    args = ap.parse_args()
    src_path = Path(args.input)
    out_path = Path(args.output) if args.output else src_path.with_suffix(".bin")
    c = Compiler()
    try:
        # IMPORTANT: read in binary + decode latin-1 to preserve original
        # line endings (CRLF in particular) — Python's read_text() does
        # universal-newlines translation, which the C++ does NOT. Strings
        # in the source can intentionally contain \r\n that must round-trip.
        blob = c.compile_source(src_path.read_bytes().decode("latin-1"))
    except MtlError as e:
        if args.types:
            for line in c.type_lines:
                print(line)
        for line in e.lines:
            print(line, file=sys.stderr)
        print("compile error: no output written", file=sys.stderr)
        return 1
    if args.types:
        for line in c.type_lines:
            print(line)
    if args.check:
        return 0
    if not args.bare:
        blob = wire_wrap(blob)
    out_path.write_bytes(blob)
    print(f"wrote {len(blob)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
