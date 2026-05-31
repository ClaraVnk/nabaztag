#!/usr/bin/env python3
"""Phase 7b: Python source compiler — `.py` -> Metal `.bin`.

The "view as Python" surface for Metal: write what looks like idiomatic
Python, get byte-identical MTL bytecode out the other end. Built on
mtl_asm's Encoder. Where mtl_comp.py parses `.mtl` source (with all its
quirks), this module parses standard Python via the stdlib `ast`
module and maps the AST nodes to MTL operations.

The lossless target: `mtl_pyc.py prog.py -o prog.bin` produces the
same bytecode `mtl_comp.py prog.mtl -o prog.bin` would, when prog.py
and prog.mtl express the same logic.

### Supported Python subset (v0)

Top-level:
    G = 42                    # → var G=42;;
    K = "hello"               # → var K="hello";;  (no const distinction yet)

    def fname(arg1, arg2):
        ...                   # → fun fname arg1 arg2 = body;;

Statements (in function body):
    return EXPR               # leaves EXPR's value as the function's return
    if X: Y else: Z           # → if X then Y else Z  (statement form)
    while COND: BODY          # → while COND do BODY
    for i in range(N): BODY   # → for i=0; i<N; i+1 do BODY
    var = EXPR                # → let EXPR -> var in <rest>  (function-scoped)

Expressions:
    int / str / None          # int / string / nil
    +, -, *, /, %, &, |, ^,
    <<, >>, ==, !=, <, >, <=, >=,
    `and` (short-circuit) and `or` (short-circuit)
    not, -EXPR, ~EXPR
    name (var ref / fn call)
    fname(a, b, c)            # MTL fn call with positional args
    [a, b, c]                 # tuple `[a b c]`

### Convention

A function named `main` always gets MTL fun#0 (auto-proto). This
matches the canonical MTL pattern `proto main 0;; ... fun main=...;;`.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mtl_asm import Encoder, FunctionBody, Op, NIL  # noqa: E402
from mtl_comp import BUILTINS, Scope  # noqa: E402


# Python AST binary ops → MTL opcode.
_BINOPS = {
    ast.Add:    Op.OPadd,
    ast.Sub:    Op.OPsub,
    ast.Mult:   Op.OPmul,
    ast.Div:    Op.OPdiv,
    ast.FloorDiv: Op.OPdiv,
    ast.Mod:    Op.OPmod,
    ast.BitAnd: Op.OPand,
    ast.BitOr:  Op.OPor,
    ast.BitXor: Op.OPeor,
    ast.LShift: Op.OPshl,
    ast.RShift: Op.OPshr,
}

# Python AST comparisons → MTL opcode.
_CMPOPS = {
    ast.Eq:    Op.OPeq,
    ast.NotEq: Op.OPne,
    ast.Lt:    Op.OPlt,
    ast.Gt:    Op.OPgt,
    ast.LtE:   Op.OPle,
    ast.GtE:   Op.OPge,
}


class PyCompiler:
    def __init__(self) -> None:
        self.enc = Encoder()
        self.globals_by_name: dict[str, int] = {}
        self.funs_by_name: dict[str, tuple[int, int]] = {}
        # Sum-type constructors: name -> (tag, has_payload).
        self.constructors: dict[str, tuple[int, bool]] = {}
        # Struct fields: field name -> (idx, struct_size).
        self.struct_fields: dict[str, tuple[int, int]] = {}
        # Names known via `# ifdef` so we can skip blocks (set by
        # `@if_defined(NAME)` decorator on classes, or auto-defined
        # by var/fun/type decls).
        # Phase 7b doesn't currently support runtime ifdef gating
        # at the Python level — but the namespace check is in place.
        # fn-being-compiled state
        self.fn: FunctionBody | None = None
        self.scope: Scope | None = None
        self.label_counter = 0
        self.nlocals_high_water = 0

    # ---- helpers ---------------------------------------------------------
    def new_label(self, prefix: str = "L") -> str:
        self.label_counter += 1
        return f"{prefix}{self.label_counter}"

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

    # ---- top-level -------------------------------------------------------
    def compile_source(self, src: str) -> bytes:
        tree = ast.parse(src)
        # First pass: register every top-level fun in source order. If
        # `main` exists, it gets index 0 (auto-proto). Var decls don't
        # need pre-registration; they're consumed in source order.
        funs: list[ast.FunctionDef] = [
            n for n in tree.body if isinstance(n, ast.FunctionDef)
        ]
        main_fn = next((f for f in funs if f.name == "main"), None)
        if main_fn is not None:
            # Reserve index 0 for main.
            placeholder = self.enc.new_function(nargs=len(main_fn.args.args), nlocals=0)
            self.funs_by_name["main"] = (0, len(main_fn.args.args))

        # Second pass: walk top-level statements in source order.
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self._compile_funcdef(node)
            elif isinstance(node, ast.ClassDef):
                self._compile_classdef(node)
            elif isinstance(node, ast.Assign):
                self._compile_global_assign(node)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                # Docstring at module level — ignore.
                pass
            elif isinstance(node, ast.If):
                # `if NAME:` at top-level is an `ifdef NAME` block.
                # Active iff NAME is currently in scope (var/fun/type).
                self._compile_top_ifdef(node)
            else:
                raise SyntaxError(
                    f"line {node.lineno}: unsupported top-level statement "
                    f"{ast.dump(node)[:80]}"
                )
        return self.enc.emit()

    def _compile_top_ifdef(self, node: ast.If) -> None:
        """`if NAME:` at top-level is an `ifdef NAME` block. The active
        branch's body is parsed as top-level decls; the inactive branch
        is skipped wholesale. Supports `else:` for the alternative.
        """
        # Accept `if NAME:` (ifdef) or `if not NAME:` (ifndef).
        negate = False
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            negate = True
            test = test.operand
        if not isinstance(test, ast.Name):
            raise SyntaxError(
                f"line {node.lineno}: top-level `if` must be `if NAME:` (ifdef)"
            )
        name = test.id
        active = self._is_defined(name)
        if negate:
            active = not active
        body = node.body if active else node.orelse
        for st in body:
            if isinstance(st, ast.FunctionDef):
                self._compile_funcdef(st)
            elif isinstance(st, ast.ClassDef):
                self._compile_classdef(st)
            elif isinstance(st, ast.Assign):
                self._compile_global_assign(st)
            elif isinstance(st, ast.If):
                self._compile_top_ifdef(st)
            else:
                raise SyntaxError(
                    f"line {st.lineno}: unsupported top-level statement "
                    f"in ifdef block"
                )

    def _is_defined(self, name: str) -> bool:
        return (
            name in self.globals_by_name
            or name in self.funs_by_name
            or name in self.constructors
            or name in self.struct_fields  # also covers struct types
        )

    def _compile_classdef(self, node: ast.ClassDef) -> None:
        """Two flavors:
          - `class Name:`  with class-body attribute assignments where
            each value is a tuple `()` (no payload) or `(...)` (1 payload)
            => sum type.
          - `class Name:`  with annotation-only assignments (`field: type`)
            => struct type with those fields.
        We disambiguate by looking at the FIRST class-body statement.
        """
        body = [s for s in node.body if not isinstance(s, ast.Pass)
                and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.AnnAssign):
            self._declare_struct(node.name, body)
        elif isinstance(first, ast.Assign):
            self._declare_sum(node.name, body)
        else:
            raise SyntaxError(
                f"line {node.lineno}: class body must be all `field: type` "
                f"(struct) or all `Cons = ()` (sum)"
            )

    def _declare_sum(self, type_name: str, body: list[ast.stmt]) -> None:
        """`class Name:
              cons1 = ()        # CONS0 (no payload)
              cons2 = (...)     # CONS (1 payload)
              ...`
        Each constructor gets an auto-incrementing tag.
        """
        tag = 0
        for st in body:
            if not isinstance(st, ast.Assign) or len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
                raise SyntaxError(
                    f"line {st.lineno}: sum-type body needs `Cons = ()` or `Cons = (X,)`"
                )
            cname = st.targets[0].id
            v = st.value
            has_payload = False
            if isinstance(v, (ast.Tuple, ast.List)):
                has_payload = len(v.elts) > 0
            elif isinstance(v, ast.Constant) and v.value == ():
                has_payload = False
            else:
                # Be lenient: any non-empty value means "with payload".
                has_payload = not (isinstance(v, ast.Constant) and v.value is None)
            self.constructors[cname] = (tag, has_payload)
            tag += 1

    def _declare_struct(self, type_name: str, body: list[ast.stmt]) -> None:
        """`class Name:
              field1: type
              field2: type
              ...`
        Each field gets an index and the total struct size.
        """
        field_names: list[str] = []
        for st in body:
            if not isinstance(st, ast.AnnAssign) or not isinstance(st.target, ast.Name):
                raise SyntaxError(
                    f"line {st.lineno}: struct field must be `name: type`"
                )
            field_names.append(st.target.id)
        size = len(field_names)
        for i, fname in enumerate(field_names):
            self.struct_fields[fname] = (i, size)

    def _compile_global_assign(self, node: ast.Assign) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise SyntaxError(
                f"line {node.lineno}: top-level assignment must be `NAME = EXPR`"
            )
        name = node.targets[0].id
        init = self._eval_const(node.value)
        idx = self.enc.add_global(init)
        self.globals_by_name[name] = idx

    def _eval_const(self, node: ast.AST) -> "object":
        """Evaluate a constant expression at compile time. Supports the
        same forms MTL accepts in `var X = EXPR;;`."""
        if isinstance(node, ast.Constant):
            v = node.value
            if v is None:
                return NIL
            if isinstance(v, bool):
                return 1 if v else 0
            if isinstance(v, (int, str)):
                return v
            raise SyntaxError(f"unsupported constant value {v!r}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._eval_const(node.operand)
            if isinstance(inner, int):
                return -inner
            raise SyntaxError("only int may be negated in a constant")
        if isinstance(node, ast.List) or isinstance(node, ast.Tuple):
            return tuple(self._eval_const(e) for e in node.elts)
        if isinstance(node, ast.Name) and node.id == "None":
            return NIL
        raise SyntaxError(
            f"line {getattr(node, 'lineno', '?')}: unsupported constant "
            f"expression {ast.dump(node)[:60]}"
        )

    # ---- function definition --------------------------------------------
    def _compile_funcdef(self, node: ast.FunctionDef) -> None:
        args = [a.arg for a in node.args.args]

        if node.name in self.funs_by_name:
            # Reserved slot (e.g. main).
            fun_idx, _ = self.funs_by_name[node.name]
            fn = self.enc._functions[fun_idx]
            fn.nargs = len(args)
            fn.code = bytearray()
        else:
            fn = self.enc.new_function(nargs=len(args), nlocals=0)
            fun_idx = len(self.enc._functions) - 1
            self.funs_by_name[node.name] = (fun_idx, len(args))

        self.fn = fn
        self.scope = Scope()
        for a in args:
            self.scope.declare(a)
        self.nlocals_high_water = 0

        self._compile_body(node.body)
        self.fn.ret()
        self.fn.nlocals = self.nlocals_high_water
        self.fn = None
        self.scope = None

    def _compile_body(self, stmts: list[ast.stmt]) -> None:
        """Compile a sequence of statements. The LAST statement's value
        becomes the function's result; intermediate ones are OPdrop'd
        after evaluation (matching Metal's `expr; expr; expr` semantics).

        A `return X` anywhere in the sequence emits X and stops further
        compilation of this body (callers handle OPret at the end).
        """
        if not stmts:
            self.fn.op(Op.OPnil)
            return
        for i, st in enumerate(stmts):
            last = (i == len(stmts) - 1)
            if isinstance(st, ast.Return):
                # `return X` — evaluate, that becomes the result; stop.
                if st.value is None:
                    self.fn.op(Op.OPnil)
                else:
                    self._compile_expr(st.value)
                return
            if isinstance(st, ast.Assign):
                # `var = EXPR` — Metal lacks plain statement-level
                # assignment, so we encode it as `let EXPR -> var in
                # <rest>`. The "rest" is the remaining statements.
                if len(st.targets) != 1:
                    raise SyntaxError(
                        f"line {st.lineno}: only single-target assignment supported"
                    )
                target = st.targets[0]
                # Struct field write: `obj.field = EXPR`.
                if isinstance(target, ast.Attribute):
                    self._compile_attribute_set_chain(target, st.value)
                    if not last:
                        self.fn.drop()
                    continue
                # List/tuple destructuring: `[a, b, c] = EXPR` mirrors
                # `let EXPR -> [a b c] in <rest>`.
                if isinstance(target, (ast.List, ast.Tuple)):
                    names: list[str] = []
                    for el in target.elts:
                        if not isinstance(el, ast.Name):
                            raise SyntaxError(
                                f"line {st.lineno}: destructuring elements "
                                f"must be plain names"
                            )
                        names.append(el.id)
                    self._compile_expr(st.value)
                    declared: list[str] = []
                    for j, nm in enumerate(names):
                        self.fn.op(Op.OPdup)
                        self.fn.opb(Op.OPfetchb, j)
                        idx = self.scope.declare(nm)
                        new_locals_after_args = (idx + 1) - self.fn.nargs
                        if new_locals_after_args > self.nlocals_high_water:
                            self.nlocals_high_water = new_locals_after_args
                        self.fn.opb(Op.OPsetlocalb, idx)
                        declared.append(nm)
                    self.fn.drop()
                    self._compile_body(stmts[i + 1:])
                    for nm in reversed(declared):
                        self.scope.unbind(nm)
                    return
                if not isinstance(target, ast.Name):
                    raise SyntaxError(
                        f"line {st.lineno}: only `NAME = EXPR` or "
                        f"`obj.field = EXPR` assignment supported"
                    )
                name = target.id
                if name in self.globals_by_name:
                    # set global X = expr  (in C++: OPintb idx; expr; OPsetglobal)
                    self._emit_intb_or_int(self.globals_by_name[name])
                    self._compile_expr(st.value)
                    self.fn.op(Op.OPsetglobal)
                    if not last:
                        self.fn.drop()
                    continue
                if self.scope.lookup(name) is not None:
                    # set local
                    loc = self.scope.lookup(name)
                    self._emit_intb_or_int(loc)
                    self._compile_expr(st.value)
                    self.fn.op(Op.OPsetlocal2)
                    if not last:
                        self.fn.drop()
                    continue
                # New local binding via let-rest.
                self._compile_expr(st.value)
                idx = self.scope.declare(name)
                new_locals_after_args = (idx + 1) - self.fn.nargs
                if new_locals_after_args > self.nlocals_high_water:
                    self.nlocals_high_water = new_locals_after_args
                self.fn.opb(Op.OPsetlocalb, idx)
                # The "rest" is the rest of the body.
                self._compile_body(stmts[i + 1:])
                self.scope.unbind(name)
                return
            # Other statement forms (handled by compile_stmt):
            self._compile_stmt(st)
            if not last:
                self.fn.drop()

    def _compile_attribute_set_chain(self, attr_target: ast.Attribute, value: ast.AST) -> None:
        """`obj.field = EXPR`  (and chained `obj.a.b = EXPR`) — mirrors
        mtl_comp's set-field codegen:
            <get OBJ>
            <OPfetchb field_idx>*    (n-1 fetches for inner fields)
            <push last field idx>
            <EXPR>
            OPstore
        """
        # Walk attribute chain to find the root expression and field path.
        fields: list[str] = []
        cur: ast.AST = attr_target
        while isinstance(cur, ast.Attribute):
            fields.append(cur.attr)
            cur = cur.value
        fields.reverse()
        # Root expression (e.g. `obj`).
        self._compile_expr(cur)
        # All but the last field are intermediate fetches.
        for f in fields[:-1]:
            if f not in self.struct_fields:
                raise SyntaxError(f"unknown field {f!r}")
            idx, _ = self.struct_fields[f]
            if 0 <= idx <= 255:
                self.fn.opb(Op.OPfetchb, idx)
            else:
                self.fn.int_(idx)
                self.fn.op(Op.OPfetch)
        last = fields[-1]
        if last not in self.struct_fields:
            raise SyntaxError(f"unknown field {last!r}")
        last_idx, _ = self.struct_fields[last]
        self._emit_intb_or_int(last_idx)
        self._compile_expr(value)
        self.fn.op(Op.OPstore)

    def _compile_stmt(self, node: ast.stmt) -> None:
        """Compile a statement that leaves exactly one value on the stack.
        (return/assign are handled in _compile_body.)"""
        if isinstance(node, ast.If):
            # if X: ELSE Z
            self._compile_expr(node.test)
            else_label = self.new_label("else")
            end_label = self.new_label("endif")
            self.fn.else_(else_label)
            self._compile_body(node.body)
            self.fn.goto(end_label)
            self.fn.label(else_label)
            if node.orelse:
                self._compile_body(node.orelse)
            else:
                self.fn.op(Op.OPnil)
            self.fn.label(end_label)
            return
        if isinstance(node, ast.While):
            loop = self.new_label("while")
            end = self.new_label("endwhile")
            self.fn.op(Op.OPnil)
            self.fn.label(loop)
            self._compile_expr(node.test)
            self.fn.else_(end)
            self.fn.drop()
            self._compile_body(node.body)
            self.fn.goto(loop)
            self.fn.label(end)
            return
        if isinstance(node, ast.For):
            # `for VAR in range(N): BODY` only (Python-idiomatic form
            # that maps cleanly to MTL Form 2).
            if not (isinstance(node.target, ast.Name)
                    and isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "range"):
                raise SyntaxError(
                    f"line {node.lineno}: only `for VAR in range(...)` supported"
                )
            args = node.iter.args
            if len(args) == 1:
                # range(N): start=0, stop=N
                start_node = ast.Constant(value=0)
                stop_node = args[0]
            elif len(args) == 2:
                start_node, stop_node = args
            else:
                raise SyntaxError(
                    f"line {node.lineno}: range with step not yet supported"
                )
            var_name = node.target.id
            self._compile_expr(start_node)
            idx = self.scope.declare(var_name)
            new_locals_after_args = (idx + 1) - self.fn.nargs
            if new_locals_after_args > self.nlocals_high_water:
                self.nlocals_high_water = new_locals_after_args
            self.fn.opb(Op.OPsetlocalb, idx)
            loop_check = self.new_label("for_check")
            end_label = self.new_label("for_end")
            self.fn.op(Op.OPnil)
            self.fn.label(loop_check)
            self.fn.opb(Op.OPgetlocalb, idx)
            self._compile_expr(stop_node)
            self.fn.op(Op.OPlt)
            self.fn.else_(end_label)
            self.fn.drop()
            self._compile_body(node.body)
            self.fn.opb(Op.OPgetlocalb, idx)
            self.fn.intb(1)
            self.fn.op(Op.OPadd)
            self.fn.opb(Op.OPsetlocalb, idx)
            self.fn.goto(loop_check)
            self.fn.label(end_label)
            self.scope.unbind(var_name)
            return
        if isinstance(node, ast.Match):
            # `match X: case Cons1: ... case Cons2(p): ... case _: ...`
            self._compile_expr(node.subject)
            end_label = self.new_label("match_end")
            self._compile_match_cases(node.cases, end_label)
            return
        if isinstance(node, ast.Expr):
            # A statement that's just an expression — eval, leave on
            # stack (caller will OPdrop if not last).
            self._compile_expr(node.value)
            return
        raise SyntaxError(
            f"line {getattr(node, 'lineno', '?')}: unsupported statement "
            f"{ast.dump(node)[:60]}"
        )

    def _compile_match_cases(self, cases: list[ast.match_case], end_label: str) -> None:
        """Walk the case list emitting tag-eq checks chained through
        OPelse, mirroring mtl_comp._parse_matchcons.
        """
        for i, case in enumerate(cases):
            pat = case.pattern
            next_case = self.new_label(f"match_next_{i}")
            bound_locals: list[str] = []

            if isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name is None:
                # `case _`: wildcard, drop the value and compile body.
                self.fn.drop()
                self._compile_body(case.body)
                self.fn.label(end_label)
                return
            if isinstance(pat, ast.MatchClass):
                # `case Cons(p)` or `case Cons()` — class pattern.
                if not isinstance(pat.cls, ast.Name) or pat.cls.id not in self.constructors:
                    raise SyntaxError(
                        f"line {pat.col_offset}: unknown constructor in match case"
                    )
                tag, has_payload = self.constructors[pat.cls.id]
                self.fn.op(Op.OPfirst)
                self._emit_intb_or_int(tag)
                self.fn.op(Op.OPeq)
                self.fn.else_(next_case)
                if has_payload:
                    self.fn.opb(Op.OPfetchb, 1)
                    if pat.patterns:
                        sub = pat.patterns[0]
                        if isinstance(sub, ast.MatchAs) and sub.pattern is None and sub.name:
                            bn = sub.name
                            idx = self.scope.declare(bn)
                            new_locals_after_args = (idx + 1) - self.fn.nargs
                            if new_locals_after_args > self.nlocals_high_water:
                                self.nlocals_high_water = new_locals_after_args
                            self.fn.opb(Op.OPsetlocalb, idx)
                            bound_locals.append(bn)
                        else:
                            self.fn.drop()
                    else:
                        self.fn.drop()
                else:
                    self.fn.drop()
            elif isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name:
                # `case name` in Python — that's actually a binding,
                # not a value match. For our purposes we interpret it
                # as a constructor name match (if known).
                if pat.name in self.constructors:
                    tag, has_payload = self.constructors[pat.name]
                    if has_payload:
                        raise SyntaxError(
                            f"constructor {pat.name!r} needs `Cons(_)` pattern"
                        )
                    self.fn.op(Op.OPfirst)
                    self._emit_intb_or_int(tag)
                    self.fn.op(Op.OPeq)
                    self.fn.else_(next_case)
                    self.fn.drop()
                else:
                    raise SyntaxError(
                        f"unknown constructor {pat.name!r} in match case"
                    )
            else:
                raise SyntaxError(
                    f"unsupported match pattern: {ast.dump(pat)[:60]}"
                )

            self._compile_body(case.body)
            for nm in bound_locals:
                self.scope.unbind(nm)
            self.fn.goto(end_label)
            self.fn.label(next_case)

        # If no `_` wildcard fall-through: drop value, push nil.
        self.fn.drop()
        self.fn.op(Op.OPnil)
        self.fn.label(end_label)

    # ---- expressions ----------------------------------------------------
    def _compile_expr(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            v = node.value
            if v is None:
                self.fn.op(Op.OPnil)
            elif isinstance(v, bool):
                self._emit_intb_or_int(1 if v else 0)
            elif isinstance(v, int):
                self._emit_intb_or_int(v)
            elif isinstance(v, str):
                idx = self.enc.add_global(v)
                self._emit_get_global(idx)
            else:
                raise SyntaxError(f"unsupported constant {v!r}")
            return
        if isinstance(node, ast.Name):
            self._compile_name(node.id)
            return
        if isinstance(node, ast.BinOp):
            op = _BINOPS.get(type(node.op))
            if op is None:
                raise SyntaxError(
                    f"unsupported binop {type(node.op).__name__}"
                )
            self._compile_expr(node.left)
            self._compile_expr(node.right)
            self.fn.op(op)
            return
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                # `-X` — keep parity with Metal's specialcase for `-INT`
                if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, int):
                    self.fn.int_(-node.operand.value)
                    return
                self._compile_expr(node.operand)
                self.fn.op(Op.OPneg)
                return
            if isinstance(node.op, ast.Invert):
                self._compile_expr(node.operand)
                self.fn.op(Op.OPnot)
                return
            if isinstance(node.op, ast.Not):
                self._compile_expr(node.operand)
                self.fn.op(Op.OPnon)
                return
            raise SyntaxError(f"unsupported unaryop {type(node.op).__name__}")
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise SyntaxError("chained comparisons not yet supported")
            op = _CMPOPS.get(type(node.ops[0]))
            if op is None:
                raise SyntaxError(
                    f"unsupported compare {type(node.ops[0]).__name__}"
                )
            self._compile_expr(node.left)
            self._compile_expr(node.comparators[0])
            self.fn.op(op)
            return
        if isinstance(node, ast.BoolOp):
            # short-circuit `and` / `or`
            jmp_op = Op.OPnon if isinstance(node.op, ast.Or) else None
            skip_label = self.new_label("bool")
            # First operand.
            self._compile_expr(node.values[0])
            for v in node.values[1:]:
                self.fn.op(Op.OPdup)
                if isinstance(node.op, ast.Or):
                    self.fn.op(Op.OPnon)
                self.fn.else_(skip_label)
                self.fn.drop()
                self._compile_expr(v)
            self.fn.label(skip_label)
            return
        if isinstance(node, ast.IfExp):
            # X if COND else Y  (ternary)
            self._compile_expr(node.test)
            else_label = self.new_label("ifx_else")
            end_label = self.new_label("ifx_end")
            self.fn.else_(else_label)
            self._compile_expr(node.body)
            self.fn.goto(end_label)
            self.fn.label(else_label)
            self._compile_expr(node.orelse)
            self.fn.label(end_label)
            return
        if isinstance(node, ast.Call):
            return self._compile_call(node)
        if isinstance(node, ast.Attribute):
            # `obj.field` — emit obj, then OPfetchb fieldidx.
            self._compile_expr(node.value)
            if node.attr in self.struct_fields:
                idx, _ = self.struct_fields[node.attr]
                if 0 <= idx <= 255:
                    self.fn.opb(Op.OPfetchb, idx)
                else:
                    self.fn.int_(idx)
                    self.fn.op(Op.OPfetch)
            else:
                raise SyntaxError(
                    f"line {node.lineno}: unknown field {node.attr!r}"
                )
            return
        if isinstance(node, (ast.List, ast.Tuple)):
            # tuple/array literal — same opcode shape as Metal.
            for e in node.elts:
                self._compile_expr(e)
            n = len(node.elts)
            if 0 <= n <= 255:
                self.fn.opb(Op.OPdeftabb, n)
            else:
                self.fn.int_(n)
                self.fn.op(Op.OPdeftab)
            return
        raise SyntaxError(
            f"line {getattr(node, 'lineno', '?')}: unsupported expression "
            f"{ast.dump(node)[:60]}"
        )

    def _compile_name(self, name: str) -> None:
        if self.scope and (loc := self.scope.lookup(name)) is not None:
            self.fn.opb(Op.OPgetlocalb, loc)
            return
        if name in self.globals_by_name:
            self._emit_get_global(self.globals_by_name[name])
            return
        if name in self.constructors:
            tag, has_payload = self.constructors[name]
            if has_payload:
                raise SyntaxError(
                    f"constructor {name!r} needs a payload — use `{name}(value)`"
                )
            self._emit_intb_or_int(tag)
            self.fn.opb(Op.OPdeftabb, 1)
            return
        if name in self.funs_by_name:
            # A user-function NAME used as a value (i.e. `loopcb(handler)`,
            # not `handler()`) is a function pointer — just push its
            # funtable index. Matches MTL `#fname`.
            fun_idx, _ = self.funs_by_name[name]
            self._emit_intb_or_int(fun_idx)
            return
        if name in BUILTINS:
            opcode, nargs = BUILTINS[name]
            if nargs != 0:
                raise SyntaxError(
                    f"builtin {name!r} takes {nargs} args but used as a value"
                )
            self.fn.op(opcode)
            return
        raise SyntaxError(f"unknown name {name!r}")

    def _compile_call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise SyntaxError(
                f"line {node.lineno}: only NAME(...) calls supported"
            )
        fname = node.func.id
        # Compile-time intrinsics — evaluate now, emit the literal.
        if fname == "ord" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and len(arg.value) >= 1:
                self._emit_intb_or_int(ord(arg.value[0]) & 0xFF)
                return
        # `lst(a, b, c)` builds an MTL cons list `a :: b :: c :: nil`.
        # Empty `lst()` is nil. Right-assoc, matches the cons codegen
        # in mtl_comp.py.
        if fname == "lst":
            if not node.args:
                self.fn.op(Op.OPnil)
                return
            self._compile_expr(node.args[0])
            for v in node.args[1:]:
                self._compile_expr(v)
            self.fn.op(Op.OPnil)
            for _ in node.args:
                self.fn.opb(Op.OPdeftabb, 2)
            return
        # `arr(a, b, c)` builds an MTL n-array `{a b c}` — same opcode
        # shape as a tuple, just a different conceptual type.
        if fname == "arr":
            for a in node.args:
                self._compile_expr(a)
            n = len(node.args)
            if 0 <= n <= 255:
                self.fn.opb(Op.OPdeftabb, n)
            else:
                self.fn.int_(n)
                self.fn.op(Op.OPdeftab)
            return
        # `call_(fn, a, b, c)` is MTL `call FN [a b c]` (dynamic dispatch).
        if fname == "call_":
            if not node.args:
                raise SyntaxError("call_() needs at least one arg (the function)")
            self._compile_expr(node.args[0])     # function value
            for a in node.args[1:]:
                self._compile_expr(a)
            n = len(node.args) - 1
            if 0 <= n <= 255:
                self.fn.opb(Op.OPcallrb, n)
            else:
                self.fn.int_(n)
                self.fn.op(Op.OPcallr)
            return
        # Sum-type constructor with payload — `done(p)`.
        if fname in self.constructors:
            tag, has_payload = self.constructors[fname]
            if not has_payload:
                raise SyntaxError(
                    f"constructor {fname!r} has no payload — use bare `{fname}`"
                )
            if len(node.args) != 1:
                raise SyntaxError(
                    f"constructor {fname!r} takes 1 payload arg, got {len(node.args)}"
                )
            self._emit_intb_or_int(tag)
            self._compile_expr(node.args[0])
            self.fn.opb(Op.OPdeftabb, 2)
            return
        # Struct creation — `Tcp(stateT=0, locT=...)`.
        if (node.keywords and not node.args
                and any(kw.arg in self.struct_fields for kw in node.keywords)):
            first = node.keywords[0].arg
            _, size = self.struct_fields[first]
            if 0 <= size <= 255:
                self.fn.opb(Op.OPmktabb, size)
            else:
                self.fn.int_(size)
                self.fn.op(Op.OPmktab)
            for kw in node.keywords:
                if kw.arg not in self.struct_fields:
                    raise SyntaxError(
                        f"line {node.lineno}: unknown struct field {kw.arg!r}"
                    )
                idx, _ = self.struct_fields[kw.arg]
                self._compile_expr(kw.value)
                if 0 <= idx <= 255:
                    self.fn.opb(Op.OPsetstructb, idx)
                else:
                    self.fn.int_(idx)
                    self.fn.op(Op.OPsetstruct)
            return
        # User function takes priority over builtin (mtl_comp v6 fix).
        if fname in self.funs_by_name:
            fun_idx, nargs = self.funs_by_name[fname]
            if len(node.args) != nargs:
                raise SyntaxError(
                    f"{fname} takes {nargs} args, got {len(node.args)}"
                )
            for a in node.args:
                self._compile_expr(a)
            self._emit_intb_or_int(fun_idx)
            self.fn.op(Op.OPexec)
            return
        if fname in BUILTINS:
            opcode, nargs = BUILTINS[fname]
            if len(node.args) != nargs:
                raise SyntaxError(
                    f"builtin {fname} takes {nargs} args, got {len(node.args)}"
                )
            for a in node.args:
                self._compile_expr(a)
            self.fn.op(opcode)
            return
        raise SyntaxError(f"line {node.lineno}: unknown function {fname!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("input", help=".py source file")
    ap.add_argument("-o", "--output", help="output .bin (default: INPUT.bin)")
    args = ap.parse_args()
    src_path = Path(args.input)
    out_path = Path(args.output) if args.output else src_path.with_suffix(".bin")
    c = PyCompiler()
    try:
        blob = c.compile_source(src_path.read_bytes().decode("utf-8"))
    except SyntaxError as e:
        print(f"compile error: {e}", file=sys.stderr)
        return 1
    out_path.write_bytes(blob)
    print(f"wrote {len(blob)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
