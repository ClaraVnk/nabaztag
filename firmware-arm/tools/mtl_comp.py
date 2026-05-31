#!/usr/bin/env python3
"""Phase 7a: Metal source compiler — `.mtl` -> `.bin`, byte-identical to the
C++ `mtl_compiler` for the supported subset.

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
bytecode. This frontend mirrors enough of the C++ parser to compile
useful programs and emits the same opcodes/operands the C++ compiler
would emit, byte-for-byte.

### Supported subset

Top-level:
    proto NAME ARITY;;
    var NAME [= EXPR];;
    const NAME = EXPR;;
    fun NAME [arg1 arg2 ...] = body;;

Expressions:
    integer literals (decimal, hex 0x..)
    string literals "..."
    nil
    identifier (variable / fun / builtin)
    BUILTIN_OR_FUN arg1 arg2 ...  (juxtaposition = call)
    EXPR ;  EXPR              (sequence, returns last)
    EXPR &&  EXPR             (logical and with short-circuit)
    EXPR ||  EXPR             (logical or with short-circuit)
    EXPR == != < > <= >= EXPR (comparison)
    EXPR + - * / % & | ^ << >> EXPR (arithmetic & bitwise)
    ! EXPR                    (logical not)
    - EXPR                    (negation)
    ~ EXPR                    (bitwise not)
    if EXPR then EXPR [else EXPR]
    let NAME -> EXPR in EXPR  (binding)
    set NAME = EXPR           (assignment; NAME must be a var)
    ( EXPR )                  (grouping)

### Not yet supported

::, [tuples], {arrays}, struct/sum/match, while, for, char literals 'a',
#funptr, type annotations, multi-file includes, ifdef. These come
incrementally.

Usage:

    python3 mtl_comp.py program.mtl -o program.bin
    python3 mtl_comp.py program.mtl --dump-masm > program.masm  # for inspection
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


# ---------------------------------------------------------------------------
# Builtin table — mnemonic name → (opcode, nargs).
# Mirrors stdlib_core.cpp `corename[]` × `coreval[]` × `corecode[]`.
# Keep in sync when upstream adds opcodes.
# ---------------------------------------------------------------------------
# (name, opcode, nargs)
BUILTINS: dict[str, tuple[int, int]] = {
    "hd":             (Op.OPhd,        1),
    "tl":             (Op.OPtl,        1),
    "Secholn":        (Op.OPSecholn,   1),
    "Secho":          (Op.OPSecho,     1),
    "Iecholn":        (Op.OPIecholn,   1),
    "Iecho":          (Op.OPIecho,     1),
    "time_ms":        (Op.OPtime_ms,   0),
    "tabnew":         (Op.OPtabnew,    2),
    "abs":            (Op.OPabs,       1),
    "min":            (Op.OPmin,       2),
    "max":            (Op.OPmax,       2),
    "rand":           (Op.OPrand,      0),
    "srand":          (Op.OPsrand,     1),
    "time":           (Op.OPtime,      0),
    "strnew":         (Op.OPstrnew,    1),
    "strset":         (Op.OPstrset,    3),
    "strcpy":         (Op.OPstrcpy,    5),
    "vstrcmp":        (Op.OPvstrcmp,   5),
    "strfind":        (Op.OPstrfind,   5),
    "strfindrev":     (Op.OPstrfindrev, 5),
    "strlen":         (Op.OPstrlen,    1),
    "strget":         (Op.OPstrget,    2),
    "strsub":         (Op.OPstrsub,    3),
    "strcat":         (Op.OPstrcat,    2),
    "tablen":         (Op.OPtablen,    1),
    "strcatlist":     (Op.OPstrcatlist, 1),
    "led":            (Op.OPled,       2),
    "motorset":       (Op.OPmotorset,  2),
    "motorget":       (Op.OPmotorget,  1),
    "button2":        (Op.OPbutton2,   0),
    "button3":        (Op.OPbutton3,   0),
    "playStart":      (Op.OPplayStart, 2),
    "playFeed":       (Op.OPplayFeed,  3),
    "playStop":       (Op.OPplayStop,  0),
    "recStart":       (Op.OPrecStart,  3),
    "recStop":        (Op.OPrecStop,   0),
    "recVol":         (Op.OPrecVol,    2),
    "load":           (Op.OPload,      5),
    "gc":             (Op.OPgc,        0),
    "save":           (Op.OPsave,      5),
    "bytecode":       (Op.OPbytecode,  1),
    "loopcb":         (Op.OPloopcb,    1),
    "udpStart":       (Op.OPudpStart,  1),
    "udpCb":          (Op.OPudpCb,     1),
    "udpStop":        (Op.OPudpStop,   1),
    "udpSend":        (Op.OPudpSend,   6),  # capital S per stdlib_core.cpp
    "tcpOpen":        (Op.OPtcpOpen,   2),
    "tcpClose":       (Op.OPtcpClose,  1),
    "tcpSend":        (Op.OPtcpSend,   4),
    "tcpCb":          (Op.OPtcpCb,     1),
    "tcpListen":      (Op.OPtcpListen, 2),
    "tcpEnable":      (Op.OPtcpEnable, 2),
    "envget":         (Op.OPenvget,    0),
    "envset":         (Op.OPenvset,    1),
    "sndVol":         (Op.OPsndVol,    1),
    "rfidGet":        (Op.OPrfidGet,   0),
    "playTime":       (Op.OPplayTime,  0),
    "netCb":          (Op.OPnetCb,     1),
    "netSend":        (Op.OPnetSend,   6),
    "netState":       (Op.OPnetState,  0),
    "netMac":         (Op.OPnetMac,    0),
    "netChk":         (Op.OPnetChk,    4),
    "netSetmode":     (Op.OPnetSetmode, 3),
    "netScan":        (Op.OPnetScan,   1),
    "netAuth":        (Op.OPnetAuth,   4),
    "netPmk":         (Op.OPnetPmk,    2),
    "netRssi":        (Op.OPnetRssi,   0),
    "netSeqAdd":      (Op.OPnetSeqAdd, 2),
    "strgetword":     (Op.OPstrgetword, 2),
    "strputword":     (Op.OPstrputword, 3),
    "atoi":           (Op.OPatoi,      1),
    "htoi":           (Op.OPhtoi,      1),
    "itoa":           (Op.OPitoa,      1),
    "ctoa":           (Op.OPctoa,      1),
    "itoh":           (Op.OPitoh,      1),
    "ctoh":           (Op.OPctoh,      1),
    "itobin2":        (Op.OPitobin2,   1),
    "listswitch":     (Op.OPlistswitch, 2),
    "listswitchstr":  (Op.OPlistswitchstr, 2),
    "sndRefresh":     (Op.OPsndRefresh, 0),
    "sndWrite":       (Op.OPsndWrite,  2),
    "sndRead":        (Op.OPsndRead,   1),
    "sndFeed":        (Op.OPsndFeed,   3),
    "sndAmpli":       (Op.OPsndAmpli,  1),
    "corePP":         (Op.OPcorePP,    0),
    "corePush":       (Op.OPcorePush,  1),
    "corePull":       (Op.OPcorePull,  1),
    "coreBit0":       (Op.OPcoreBit0,  2),
    "reboot":         (Op.OPreboot,    2),
    "strcmp":         (Op.OPstrcmp,    2),
    "flashFirmware":  (Op.OPflashFirmware, 3),
    "verifySig":      (Op.OPverifySig, 2),
    "rfidGetList":    (Op.OPrfidGetList, 0),
    "rfidRead":       (Op.OPrfidRead,  2),
    "rfidWrite":      (Op.OPrfidWrite, 3),
    "i2cRead":        (Op.OPi2cRead,   2),
    "i2cWrite":       (Op.OPi2cWrite,  3),
    "adp2wav":        (Op.OPadp2wav,   5),
    "wav2adp":        (Op.OPwav2adp,   5),
    "alaw2wav":       (Op.OPalaw2wav,  6),
    "wav2alaw":       (Op.OPwav2alaw,  6),
    "crypt":          (Op.OPcrypt,     5),
    "uncrypt":        (Op.OPuncrypt,   5),
    # fixarg1..fixarg8 all map to OPfixarg with arity 2 (per
    # stdlib_core.cpp /*5*/: 2,2,2,2,2,2,2,2). They differ only in
    # the type system — same bytecode emission.
    "fixarg1":        (Op.OPfixarg,    2),
    "fixarg2":        (Op.OPfixarg,    2),
    "fixarg3":        (Op.OPfixarg,    2),
    "fixarg4":        (Op.OPfixarg,    2),
    "fixarg5":        (Op.OPfixarg,    2),
    "fixarg6":        (Op.OPfixarg,    2),
    "fixarg7":        (Op.OPfixarg,    2),
    "fixarg8":        (Op.OPfixarg,    2),
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
                    if nxt == "n":   out_str.append(10); j += 2; continue
                    if nxt == "t":   out_str.append(9);  j += 2; continue
                    if nxt == "r":   out_str.append(13); j += 2; continue
                    if nxt == "\\":  out_str.append(ord("\\")); j += 2; continue
                    if nxt == '"':   out_str.append(ord('"')); j += 2; continue
                    if nxt == "0":
                        # Look for a multi-digit decimal escape like \255 or
                        # \13 — the source code uses these for byte values.
                        end = j + 2
                        while end < n and src[end].isdigit():
                            end += 1
                        out_str.append(int(src[j + 1:end]))
                        j = end
                        continue
                    if nxt.isdigit():
                        end = j + 2
                        while end < n and src[end].isdigit():
                            end += 1
                        out_str.append(int(src[j + 1:end]) & 0xFF)
                        j = end
                        continue
                    if nxt == "$" and j + 3 < n:
                        out_str.append(int(src[j + 2:j + 4], 16))
                        j += 4
                        continue
                out_str.append(ord(src[j]))
                j += 1
            if j >= n:
                raise SyntaxError(f"line {line}: unterminated string")
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
                    raise SyntaxError(
                        f"line {line}: float literals not yet supported"
                    )
            tok_text = src[i:j]
            if only_digits:
                out.append(Token("int", tok_text, int(tok_text), line, col))
            elif tok_text.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in tok_text[2:]):
                out.append(Token("int", tok_text, int(tok_text, 16), line, col))
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
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    """Lexical scope for `let`/`for` locals. Each entry: name -> stack offset
    (a 0-based call-frame index used by OPgetlocalb/OPsetlocalb).
    """
    names: dict[str, int] = field(default_factory=dict)
    next_index: int = 0

    def declare(self, name: str) -> int:
        idx = self.next_index
        self.names[name] = idx
        self.next_index += 1
        return idx

    def lookup(self, name: str) -> int | None:
        return self.names.get(name)


class Compiler:
    def __init__(self) -> None:
        self.enc = Encoder()
        # Symbol tables.
        self.globals_by_name: dict[str, int] = {}    # var/const name -> globals index
        self.funs_by_name: dict[str, tuple[int, int]] = {}  # fun name -> (funtable idx, nargs)
        self.proto_by_name: dict[str, tuple[int, int]] = {}  # proto name -> (idx, nargs)
        # Sum-type constructors: name -> (tag, has_payload).
        # `type Wifi = initW | gomasterW _ | stationW _;;` registers:
        #   initW       -> (0, False)
        #   gomasterW   -> (1, True)
        #   stationW    -> (2, True)
        self.constructors: dict[str, tuple[int, bool]] = {}
        # Sum-type names themselves — recorded so `ifdef Wifi { ... }`
        # can see them.
        self.types: set[str] = set()
        # Function-being-compiled state.
        self.fn: FunctionBody | None = None
        self.scope: Scope | None = None
        self.label_counter = 0
        # Internal helpers.
        self.tokens: list[Token] = []
        self.pos = 0
        # Track total nlocals declared by `let` so we can set fn.nlocals at
        # the end.
        self.nlocals_high_water = 0

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
            raise SyntaxError(
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

    # ---- Top-level: parseprogram ----------------------------------------
    def compile_source(self, src: str) -> bytes:
        self.tokens = lex(src)
        self.pos = 0
        # Walk top-level declarations.
        while self.peek().kind != "eof":
            self._top_decl()
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
            raise SyntaxError(
                f"line {t.line}: expected top-level declaration "
                f"(proto/var/const/fun/type/ifdef/ifndef), got {t.text!r}"
            )

    def _is_defined(self, name: str) -> bool:
        """Mirror of the C++'s `searchref || searchtype` check used by
        `ifdef NAME { ... }` to decide which branch is active."""
        return (
            name in self.globals_by_name
            or name in self.funs_by_name
            or name in self.proto_by_name
            or name in self.constructors
            or name in self.types
        )

    def _ifdef_decl(self, *, invert: bool) -> None:
        """`ifdef NAME { ... } [else { ... }]`  (or `ifndef NAME`).

        Top-level only. The active branch is parsed as a sequence of
        top-level decls; the inactive branch's tokens are skipped by
        matching brace nesting (just like compiler_file.cpp::skipifdef).
        """
        self.advance()  # consume ifdef/ifndef
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise SyntaxError(
                f"line {name_tok.line}: ifdef needs a name, got {name_tok.text!r}"
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
                raise SyntaxError("unexpected EOF inside ifdef block")
            self._top_decl()

    def _skip_braces(self) -> None:
        """Skip tokens until the matching `}` (counting nesting)."""
        depth = 1
        while depth > 0:
            t = self.advance()
            if t.kind == "eof":
                raise SyntaxError("unexpected EOF inside ifdef block")
            if t.text == "{":
                depth += 1
            elif t.text == "}":
                depth -= 1

    def _type_decl(self) -> None:
        """`type Name = Cons1 | Cons2 _ | Cons3 [type] | ...;;`

        Each constructor gets an auto-incrementing tag. A bare name
        (no payload) is CODE_CONS0; with `_` or an explicit type
        argument it's CODE_CONS (1 payload slot).
        """
        self.expect("type")
        # Type-name (e.g. `Wifi`) — record so `ifdef Wifi` can see it.
        type_name = self.advance().text
        self.types.add(type_name)
        self.expect("=")
        tag = 0
        while True:
            cons_tok = self.advance()
            if cons_tok.kind != "id":
                raise SyntaxError(
                    f"line {cons_tok.line}: constructor name expected, got {cons_tok.text!r}"
                )
            # Look ahead: is the next token `|` or `;;`? If so, this is
            # a CODE_CONS0 (no payload). Otherwise it's CODE_CONS with
            # a payload type (we skip the type — we don't typecheck yet,
            # but we must consume the type tokens).
            nxt = self.peek().text
            if nxt in ("|", ";;"):
                self.constructors[cons_tok.text] = (tag, False)
            else:
                # Consume the type expression. The C++ creategraph reads
                # a full type tree — for now we just skip tokens until
                # we hit `|` or `;;`. This matches Metal's grammar for
                # the simple case `Cons _` (the underscore) and for
                # `Cons SomeType` (single type name).
                while self.peek().text not in ("|", ";;"):
                    self.advance()
                self.constructors[cons_tok.text] = (tag, True)
            tag += 1
            if self.peek().text == ";;":
                self.advance()
                return
            self.expect("|")

    def _proto_decl(self) -> None:
        self.expect("proto")
        name = self.advance().text
        nargs_tok = self.advance()
        if nargs_tok.kind != "int":
            raise SyntaxError(f"line {nargs_tok.line}: arity must be an int")
        nargs = nargs_tok.value
        self.expect(";;")
        # Reserve a fun index for this name; the matching `fun NAME = ...`
        # later fills the slot.
        # mtl_compiler also stores a forward index here — we mirror that.
        idx = len(self.enc._functions)  # next-free funtable index
        # Reserve by adding a placeholder FunctionBody we'll overwrite.
        placeholder = self.enc.new_function(nargs=nargs, nlocals=0)
        # Stop the encoder from finalizing a function that has no body —
        # we mark the placeholder so the matching `fun` definition will
        # populate it. The simplest hack: keep `idx` reserved, but the
        # matching `fun NAME = ...` will reset placeholder.code.
        self.proto_by_name[name] = (idx, nargs)
        self.funs_by_name[name] = (idx, nargs)

    def _var_decl(self, *, is_const: bool) -> None:
        self.advance()  # var | const
        name = self.advance().text
        # var NAME = EXPR;; or var NAME;;
        initial: Any = NIL
        if self.accept("="):
            initial = self._const_expr()
        self.expect(";;")
        idx = self.enc.add_global(initial)
        self.globals_by_name[name] = idx

    def _const_expr(self) -> Any:
        """Constant expression at top level — int, string, or nil only.
        (No arithmetic on globals at decl-site, matches the C++ subset
        we care about right now.)"""
        t = self.advance()
        if t.kind == "int":
            return t.value
        if t.text == "-" and self.peek().kind == "int":
            return -self.advance().value
        if t.kind == "str":
            return t.value
        if t.text == "nil":
            return NIL
        raise SyntaxError(
            f"line {t.line}: only int/string/nil constants supported "
            f"in global initializers, got {t.text!r}"
        )

    def _fun_decl(self) -> None:
        self.expect("fun")
        name = self.advance().text
        # Collect arg names until `=`.
        args: list[str] = []
        while self.peek().text != "=" and self.peek().kind != "eof":
            args.append(self.advance().text)
        self.expect("=")

        # Resolve fun index: if proto'd, use that slot; else allocate fresh.
        if name in self.proto_by_name:
            idx, expected_args = self.proto_by_name[name]
            if expected_args != len(args):
                raise SyntaxError(
                    f"fun {name}: arity {len(args)} doesn't match proto {expected_args}"
                )
            fn = self.enc._functions[idx]
            fn.nargs = len(args)
            fn.code = bytearray()  # clear placeholder
        else:
            fn = self.enc.new_function(nargs=len(args), nlocals=0)
            idx = len(self.enc._functions) - 1
            self.funs_by_name[name] = (idx, len(args))

        # Set up scope: args are locals 0..N-1.
        self.fn = fn
        self.scope = Scope()
        for a in args:
            self.scope.declare(a)
        self.nlocals_high_water = 0

        # Parse the body as a `parseprogram` (sequences with `;`).
        self._parse_program()

        # Body ends — emit OPret.
        self.fn.ret()
        # nlocals = how many `let`-introduced locals beyond the args.
        self.fn.nlocals = self.nlocals_high_water

        self.expect(";;")
        self.fn = None
        self.scope = None

    # ---- Expression parser ------------------------------------------------
    def _parse_program(self) -> None:
        """parseprogram  := expression ( ; expression )*

        Like the C++: each `;` drops the previous value, leaving the last
        expression's value on the stack.
        """
        self._parse_expression()
        while True:
            if self.peek().text != ";":
                # `;;` is tokenized as a single token, end-of-decl.
                break
            self.advance()  # consume `;`
            self.fn.drop()  # OPdrop on previous value
            self._parse_expression()

    def _parse_expression(self) -> None:
        """expression := arithm ( :: expression )?  — cons is right-associative.

        `A :: B` builds a 2-tuple `[A, B]` (the standard cons cell).
        The right side is parsed recursively so `A :: B :: nil` parses
        as `A :: (B :: nil)`.
        """
        self._parse_arithm()
        if self.peek().text == "::":
            self.advance()
            self._parse_expression()
            self.fn.opb(Op.OPdeftabb, 2)

    def _parse_arithm(self) -> None:
        """arithm := a1 ( (&& | ||) a1 )*  (short-circuit)"""
        self._parse_a1()
        while True:
            t = self.peek()
            if t.text not in ("&&", "||"):
                return
            self.advance()
            # Compiler emits:
            #   dup
            #   if op is '||':  non       (flip the test for OR)
            #   else  (no op for AND)
            #   *Wait — C++ emits `if op == &&` do NOTHING extra,
            #   if op == ||  emits OPnon (logical not). Both then emit OPelse.
            self.fn.op(Op.OPdup)
            if t.text == "||":
                self.fn.op(Op.OPnon)
            skip_label = self.new_label("L")
            self.fn.else_(skip_label)
            self.fn.drop()
            self._parse_a1()
            self.fn.label(skip_label)

    def _parse_a1(self) -> None:
        """a1 := '!' a1 | a2"""
        if self.peek().text == "!":
            self.advance()
            self._parse_a1()
            self.fn.op(Op.OPnon)
            return
        self._parse_a2()

    def _parse_a2(self) -> None:
        """a2 := a3 ( ( == != < > <= >= ) a3 )*"""
        self._parse_a3()
        while True:
            t = self.peek()
            op = {
                "==": Op.OPeq, "!=": Op.OPne,
                "<": Op.OPlt, ">": Op.OPgt,
                "<=": Op.OPle, ">=": Op.OPge,
            }.get(t.text)
            if op is None:
                return
            self.advance()
            self._parse_a3()
            self.fn.op(op)

    def _parse_a3(self) -> None:
        """a3 := a4 ( ( + - ) a4 )*"""
        self._parse_a4()
        while True:
            t = self.peek()
            op = {"+": Op.OPadd, "-": Op.OPsub}.get(t.text)
            if op is None:
                return
            self.advance()
            self._parse_a4()
            self.fn.op(op)

    def _parse_a4(self) -> None:
        """a4 := a5 ( ( * / % ) a5 )*"""
        self._parse_a5()
        while True:
            t = self.peek()
            op = {"*": Op.OPmul, "/": Op.OPdiv, "%": Op.OPmod}.get(t.text)
            if op is None:
                return
            self.advance()
            self._parse_a5()
            self.fn.op(op)

    def _parse_a5(self) -> None:
        """a5 := a6 ( ( & | ^ << >> ) a6 )*"""
        self._parse_a6()
        while True:
            t = self.peek()
            op = {
                "&": Op.OPand, "|": Op.OPor, "^": Op.OPeor,
                "<<": Op.OPshl, ">>": Op.OPshr,
            }.get(t.text)
            if op is None:
                return
            self.advance()
            self._parse_a6()
            self.fn.op(op)

    def _parse_a6(self) -> None:
        """a6 := '-' int | '-' a6 | '~' a6 | term"""
        t = self.peek()
        if t.text == "-":
            # `- INT` is a NEGATIVE LITERAL (special-cased by C++ to emit
            # one OPint instead of OPintb+OPneg).
            if self.peek(1).kind == "int":
                self.advance()
                val = -self.advance().value
                self.fn.int_(val)
                return
            self.advance()
            self._parse_a6()
            self.fn.op(Op.OPneg)
            return
        if t.text == "~":
            self.advance()
            self._parse_a6()
            self.fn.op(Op.OPnot)
            return
        self._parse_term()

    def _parse_term(self) -> None:
        """term := ( prog ) | [ tuple ] | nil | int | string | char | #funptr |
                   identifier | if | let | set"""
        t = self.advance()
        if t.text == "(":
            self._parse_program()
            self.expect(")")
            return
        if t.text in ("[", "{"):
            # n-tuple `[a b c]` or array `{a b c}` — the C++ emits the
            # same OPdeftabb sequence for both. Only the type system
            # differs (tabs are mutable, tuples aren't) — for byte-
            # identical output that doesn't matter to us.
            closer = "]" if t.text == "[" else "}"
            nval = 0
            while True:
                if self.peek().text == closer:
                    self.advance()
                    break
                self._parse_expression()
                nval += 1
            if 0 <= nval <= 255:
                self.fn.opb(Op.OPdeftabb, nval)
            else:
                self.fn.int_(nval)
                self.fn.op(Op.OPdeftab)
            return
        if t.text == "nil":
            self.fn.op(Op.OPnil)
            return
        if t.kind == "int":
            self._emit_intb_or_int(t.value)
            return
        if t.kind == "str":
            # Strings become an anonymous global; reference it with OPgetglobal.
            idx = self.enc.add_global(t.value)
            self._emit_get_global(idx)
            return
        if t.text == "'":
            # Char literal: 'X' or '\n' etc. The C++ takes `token[0]`,
            # which means it grabs the first byte of the inner token —
            # so `'\n'` becomes the byte `\\`, not 10. Tested against
            # mtl_compiler; we match byte-for-byte.
            c_tok = self.advance()
            if c_tok.kind == "eof":
                raise SyntaxError(f"line {t.line}: unterminated char literal")
            ch = c_tok.text[0]
            self._emit_intb_or_int(ord(ch) & 0xFF)
            self.expect("'")
            return
        if t.text == "#":
            # #FUN is a function pointer (used for loopcb, regudp etc.).
            # Emit as OPint <fun_idx> + create the [fun_idx, NIL]-shaped
            # tuple the runtime expects (so callsites are uniform).
            name_tok = self.advance()
            if name_tok.kind != "id":
                raise SyntaxError(
                    f"line {t.line}: #FUN expects a function name"
                )
            self._parse_funptr(name_tok.text)
            return
        if t.text == "if":
            self._parse_if()
            return
        if t.text == "let":
            self._parse_let()
            return
        if t.text == "set":
            self._parse_set()
            return
        if t.text == "while":
            self._parse_while()
            return
        if t.text == "for":
            self._parse_for()
            return
        if t.text == "match":
            self._parse_match()
            return
        if t.text == "call":
            self._parse_call()
            return
        if t.kind == "id":
            self._parse_ref(t.text)
            return
        raise SyntaxError(
            f"line {t.line}: unexpected term {t.text!r}"
        )

    def _parse_funptr(self, name: str) -> None:
        """Emit code for `#FUN` — a function pointer literal.

        For a plain unbound function reference, the C++ compiler emits
        just the raw funtable index (OPintb/OPint). OPexec accepts both
        a raw-int index (regular calls) and a tuple [idx, captured]
        (closures); for `#FUN` with no captures, the index alone is
        enough and matches the C++ output byte-for-byte.
        """
        if name in self.funs_by_name:
            fun_idx, _nargs = self.funs_by_name[name]
        elif name in BUILTINS:
            # The C++ also handles builtins here, but they're a small
            # minority of real `#` use — leave for later.
            raise SyntaxError(
                f"#{name}: function pointer to builtins not yet supported"
            )
        else:
            raise SyntaxError(f"#{name}: unknown function")
        self._emit_intb_or_int(fun_idx)

    # ---- if/let/set ------------------------------------------------------
    def _parse_if(self) -> None:
        # condition
        self._parse_expression()
        self.expect("then")
        else_label = self.new_label("else")
        end_label = self.new_label("endif")
        self.fn.else_(else_label)
        self._parse_expression()
        self.fn.goto(end_label)
        self.fn.label(else_label)
        if self.accept("else"):
            self._parse_expression()
        else:
            self.fn.op(Op.OPnil)
        self.fn.label(end_label)

    def _parse_while(self) -> None:
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
        self._parse_expression()
        self.expect("do")
        self.fn.else_(end_label)
        self.fn.drop()
        self._parse_expression()
        self.fn.goto(loop_label)
        self.fn.label(end_label)

    def _parse_for(self) -> None:
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
        if var_tok.kind != "id":
            raise SyntaxError(f"line {var_tok.line}: for needs a label")
        self.expect("=")
        self._parse_expression()
        idx = self.scope.declare(var_tok.text)
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
        self._parse_expression()     # condition
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
            self._parse_expression()     # NEXT
            # NEXT's value is STORED INTO THE LOOP VAR (i+1 → i=i+1).
            self.fn.opb(Op.OPsetlocalb, idx)
            self.fn.goto(loop_check)
            self.expect("do")
            self.fn.label(body_label)
            self.fn.drop()
            self._parse_expression()     # BODY
            self.fn.goto(next_label)
        else:
            # Form 2 — auto-increment by 1 after BODY.
            self.expect("do")
            self.fn.drop()
            self._parse_expression()     # BODY
            self.fn.opb(Op.OPgetlocalb, idx)
            self.fn.intb(1)
            self.fn.op(Op.OPadd)
            self.fn.opb(Op.OPsetlocalb, idx)
            self.fn.goto(loop_check)

        self.fn.label(end_label)
        del self.scope.names[var_tok.text]
        self.scope.next_index -= 1

    def _parse_call(self) -> None:
        """call FUN [arg1 arg2 ...]   — dynamic dispatch to a function value
           call FUN arg                — single-arg variant via OPcall

        FUN is any expression that produces a function-shaped value
        (e.g. from `#funname` or a stored variable). Used in boot.mtl
        for callbacks dispatched at runtime.

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
        """
        self._parse_expression()                  # the function value
        if self.peek().text == "[":
            self.advance()
            nval = 0
            while True:
                if self.peek().text == "]":
                    self.advance()
                    break
                self._parse_expression()
                nval += 1
            if 0 <= nval <= 255:
                self.fn.opb(Op.OPcallrb, nval)
            else:
                self.fn.int_(nval)
                self.fn.op(Op.OPcallr)
        else:
            self._parse_expression()             # the single arg
            self.fn.op(Op.OPcall)

    def _parse_match(self) -> None:
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
        self._parse_expression()        # value to match
        self.expect("with")
        end_label = self.new_label("match_end")
        self._parse_matchcons(end_label)

    def _parse_matchcons(self, end_label: str) -> None:
        """One match case `( Cons args -> body )`, possibly followed by
        `| ( more cases )` recursively.
        """
        self.expect("(")
        head = self.advance()
        if head.text == "_":
            # Wildcard: drop the value and parse the body.
            self.fn.drop()
            self.expect("->")
            self._parse_program()
            self.expect(")")
            self.fn.label(end_label)
            return

        if head.kind != "id" or head.text not in self.constructors:
            raise SyntaxError(
                f"line {head.line}: constructor expected, got {head.text!r}"
            )
        tag, has_payload = self.constructors[head.text]
        next_case = self.new_label("match_case")
        self.fn.op(Op.OPfirst)             # get tag
        self._emit_intb_or_int(tag)
        self.fn.op(Op.OPeq)
        self.fn.else_(next_case)

        # Inside the case: payload handling.
        bound_locals: list[str] = []
        if has_payload:
            # Get payload (fetch[1]) then bind to a single local. (We
            # don't support multi-arg destructuring `Cons [a b _]` yet.)
            self.fn.opb(Op.OPfetchb, 1)
            payload_name = self.advance().text
            if payload_name == "_":
                self.fn.drop()
            else:
                idx = self.scope.declare(payload_name)
                new_locals_after_args = (idx + 1) - self.fn.nargs
                if new_locals_after_args > self.nlocals_high_water:
                    self.nlocals_high_water = new_locals_after_args
                self.fn.opb(Op.OPsetlocalb, idx)
                bound_locals.append(payload_name)
        else:
            self.fn.drop()

        self.expect("->")
        self._parse_program()
        self.expect(")")

        # Clean up the locals bound in this case.
        for nm in bound_locals:
            del self.scope.names[nm]
            self.scope.next_index -= 1

        self.fn.goto(end_label)
        self.fn.label(next_case)

        if self.peek().text == "|":
            self.advance()
            self._parse_matchcons(end_label)
            return

        # No more cases — fall through to nil + end.
        self.fn.drop()
        self.fn.op(Op.OPnil)
        self.fn.label(end_label)

    def _parse_let(self) -> None:
        """let VALUE -> DEST in BODY — DEST is either:
            - a single NAME              (simple binding via OPsetlocalb)
            - `[a b _ c]` tuple pattern  (destructure each position)

        Matches the C++ codegen byte-for-byte (compiler_term.cpp::parsels +
        parselocals).
        """
        # Evaluate the source value.
        self._parse_expression()
        self.expect("->")
        bound_names = self._parse_let_locals()
        self.expect("in")
        self._parse_expression()
        # Unbind everything we declared.
        for nm in bound_names:
            del self.scope.names[nm]
            self.scope.next_index -= 1

    def _parse_let_locals(self) -> list[str]:
        """parselocals — bind the value on stack to a destination pattern.
        Returns the list of names introduced (so the caller can unbind
        them after `in EXPR`).

        Supported destinations:
          - NAME            : single binding (OPsetlocalb)
          - `[a b _ c]`     : tuple destructuring; each slot is OPdup
                              + OPfetchb i + recursive parselocals;
                              after all positions, OPdrop the value.
          - `_`             : drops the value (no binding)
        """
        bound: list[str] = []
        t = self.advance()
        if t.text == "_":
            self.fn.drop()
            return bound
        if t.text == "[":
            n = 0
            while True:
                nxt = self.peek()
                if nxt.text == "]":
                    self.advance()
                    self.fn.drop()   # drop the original tuple
                    return bound
                if nxt.text == "_":
                    self.advance()
                    n += 1
                    continue
                # Real binding at position n.
                self.fn.op(Op.OPdup)
                if 0 <= n <= 255:
                    self.fn.opb(Op.OPfetchb, n)
                else:
                    self.fn.int_(n)
                    self.fn.op(Op.OPfetch)
                # Recursive parselocals for that field.
                bound += self._parse_let_locals()
                n += 1
        if t.kind != "id":
            raise SyntaxError(
                f"line {t.line}: let destination must be a name, '_', or '[..]'; "
                f"got {t.text!r}"
            )
        # Simple label binding.
        idx = self.scope.declare(t.text)
        new_locals_after_args = (idx + 1) - self.fn.nargs
        if new_locals_after_args > self.nlocals_high_water:
            self.nlocals_high_water = new_locals_after_args
        self.fn.opb(Op.OPsetlocalb, idx)
        bound.append(t.text)
        return bound

    def _parse_set(self) -> None:
        name_tok = self.advance()
        if name_tok.kind != "id":
            raise SyntaxError(f"line {name_tok.line}: set needs a NAME")
        self.expect("=")
        # The C++ uses the SAME pattern for both globals and locals:
        #   1) push idx
        #   2) evaluate rhs (leaves value on stack)
        #   3) OPsetglobal / OPsetlocal2  (both take [idx, value] from stack)
        # OPsetlocal2 (vs OPsetlocalb) lets the index live in `set`'s
        # uniform [idx, value] discipline. Same final bytecode size on
        # this path; matches the C++ emit order byte-for-byte.
        if self.scope and (loc := self.scope.lookup(name_tok.text)) is not None:
            self._emit_intb_or_int(loc)
            self._parse_expression()
            self.fn.op(Op.OPsetlocal2)
        elif name_tok.text in self.globals_by_name:
            self._emit_intb_or_int(self.globals_by_name[name_tok.text])
            self._parse_expression()
            self.fn.op(Op.OPsetglobal)
        else:
            raise SyntaxError(
                f"line {name_tok.line}: set: unknown variable {name_tok.text!r}"
            )

    # ---- identifier reference: var read OR function call ---------------
    def _parse_ref(self, name: str) -> None:
        # Local?
        if self.scope and (loc := self.scope.lookup(name)) is not None:
            self.fn.opb(Op.OPgetlocalb, loc)
            return
        # Global var/const?
        if name in self.globals_by_name:
            self._emit_get_global(self.globals_by_name[name])
            return
        # Sum-type constructor?
        if name in self.constructors:
            tag, has_payload = self.constructors[name]
            self._emit_intb_or_int(tag)
            if has_payload:
                self._parse_expression()
                self.fn.opb(Op.OPdeftabb, 2)
            else:
                self.fn.opb(Op.OPdeftabb, 1)
            return
        # Builtin call?
        if name in BUILTINS:
            opcode, nargs = BUILTINS[name]
            for _ in range(nargs):
                self._parse_expression()
            self.fn.op(opcode)
            return
        # User function call?
        if name in self.funs_by_name:
            fun_idx, nargs = self.funs_by_name[name]
            for _ in range(nargs):
                self._parse_expression()
            self._emit_intb_or_int(fun_idx)
            self.fn.op(Op.OPexec)
            return
        raise SyntaxError(f"unknown identifier {name!r}")

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("input", help=".mtl source file to compile")
    ap.add_argument("-o", "--output", help="output .bin (default: INPUT.bin)")
    args = ap.parse_args()
    src_path = Path(args.input)
    out_path = Path(args.output) if args.output else src_path.with_suffix(".bin")
    c = Compiler()
    try:
        blob = c.compile_source(src_path.read_text())
    except SyntaxError as e:
        print(f"compile error: {e}", file=sys.stderr)
        return 1
    out_path.write_bytes(blob)
    print(f"wrote {len(blob)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
