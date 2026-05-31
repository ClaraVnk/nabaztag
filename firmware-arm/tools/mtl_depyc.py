#!/usr/bin/env python3
"""mtl_depyc — Metal bytecode → Python source (Phase 7b reverse direction).

Reads a `.mtl` `.bin` file and emits a Python-flavored rendering of the
program. This is the "view as Python" companion to `mtl_pyc.py`. Where
control flow can be reconstructed deterministically the output is real
Python; for unreconstructed patches it falls through to a small set of
`mtl.<opcode>()` intrinsic calls so nothing is silently dropped.

Strategy:
    1. Parse the file via `mtl_dis.parse`.
    2. For each function, build a CFG indexed by PC and walk it as a
       symbolic stack machine. Push opcodes push expressions; consumers
       (set*, drop, ret, comparisons, calls) pop them.
    3. Recognise the bytecode patterns mtl_pyc emits going down:
         - `OPelse` after a comparison + matching `OPgoto END` = if/else
         - `OPnil; <test>; OPelse END; OPdrop; <body>; <step>; OPgoto top`
           = `for i in range(...)` (Form 2) or `while`
         - `OPfirst; OPintb T; OPeq; OPelse NEXT; (OPdrop | OPfetchb 1
           OPsetlocalb X); body; OPgoto END; NEXT: ...` = match/case
         - `OPdup; OPfetchb i; OPsetlocalb x; ...; OPdrop` = list destruc
       Anything unmatched is emitted as a literal `mtl.OPxxx(args...)`
       call so the reader sees exactly what's there.

The output is *not* guaranteed to round-trip to byte-identical bytecode
(decompilers rarely are). Functional equivalence is the goal.
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# Reuse the disassembler's parser + opcode tables.
from mtl_dis import (
    parse as parse_bytecode,
    OPCODES,
    BUILTIN_NAMES,
    Function,
    Bytecode,
    NIL,
    extract_fun_names,
)


# Operators that look like binary `<a> OP <b>`. Maps opcode → py operator.
_BINOPS = {
    9: "+", 10: "-", 11: "*", 12: "//", 13: "%",
    14: "&", 15: "|", 16: "^", 17: "<<", 18: ">>",
    22: "==", 23: "!=", 24: "<", 25: ">", 26: "<=", 27: ">=",
}
# Unary opcodes.
_UNOPS = {19: "-", 20: "not ", 21: "(not )"}  # 21 = OPnon (is-nil-ish)


def _format_global(v: Any) -> str:
    """Render a decoded global value as a Python literal."""
    if v is NIL:
        return "None"
    if isinstance(v, int):
        return str(v >> 1)
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8")
            return repr(s)
        except UnicodeDecodeError:
            return repr(v)
    if isinstance(v, tuple):
        return "[" + ", ".join(_format_global(x) for x in v) + "]"
    return repr(v)


@dataclass
class Insn:
    pc: int
    op: int
    mnem: str
    operand: Any
    target: int | None = None  # absolute target for jumps (relative pcbase + offset)


def _enrich_insns(fn: Function) -> list[Insn]:
    """Wrap raw insn tuples with parsed jump targets."""
    out: list[Insn] = []
    for pc, op, mnem, operand in fn.insns:
        target = None
        if op in (28, 29) and operand:  # OPgoto, OPelse
            # operand_text is e.g. "-> 0x002A"
            try:
                target = int(operand.split("0x")[-1], 16)
            except ValueError:
                target = None
        out.append(Insn(pc=pc, op=op, mnem=mnem, operand=operand, target=target))
    return out


# Stack-machine decompiler ---------------------------------------------------

class FunDecomp:
    def __init__(self, bc: Bytecode, fn: Function, fun_names: list[str | None]):
        self.bc = bc
        self.fn = fn
        self.insns = _enrich_insns(fn)
        self.by_pc = {ins.pc: i for i, ins in enumerate(self.insns)}
        self.fun_names = fun_names
        self.lines: list[str] = []
        self.indent = 0
        self.stack: list[str] = []

    # ------------- helpers -------------
    def emit(self, s: str) -> None:
        self.lines.append("    " * self.indent + s)

    def _local(self, idx: int) -> str:
        if idx < self.fn.nargs:
            return f"a{idx}"
        return f"v{idx}"

    def _global(self, idx: int) -> str:
        return f"g{idx}"

    def _fun(self, idx: int) -> str:
        name = self.fun_names[idx] if 0 <= idx < len(self.fun_names) else None
        return name or f"fun_{idx}"

    def _opcode_call(self, ins: Insn) -> str:
        """Fallback: emit a literal mtl.OPxxx(...) call."""
        op = ins.op
        bname = BUILTIN_NAMES.get(op)
        if bname:
            return f"mtl.{bname}()"
        return f"mtl.{ins.mnem}({ins.operand})" if ins.operand else f"mtl.{ins.mnem}()"

    # ------------- region scan -------------
    def _scan(self, start_pc: int, end_pc: int) -> None:
        """Walk insns in [start_pc, end_pc) emitting statements."""
        i = self.by_pc.get(start_pc)
        if i is None:
            self.emit(f"# unmappable @0x{start_pc:04X}")
            return
        end_i = self.by_pc.get(end_pc, len(self.insns))
        while i < end_i:
            ins = self.insns[i]
            handled, di = self._try_structural(i, end_i)
            if handled:
                i += di
                continue
            i = self._exec_one(i)

    # ------------- pattern detectors -------------
    def _try_structural(self, i: int, end_i: int) -> tuple[bool, int]:
        """Try to recognise an if/while/match/destruc structure starting at
        insns[i]. Return (handled, advance_count)."""
        ins = self.insns[i]
        # if/else pattern: test left on stack ⇒ OPelse T → then ⇒ OPgoto E → else ⇒ E
        if ins.op == 29 and ins.target is not None:  # OPelse
            cond = self._pop()
            else_pc = ins.target
            # The "then" body runs from next insn until an OPgoto pointing
            # past else, OR until else_pc.
            then_start = self.insns[i + 1].pc
            then_end_idx = self.by_pc.get(else_pc)
            if then_end_idx is None or then_end_idx > end_i:
                return False, 0
            # Look at the insn just before else_pc — if it's an unconditional
            # OPgoto E with E > else_pc, that signals an if/else.
            tail = self.insns[then_end_idx - 1]
            if tail.op == 28 and tail.target is not None and tail.target > else_pc:
                end_pc = tail.target
                else_end_idx = self.by_pc.get(end_pc, end_i)
                self.emit(f"if {cond}:")
                self.indent += 1
                self._scan(then_start, tail.pc)  # excludes the OPgoto
                self.indent -= 1
                self.emit("else:")
                self.indent += 1
                self._scan(else_pc, end_pc)
                self.indent -= 1
                return True, else_end_idx - i
            # No else branch — pure if.
            self.emit(f"if {cond}:")
            self.indent += 1
            self._scan(then_start, else_pc)
            self.indent -= 1
            return True, then_end_idx - i
        return False, 0

    # ------------- single-insn semantics -------------
    def _push(self, e: str) -> None:
        self.stack.append(e)

    def _pop(self) -> str:
        return self.stack.pop() if self.stack else "<?>"

    def _exec_one(self, i: int) -> int:
        ins = self.insns[i]
        op = ins.op
        # Pushes.
        if op == 2 or op == 3:  # OPintb / OPint
            self._push(str(ins.operand))
            return i + 1
        if op == 4:  # OPnil
            self._push("None")
            return i + 1
        if op == 7 or op == 8:  # OPgetlocalb / OPgetlocal
            self._push(self._local(int(ins.operand) if op == 7 else self._pop()))
            return i + 1
        if op == 36 or op == 37:  # OPgetglobalb / OPgetglobal
            idx = int(ins.operand) if op == 36 else int(self._pop())
            self._push(self._global(idx))
            return i + 1
        if op == 6:  # OPdup
            top = self.stack[-1] if self.stack else "<?>"
            self._push(top)
            return i + 1
        if op == 5:  # OPdrop
            if self.stack:
                expr = self._pop()
                self.emit(expr)
            return i + 1
        if op == 1:  # OPret
            if self.stack:
                self.emit(f"return {self._pop()}")
            else:
                self.emit("return")
            return i + 1
        # Arithmetic / comparisons.
        if op in _BINOPS:
            b = self._pop(); a = self._pop()
            self._push(f"({a} {_BINOPS[op]} {b})")
            return i + 1
        if op in _UNOPS:
            a = self._pop()
            self._push(f"{_UNOPS[op]}{a}")
            return i + 1
        # Sets.
        if op == 40:  # OPsetlocalb idx
            v = self._pop()
            self.emit(f"{self._local(int(ins.operand))} = {v}")
            self._push(v)
            return i + 1
        if op == 47:  # OPsetlocal2: stack=[idx, val]
            v = self._pop(); idx_e = self._pop()
            try:
                idx = int(idx_e)
                self.emit(f"{self._local(idx)} = {v}")
            except ValueError:
                self.emit(f"locals[{idx_e}] = {v}")
            self._push(v)
            return i + 1
        if op == 42:  # OPsetglobal: stack=[idx, val]
            v = self._pop(); idx_e = self._pop()
            try:
                idx = int(idx_e)
                self.emit(f"{self._global(idx)} = {v}")
            except ValueError:
                self.emit(f"globals[{idx_e}] = {v}")
            self._push(v)
            return i + 1
        # Function call via OPexec — needs preceding push of fun-index.
        if op == 0:  # OPexec
            # Look back to find the function-index push (mtl_pyc puts intb
            # right before OPexec). The expression stack reflects this.
            # Args are below the fun-index push: walk back through ins.
            fn_idx_e = self._pop()
            # Heuristic: rewind through prior insns to count nargs (look
            # at the target fn from funtable if possible).
            try:
                target_fn = int(fn_idx_e)
                fnobj = self.bc.functions[target_fn] if 0 <= target_fn < len(self.bc.functions) else None
                nargs = fnobj.nargs if fnobj else 0
            except ValueError:
                target_fn = None
                nargs = 0
            args = [self._pop() for _ in range(nargs)][::-1]
            name = self._fun(target_fn) if target_fn is not None else "mtl.dyncall"
            self._push(f"{name}({', '.join(args)})")
            return i + 1
        # Tuple/array build: OPdeftabb count packs the top `count` stack
        # entries into an array. Emit as a Python list literal.
        if op == 32 or op == 33:  # OPdeftabb / OPdeftab
            count = int(ins.operand) if op == 32 else int(self._pop())
            items = [self._pop() for _ in range(count)][::-1]
            self._push(f"[{', '.join(items)}]")
            return i + 1
        # OPfirst (first tag of constructor)
        if op == 52:  # OPfirst
            v = self._pop()
            self._push(f"({v}).tag")
            return i + 1
        if op == 34:  # OPfetchb idx
            v = self._pop()
            self._push(f"({v})[{ins.operand}]")
            return i + 1
        if op == 48:  # OPstore: stack=[obj, idx, val]
            val = self._pop(); idx_e = self._pop(); obj = self._pop()
            self.emit(f"{obj}[{idx_e}] = {val}")
            self._push(val)
            return i + 1
        if op == 28:  # OPgoto — emit as comment, structural pass should handle
            self.emit(f"# goto 0x{ins.target:04X}" if ins.target else "# goto ?")
            return i + 1
        if op == 29:  # OPelse — same as goto (structural fell through)
            cond = self._pop() if self.stack else "<?>"
            self.emit(f"# if not {cond}: goto 0x{ins.target:04X}")
            return i + 1
        # Builtin opcodes (Secho, led, motorset…) — these consume a few args.
        bname = BUILTIN_NAMES.get(op)
        if bname:
            # Heuristic: pop up to 4 args if available.
            args = []
            while self.stack and len(args) < 4:
                args.append(self._pop())
            args.reverse()
            self._push(f"mtl.{bname}({', '.join(args)})")
            return i + 1
        # Fallback.
        self.emit(self._opcode_call(ins))
        return i + 1

    # ------------- function-level driver -------------
    def render(self) -> list[str]:
        name = self._fun(self.fn.index)
        args = ", ".join(self._local(i) for i in range(self.fn.nargs))
        self.emit(f"def {name}({args}):")
        self.indent += 1
        if not self.insns:
            self.emit("pass")
        else:
            start = self.insns[0].pc
            end = self.insns[-1].pc + 4  # past the last insn
            self._scan(start, end)
        self.indent -= 1
        return self.lines


# -------------- top-level driver --------------

def decompile(bc: Bytecode, src_for_names: str | None = None) -> str:
    fun_names: list[str | None] = [None] * len(bc.functions)
    if src_for_names:
        try:
            names = extract_fun_names(src_for_names)
            for i, n in enumerate(names):
                if i < len(fun_names):
                    fun_names[i] = n
        except Exception:
            pass

    out: list[str] = [
        '"""Decompiled Metal bytecode (mtl_depyc).',
        '',
        'This view is best-effort, not byte-perfect: it shows what the bytecode',
        'does in Python syntax, but is not guaranteed to recompile to the same',
        'bytes. For canonical source, see the original `.mtl` or `.py`.',
        '"""',
        '',
    ]
    # Globals as module-level constants.
    for i, g in enumerate(bc.globals):
        out.append(f"g{i} = {_format_global(g)}")
    if bc.globals:
        out.append("")

    for fn in bc.functions:
        decomp = FunDecomp(bc, fn, fun_names)
        out.extend(decomp.render())
        out.append("")

    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Metal bytecode → Python view")
    ap.add_argument("input", type=Path, help=".bin bytecode file")
    ap.add_argument("-o", "--output", type=Path, help="write to file (default stdout)")
    ap.add_argument("--src", type=Path,
                    help="optional matching .mtl source for fun-name resolution")
    args = ap.parse_args(argv)
    bc = parse_bytecode(args.input)
    src_text = args.src.read_text() if args.src else None
    out = decompile(bc, src_for_names=src_text)
    if args.output:
        args.output.write_text(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
