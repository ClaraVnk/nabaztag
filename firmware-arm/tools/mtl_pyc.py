#!/usr/bin/env python3
"""Phase 7b: Python source compiler — `.py` -> Metal `.bin`, through `.mtl`.

The "view as Python" surface for Metal: write a subset of Python, get the
bytecode the C++ `mtl_compiler` would emit for the equivalent `.mtl`.

This module is a front end only. It parses the Python with the stdlib
`ast` module and writes Metal source text (`--emit-mtl` prints it); that
text then goes through `mtl_comp.Compiler`, in-process — the same parser,
type inference and codegen that compile the real bootcode byte-identically
to the C++. So a Python program is typed exactly like its `.mtl` spelling,
and one the C++ would refuse is refused here, with mtl_comp's words.

### Supported Python subset

Top-level:
    G = 42                    # var G = 42;;
    def f(a, b): ...          # fun f a b = ...;;
    class S: idle = (); done = (int,)      # type S = idle | done I;;
    class T: x: int; y: str   # type T = [x: I y: S];;
    if FLAG: ... else: ...    # ifdef FLAG { ... } else { ... }
    if not FLAG: ...          # ifndef FLAG { ... }

Statements (in a function body):
    return EXPR               # EXPR is the value of the rest of the body
    x = EXPR                  # new name: let EXPR -> x in <rest>
                              # known local or global: set x = EXPR
    [a, b, _] = EXPR          # let EXPR -> [a b _] in <rest>
    obj.f = EXPR, x += EXPR   # set obj.f = EXPR, set x = x + EXPR
    if / elif / else          # if C then A else B
    while C: BODY             # while C do BODY
    for i in range(A, B):     # for i=A; i<B do BODY   (Form 2)
    match X: case Cons(p):    # match X with (Cons p -> ...) | (_ -> ...)
    global x                  # ignored (assignment to a global is a set)

Expressions: int / str / None / True / False, arithmetic, bitwise and
comparison operators, `and` / `or` / `not`, `-x`, `~x`, `A if C else B`,
`f(a, b)`, a bare function name (a function pointer, `#f`), `[a, b]`
(tuple), `arr(a, b)` ({a b}), `lst(a, b)` (a :: b :: nil), `ord('A')`,
`call_(f, a, b)` (call f [a b]), `Cons(x)`, `T(x=1, y=2)` ([x: 1 y: 2]),
`obj.f`.

A function named `main` always gets fun#0: the emitted source starts with
`proto main N;;`. A function called before its `def` gets a `proto` too.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mtl_comp import BUILTINS, Compiler, MtlError, wire_wrap  # noqa: E402


# Precedence levels of the MTL grammar (mtl_comp's parse ladder), lowest
# first. A sub-expression whose level is below what its slot needs gets
# parentheses; `( … )` is a parseprogram and costs no bytecode.
SEQ, EXPR, LOGIC, NOT, CMP, ADD, MUL, BIT, UNARY, ATOM = range(10)

_BINOPS = {
    ast.Add: ("+", ADD), ast.Sub: ("-", ADD),
    ast.Mult: ("*", MUL), ast.Div: ("/", MUL), ast.FloorDiv: ("/", MUL),
    ast.Mod: ("%", MUL),
    ast.BitAnd: ("&", BIT), ast.BitOr: ("|", BIT), ast.BitXor: ("^", BIT),
    ast.LShift: ("<<", BIT), ast.RShift: (">>", BIT),
}

_CMPOPS = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.Gt: ">",
    ast.LtE: "<=", ast.GtE: ">=",
}

# Words the MTL parser reads as syntax; a Python name spelled like one
# cannot be written into the source.
_MTL_KEYWORDS = {
    "proto", "var", "const", "fun", "type", "ifdef", "ifndef", "else",
    "if", "then", "let", "in", "set", "while", "do", "for", "match", "with",
    "call", "update", "nil",
}

# Python annotation -> MTL type.
_TYPE_NAMES = {"int": "I", "str": "S"}


def _mtl_string(value: str) -> str:
    """A Python str as an MTL string literal. The bytes are the UTF-8
    encoding; anything outside printable ASCII is written `\\DDD` with
    three digits, so a following digit is never read into the escape."""
    out = ['"']
    for b in value.encode("utf-8"):
        c = chr(b)
        if c in '"\\':
            out.append("\\" + c)
        elif 32 <= b < 127:
            out.append(c)
        else:
            out.append(f"\\{b:03d}")
    out.append('"')
    return "".join(out)


def _err(node: ast.AST, msg: str) -> SyntaxError:
    return SyntaxError(f"line {getattr(node, 'lineno', '?')}: {msg}")


class MtlEmitter:
    """Python AST -> MTL source text. Knows names only well enough to pick
    the spelling (`#f` or `f`, `set` or `let`); every check of meaning —
    arity, types, unknown labels — is left to mtl_comp."""

    def __init__(self) -> None:
        self.funs: dict[str, int] = {}             # name -> arity
        self.globals: set[str] = set()
        self.constructors: dict[str, bool] = {}    # name -> has payload
        self.structs: set[str] = set()
        self.fields: set[str] = set()
        self.types: set[str] = set()
        self.locals: list[str] = []

    # ---- module -----------------------------------------------------------
    def emit_module(self, src: str) -> str:
        tree = ast.parse(src)
        self._collect(tree.body)
        lines: list[str] = []
        if "main" in self.funs:
            lines.append(f"proto main {self.funs['main']};;")
        for name in self._forward_refs(tree.body):
            lines.append(f"proto {name} {self.funs[name]};;")
        for node in tree.body:
            lines.extend(self._top(node))
        return "\n".join(lines) + "\n"

    def _collect(self, body: list[ast.stmt]) -> None:
        """Every name declared at top level, both ifdef branches included:
        which branch is live is mtl_comp's decision, not ours."""
        for node in body:
            if isinstance(node, ast.FunctionDef):
                self.funs.setdefault(self._name(node, node.name), len(node.args.args))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.globals.add(self._name(node, t.id))
            elif isinstance(node, ast.ClassDef):
                self.types.add(self._name(node, node.name))
                for st in self._class_body(node):
                    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                        self.structs.add(node.name)
                        self.fields.add(self._name(st, st.target.id))
                    elif (isinstance(st, ast.Assign) and len(st.targets) == 1
                          and isinstance(st.targets[0], ast.Name)):
                        self.constructors[self._name(st, st.targets[0].id)] = \
                            self._sum_payload(st.value) is not None
            elif isinstance(node, ast.If):
                self._collect(node.body)
                self._collect(node.orelse)

    def _forward_refs(self, body: list[ast.stmt]) -> list[str]:
        """Functions called (or pointed to) before their `def`: each needs a
        `proto` ahead of the first use, as in MTL."""
        defined = {"main"}
        needed: list[str] = []

        def walk(stmts: list[ast.stmt]) -> None:
            for node in stmts:
                if isinstance(node, ast.FunctionDef):
                    defined.add(node.name)          # recursion needs no proto
                    for sub in ast.walk(node):
                        if (isinstance(sub, ast.Name) and sub.id in self.funs
                                and sub.id not in defined and sub.id not in needed):
                            needed.append(sub.id)
                elif isinstance(node, ast.If):
                    walk(node.body)
                    walk(node.orelse)

        walk(body)
        return needed

    def _top(self, node: ast.stmt) -> list[str]:
        if isinstance(node, ast.FunctionDef):
            return [self._fundef(node)]
        if isinstance(node, ast.ClassDef):
            return [self._classdef(node)]
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise _err(node, "top-level assignment must be `NAME = EXPR`")
            return [f"var {node.targets[0].id} = {self._const(node.value)};;"]
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return []                                   # module docstring
        if isinstance(node, ast.If):
            return self._ifdef(node)
        raise _err(node, f"unsupported top-level statement {type(node).__name__}")

    def _ifdef(self, node: ast.If) -> list[str]:
        test, kw = node.test, "ifdef"
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            test, kw = test.operand, "ifndef"
        if not isinstance(test, ast.Name):
            raise _err(node, "top-level `if` must be `if NAME:` or `if not NAME:` (ifdef)")
        out = [f"{kw} {self._name(node, test.id)} {{"]
        for st in node.body:
            out.extend("\t" + ln for ln in self._top(st))
        out.append("}")
        if node.orelse:
            out.append("else {")
            for st in node.orelse:
                out.extend("\t" + ln for ln in self._top(st))
            out.append("}")
        return out

    # ---- types ------------------------------------------------------------
    @staticmethod
    def _class_body(node: ast.ClassDef) -> list[ast.stmt]:
        return [s for s in node.body if not isinstance(s, ast.Pass)
                and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]

    def _classdef(self, node: ast.ClassDef) -> str:
        body = self._class_body(node)
        if not body:
            return f"type {node.name};;"
        if all(isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
               and s.value is None for s in body):
            fields = []
            for s in body:
                t = self._annotation(s.annotation)
                fields.append(s.target.id if t is None else f"{s.target.id}: {t}")
            return f"type {node.name} = [{' '.join(fields)}];;"
        if all(isinstance(s, ast.Assign) and len(s.targets) == 1
               and isinstance(s.targets[0], ast.Name) for s in body):
            conses = []
            for s in body:
                payload = self._sum_payload(s.value)
                name = s.targets[0].id
                conses.append(name if payload is None else f"{name} {payload}")
            return f"type {node.name} = {' | '.join(conses)};;"
        raise _err(node, "class body must be all `field: type` (struct) "
                         "or all `Cons = ()` / `Cons = (type,)` (sum)")

    def _annotation(self, node: ast.AST | None) -> str | None:
        """A Python annotation as an MTL type, or None (left to inference)."""
        if isinstance(node, ast.Name):
            if node.id in _TYPE_NAMES:
                return _TYPE_NAMES[node.id]
            if node.id in self.types:
                return node.id
            return None
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "list"):
            inner = self._annotation(node.slice)
            return f"list {inner or '_'}"
        return None

    def _sum_payload(self, value: ast.AST) -> str | None:
        """`()` / None: no payload. `(int,)`: one, `I`. `(int, str)`: a
        tuple, `[I S]`. An element we cannot name is `_` (inferred)."""
        if isinstance(value, ast.Constant) and value.value in ((), None):
            return None
        if isinstance(value, (ast.Tuple, ast.List)):
            if not value.elts:
                return None
            items = [self._annotation(e) or "_" for e in value.elts]
            return items[0] if len(items) == 1 else f"[{' '.join(items)}]"
        return self._annotation(value) or "_"

    # ---- global values ----------------------------------------------------
    def _const(self, node: ast.AST, in_list: bool = False) -> str:
        if isinstance(node, ast.Constant):
            v = node.value
            if v is None:
                return "nil"
            if isinstance(v, bool):
                return "1" if v else "0"
            if isinstance(v, int):
                return str(v) if v >= 0 or not in_list else f"({v})"
            if isinstance(v, str):
                return _mtl_string(v)
            raise _err(node, f"unsupported constant {v!r}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._const(node.operand, False)
            return f"(-{inner})" if in_list else f"-{inner}"
        if isinstance(node, (ast.List, ast.Tuple)):
            return "[" + " ".join(self._const(e, True) for e in node.elts) + "]"
        raise _err(node, f"unsupported constant expression {type(node).__name__}")

    # ---- functions --------------------------------------------------------
    def _fundef(self, node: ast.FunctionDef) -> str:
        a = node.args
        if (a.vararg or a.kwarg or a.kwonlyargs or a.defaults or a.posonlyargs
                or node.decorator_list):
            raise _err(node, "functions take plain positional arguments only")
        args = [self._name(node, x.arg) for x in a.args]
        self.locals = list(args)
        body, _ = self._block(node.body)
        self.locals = []
        head = " ".join([node.name] + args)
        return f"fun {head} = {body};;"

    def _block(self, stmts: list[ast.stmt]) -> tuple[str, int]:
        """A statement list as an MTL program: `a; b; c`. An assignment to a
        new name turns the rest of the list into a `let … in` body."""
        parts: list[str] = []
        prec = ATOM
        for i, st in enumerate(stmts):
            if isinstance(st, ast.Global):
                continue
            if isinstance(st, ast.Return):
                text, prec = (("nil", ATOM) if st.value is None else self._expr(st.value))
                parts.append(text)
                break
            if isinstance(st, ast.Assign):
                if len(st.targets) != 1:
                    raise _err(st, "only single-target assignment is supported")
                bound = self._let_target(st.targets[0])
                if bound is not None:
                    text = self._let(st, st.targets[0], st.value, stmts[i + 1:], bound)
                    parts.append(text)
                    prec = EXPR
                    break
                text, prec = self._set(st, st.targets[0], st.value), EXPR
            elif isinstance(st, ast.AugAssign):
                op = ast.BinOp(left=self._as_load(st.target), op=st.op, right=st.value)
                ast.copy_location(op, st)
                if self._let_target(st.target) is not None:
                    raise _err(st, "augmented assignment to an unbound name")
                text, prec = self._set(st, st.target, op), EXPR
            else:
                text, prec = self._stmt(st)
            parts.append(text)
        if not parts:
            return "nil", ATOM
        if len(parts) == 1:
            return parts[0], prec
        return "; ".join(parts), SEQ

    @staticmethod
    def _as_load(target: ast.AST) -> ast.AST:
        node = ast.parse(ast.unparse(target), mode="eval").body
        return ast.copy_location(node, target)

    def _let_target(self, target: ast.AST) -> list[str] | None:
        """Names a `let` would bind for this target, or None if the target
        is an existing local/global or a field (a `set`)."""
        if isinstance(target, ast.Name):
            if target.id in self.locals or target.id in self.globals:
                return None
            return [target.id]
        if isinstance(target, (ast.List, ast.Tuple)):
            names: list[str] = []
            for el in target.elts:
                if isinstance(el, ast.Name):
                    if el.id != "_":
                        names.append(el.id)
                elif isinstance(el, (ast.List, ast.Tuple)):
                    names.extend(self._let_target(el) or [])
                else:
                    raise _err(target, "destructuring elements must be names or `_`")
            return names
        return None

    def _pattern(self, target: ast.AST) -> str:
        if isinstance(target, ast.Name):
            return self._name(target, target.id)
        return "[" + " ".join(self._pattern(e) for e in target.elts) + "]"

    def _let(self, st: ast.stmt, target: ast.AST, value: ast.AST,
             rest: list[ast.stmt], bound: list[str]) -> str:
        val = self._wrap(value, EXPR)
        pat = self._pattern(target)
        self.locals.extend(bound)
        body = self._paren(self._block(rest), EXPR)
        del self.locals[len(self.locals) - len(bound):]
        return f"let {val} -> {pat} in {body}"

    def _set(self, st: ast.stmt, target: ast.AST, value: ast.AST) -> str:
        if isinstance(target, ast.Name):
            lhs = target.id
        elif isinstance(target, ast.Attribute):
            lhs = self._field_path(target)
        else:
            raise _err(st, "only `NAME = EXPR`, `obj.field = EXPR` or "
                           "`[a, b] = EXPR` assignment is supported")
        return f"set {lhs} = {self._wrap(value, EXPR)}"

    def _field_path(self, node: ast.Attribute) -> str:
        path: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            path.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            raise _err(node, "MTL reads and writes a field only through a variable")
        return ".".join([cur.id] + list(reversed(path)))

    # ---- statements -------------------------------------------------------
    def _stmt(self, node: ast.stmt) -> tuple[str, int]:
        if isinstance(node, ast.Expr):
            return self._expr(node.value)
        if isinstance(node, ast.If):
            cond = self._wrap(node.test, EXPR)
            then = self._paren(self._block(node.body), EXPR)
            # Always an `else`: `else nil` is what a missing one compiles
            # to (OPnil, an undefined type), and it rules out a dangling else.
            other = self._paren(self._block(node.orelse), EXPR)
            return f"if {cond} then {then} else {other}", EXPR
        if isinstance(node, ast.While):
            if node.orelse:
                raise _err(node, "`while … else` is not supported")
            cond = self._wrap(node.test, EXPR)
            body = self._paren(self._block(node.body), EXPR)
            return f"while {cond} do {body}", EXPR
        if isinstance(node, ast.For):
            return self._for(node), EXPR
        if isinstance(node, ast.Match):
            return self._match(node), EXPR
        raise _err(node, f"unsupported statement {type(node).__name__}")

    def _for(self, node: ast.For) -> str:
        it = node.iter
        if not (isinstance(node.target, ast.Name) and isinstance(it, ast.Call)
                and isinstance(it.func, ast.Name) and it.func.id == "range"
                and not it.keywords and 1 <= len(it.args) <= 2) or node.orelse:
            raise _err(node, "only `for VAR in range(STOP)` or `range(START, STOP)` "
                             "is supported")
        start = "0" if len(it.args) == 1 else self._wrap(it.args[0], EXPR)
        var = self._name(node, node.target.id)
        self.locals.append(var)
        stop = self._wrap(it.args[-1], ADD)
        body = self._paren(self._block(node.body), EXPR)
        self.locals.pop()
        return f"for {var}={start}; {var}<{stop} do {body}"

    def _match(self, node: ast.Match) -> str:
        subject = self._wrap(node.subject, EXPR)
        cases: list[str] = []
        for case in node.cases:
            if case.guard is not None:
                raise _err(case.pattern, "match guards are not supported")
            pat = case.pattern
            if isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name is None:
                body, _ = self._block(case.body)
                cases.append(f"(_ -> {body})")
                break                       # mtl: the wildcard ends the match
            bound: list[str] = []
            if isinstance(pat, ast.MatchAs) and pat.pattern is None:
                if self.constructors.get(pat.name):
                    raise _err(pat, f"constructor {pat.name!r} has a payload: "
                                    f"write `case {pat.name}(x)` or `case {pat.name}(_)`")
                head = self._name(pat, pat.name)
            elif isinstance(pat, ast.MatchClass) and isinstance(pat.cls, ast.Name):
                if pat.kwd_patterns:
                    raise _err(pat, "keyword patterns are not supported")
                cname = self._name(pat, pat.cls.id)
                subs = [self._match_local(p, bound) for p in pat.patterns]
                if len(subs) > 1:
                    head = f"{cname} [{' '.join(subs)}]"
                elif subs:
                    head = f"{cname} {subs[0]}"
                elif self.constructors.get(cname):
                    head = f"{cname} _"
                else:
                    head = cname
            else:
                raise _err(pat, "a case must be a constructor, `Cons(x)` or `_`")
            self.locals.extend(bound)
            body, _ = self._block(case.body)
            del self.locals[len(self.locals) - len(bound):]
            cases.append(f"({head} -> {body})")
        return f"match {subject} with " + " | ".join(cases)

    def _match_local(self, pat: ast.pattern, bound: list[str]) -> str:
        if isinstance(pat, ast.MatchAs) and pat.pattern is None:
            if pat.name is None:
                return "_"
            bound.append(self._name(pat, pat.name))
            return pat.name
        if isinstance(pat, ast.MatchSequence):
            return "[" + " ".join(self._match_local(p, bound) for p in pat.patterns) + "]"
        raise _err(pat, "a constructor's payload pattern must be a name, `_` or `[a, b]`")

    # ---- expressions ------------------------------------------------------
    @staticmethod
    def _paren(r: tuple[str, int], need: int) -> str:
        text, prec = r
        return text if prec >= need else f"({text})"

    def _wrap(self, node: ast.AST, need: int) -> str:
        return self._paren(self._expr(node), need)

    def _expr(self, node: ast.AST) -> tuple[str, int]:
        if isinstance(node, ast.Constant):
            v = node.value
            if v is None:
                return "nil", ATOM
            if isinstance(v, bool):
                return ("1" if v else "0"), ATOM
            if isinstance(v, int):
                return (str(v), ATOM) if v >= 0 else (str(v), UNARY)
            if isinstance(v, str):
                return _mtl_string(v), ATOM
            raise _err(node, f"unsupported constant {v!r}")
        if isinstance(node, ast.Name):
            return self._ref(node), ATOM
        if isinstance(node, ast.BinOp):
            op = _BINOPS.get(type(node.op))
            if op is None:
                raise _err(node, f"unsupported operator {type(node.op).__name__}")
            sym, prec = op
            return f"{self._wrap(node.left, prec)} {sym} {self._wrap(node.right, prec + 1)}", prec
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                if (isinstance(node.operand, ast.Constant)
                        and type(node.operand.value) is int and node.operand.value >= 0):
                    return f"-{node.operand.value}", UNARY
                inner = self._wrap(node.operand, UNARY)
                return (f"-({inner})" if inner.startswith("-") else f"-{inner}"), UNARY
            if isinstance(node.op, ast.Invert):
                return f"~{self._wrap(node.operand, UNARY)}", UNARY
            if isinstance(node.op, ast.Not):
                return f"!{self._wrap(node.operand, NOT)}", NOT
            raise _err(node, f"unsupported unary operator {type(node.op).__name__}")
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise _err(node, "chained comparisons are not supported")
            sym = _CMPOPS.get(type(node.ops[0]))
            if sym is None:
                raise _err(node, f"unsupported comparison {type(node.ops[0]).__name__}")
            return (f"{self._wrap(node.left, CMP)} {sym} "
                    f"{self._wrap(node.comparators[0], CMP + 1)}"), CMP
        if isinstance(node, ast.BoolOp):
            sym = " && " if isinstance(node.op, ast.And) else " || "
            return sym.join(self._wrap(v, NOT) for v in node.values), LOGIC
        if isinstance(node, ast.IfExp):
            return (f"if {self._wrap(node.test, EXPR)} then {self._wrap(node.body, EXPR)} "
                    f"else {self._wrap(node.orelse, EXPR)}"), EXPR
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return self._field_path(node), ATOM
        if isinstance(node, (ast.List, ast.Tuple)):
            return f"[{self._items(node.elts)}]", ATOM
        raise _err(node, f"unsupported expression {type(node).__name__}")

    def _items(self, elts: list[ast.AST]) -> str:
        items = [self._wrap(e, ATOM) for e in elts]
        # `[x …` with x a field name is a struct creation to the parser.
        if items and items[0] in self.fields:
            items[0] = f"({items[0]})"
        return " ".join(items)

    def _ref(self, node: ast.Name) -> str:
        name = self._name(node, node.id)
        if name in self.locals or name in self.globals:
            return name
        if name in self.constructors:
            if self.constructors[name]:
                raise _err(node, f"constructor {name!r} needs a payload: `{name}(value)`")
            return name
        if name in self.funs:
            return f"#{name}"                   # a function used as a value
        if name in BUILTINS and BUILTINS[name][1] != 0:
            return f"#{name}"
        return name

    def _call(self, node: ast.Call) -> tuple[str, int]:
        if not isinstance(node.func, ast.Name):
            raise _err(node, "only NAME(...) calls are supported")
        fname = self._name(node, node.func.id)
        args = node.args
        if any(isinstance(a, ast.Starred) for a in args):
            raise _err(node, "*args is not supported")
        if node.keywords:
            if args:
                raise _err(node, "a struct is built from keywords only: T(x=1, y=2)")
            fields = []
            for kw in node.keywords:
                if kw.arg is None:
                    raise _err(node, "**kwargs is not supported")
                fields.append(f"{kw.arg}: {self._wrap(kw.value, ATOM)}")
            return f"[{' '.join(fields)}]", ATOM
        if fname == "ord" and len(args) == 1 and isinstance(args[0], ast.Constant) \
                and isinstance(args[0].value, str) and len(args[0].value) == 1:
            ch = args[0].value
            if ch.isascii() and ch.isalnum():
                return f"'{ch}'", ATOM
            return str(ord(ch) & 0xFF), ATOM
        if fname == "lst":
            if not args:
                return "nil", ATOM
            return " :: ".join([self._wrap(a, LOGIC) for a in args] + ["nil"]), EXPR
        if fname == "arr":
            return f"{{{self._items(args)}}}", ATOM
        if fname == "call_":
            if not args:
                raise _err(node, "call_() needs the function as its first argument")
            return f"call {self._wrap(args[0], ATOM)} [{self._items(args[1:])}]", EXPR
        if fname in self.locals:
            raise _err(node, f"{fname!r} is a variable: call a function value "
                             f"with call_({fname}, …)")
        if fname in self.constructors:
            if not self.constructors[fname]:
                if args:
                    raise _err(node, f"constructor {fname!r} has no payload")
                return fname, ATOM
            if len(args) != 1:
                raise _err(node, f"constructor {fname!r} takes one payload argument")
            return f"{fname} {self._wrap(args[0], ATOM)}", EXPR
        if not args:
            # A call with no argument is the bare name. Unless the name is
            # known to take none, parenthesise it, so a function that wants
            # arguments fails at `)` instead of taking its neighbours.
            arity = (self.funs[fname] if fname in self.funs
                     else BUILTINS.get(fname, (None, None))[1])
            if arity == 0:
                return fname, ATOM
            return f"({fname})", ATOM
        return " ".join([fname] + [self._wrap(a, ATOM) for a in args]), EXPR

    @staticmethod
    def _name(node: ast.AST, name: str) -> str:
        if name in _MTL_KEYWORDS:
            raise _err(node, f"{name!r} is an MTL keyword and cannot be used as a name")
        return name


def python_to_mtl(src: str) -> str:
    """The Metal source for a Python program (raises SyntaxError)."""
    return MtlEmitter().emit_module(src)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("input", help=".py source file")
    ap.add_argument("-o", "--output", help="output .bin (default: INPUT.bin)")
    ap.add_argument(
        "--bare", action="store_true",
        help="emit the bytecode body alone, without the 17-byte "
             "amber/Mind envelope mtl_compiler normally writes around it",
    )
    ap.add_argument(
        "--emit-mtl", action="store_true",
        help="print the generated .mtl source and stop (nothing compiled, "
             "nothing written)",
    )
    ap.add_argument(
        "--types", action="store_true",
        help="print the `Compiler : name : type` line of every declaration, "
             "as mtl_comp.py --types does",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="type-check only: write nothing",
    )
    args = ap.parse_args()
    src_path = Path(args.input)
    out_path = Path(args.output) if args.output else src_path.with_suffix(".bin")
    try:
        mtl = python_to_mtl(src_path.read_bytes().decode("utf-8"))
    except SyntaxError as e:
        print(f"compile error: {e}", file=sys.stderr)
        return 1
    if args.emit_mtl:
        sys.stdout.write(mtl)
        return 0
    c = Compiler()
    try:
        blob = c.compile_source(mtl)
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
