#!/usr/bin/env python3
"""Metal type graphs — a port of mtl_linux `src/vcomp/compiler_type.cpp`
(RedoXyde/mtl_linux @ 7e557a1), used by mtl_comp.py so that a program it
accepts is one the C++ `mtl_compiler` accepts too.

The C++ keeps a type as a graph of heap cells: a header of
`[TYPENAME code, actual, copy]` followed by the node's children. Unification
never rewrites a node, it links the loser's `actual` field to the winner
(`recunif`); instantiation walks the graph and copies every node that is not
a basic core type or a weak variable (`reccopytype`); generalisation does
not exist, instead `u` (UNDEF) variables are the ones `copytype` duplicates
and `w` (WEAK) variables are the ones it shares, and a WEAK node that gets
unified with a graph turns that graph's UNDEF nodes WEAK (`recgoweak`).

This module keeps the same shapes, operation for operation, so that the
`name : type` strings `recechograph` prints — `u0`/`w0` numbering included,
which is simply the number of variables already named while printing — come
out identical. Each function names the C++ one it mirrors.
"""

from __future__ import annotations

from typing import Any, Callable

# memory.h: TYPENAME_*
TYPENAME_UNDEF = 0
TYPENAME_FUN = 1
TYPENAME_LIST = 2
TYPENAME_TUPLE = 3
TYPENAME_TAB = 4
TYPENAME_WEAK = 5
TYPENAME_CORE = 6
TYPENAME_REC = 7

# memory.h: MTLERR_*
MTLERR_SN = -6
MTLERR_TYPE = -7


class MtlError(SyntaxError):
    """A compile error, carrying the `Compiler : ...` lines the C++ prints
    on the way out (the innermost first, as the C++ prints them)."""

    def __init__(self, lines: list[str] | str, code: int = MTLERR_SN) -> None:
        if isinstance(lines, str):
            lines = [lines]
        self.lines = list(lines)
        self.code = code
        super().__init__("\n".join(self.lines))

    def add(self, line: str) -> "MtlError":
        self.lines.append(line)
        self.args = ("\n".join(self.lines),)
        return self


class TypeDef:
    """The identity of a core type. The C++ stores the type's name string
    in the core node and `recunif` compares the two strings by *pointer*
    (`TABGET(s,TYPEHEADER_LENGTH+1)!=TABGET(t,TYPEHEADER_LENGTH+1)`), so two
    `type X` declarations are two different types even though they print
    the same. One TypeDef per declaration reproduces that."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"TypeDef({self.name!r})"


class TNode:
    """One type cell. `code` is TYPENAME_*; `actual` is the unification
    link; `copy` is the scratch field reccopytype/recgoweak use."""

    __slots__ = ("code", "actual", "copy", "a", "b", "items", "tdef", "args", "rec")

    def __init__(self, code: int) -> None:
        self.code = code
        self.actual: TNode | None = None
        self.copy: Any = None
        self.a: TNode | None = None        # FUN: args tuple; LIST/TAB: element
        self.b: TNode | None = None        # FUN: result
        self.items: list[TNode] | None = None  # TUPLE
        self.tdef: TypeDef | None = None   # CORE
        self.args: list[TNode] | None = None   # CORE: parameters, or None
        self.rec: int = 0                  # REC: ancestor distance

    def __repr__(self) -> str:
        return f"<TNode {echograph(self)}>"


# compiler_type.cpp: createnodetypecore(const char*) / createnodetypecore(int)
def createnodetypecore(tdef: TypeDef) -> TNode:
    n = TNode(TYPENAME_CORE)
    n.tdef = tdef
    return n


# compiler_type.cpp: createnodetype
def createnodetype(code: int) -> TNode:
    return TNode(code)


# compiler_type.cpp: createnodetuple — the C++ pops `size` nodes off the
# stack; here the caller hands them over in stack order.
def createnodetuple(items: list[TNode]) -> TNode:
    n = TNode(TYPENAME_TUPLE)
    n.items = list(items)
    return n


def createnodefun(args: TNode, result: TNode) -> TNode:
    n = TNode(TYPENAME_FUN)
    n.a = args
    n.b = result
    return n


# compiler_type.cpp: actualtype
def actualtype(p: TNode) -> TNode:
    while p.actual is not None:
        p = p.actual
    return p


# parser.cpp: isletter / isletnum / islabel / isdecimal. Note isdecimal("")
# is true in the C++ (the loop never runs), so a bare `w` is a weak variable
# and a bare `r` is `r0`.
def _isletter(c: str) -> bool:
    return ("A" <= c <= "Z") or ("a" <= c <= "z") or c == "_"


def _isletnum(c: str) -> bool:
    return _isletter(c) or ("0" <= c <= "9")


def islabel(s: str) -> bool:
    return bool(s) and _isletter(s[0]) and all(_isletnum(c) for c in s[1:])


def isdecimal(s: str) -> bool:
    return all("0" <= c <= "9" for c in s)


def mtl_atoi(s: str) -> int:
    i = 0
    for c in s:
        if not ("0" <= c <= "9"):
            break
        i = i * 10 + ord(c) - 48
    return i


class StringTokens:
    """parser.cpp::gettoken restricted to what a type string needs — the
    `Parser(m->term, src)` creategraph builds for `coretype[]` entries such
    as `fun[list[u0 u1] u0]u1`."""

    def __init__(self, src: str) -> None:
        self.toks: list[str] = []
        i, n = 0, len(src)
        while i < n:
            c = src[i]
            if c <= " ":
                i += 1
                continue
            if _isletnum(c):
                j = i
                while j < n and _isletnum(src[j]):
                    j += 1
                self.toks.append(src[i:j])
                i = j
                continue
            self.toks.append(c)
            i += 1
        self.pos = 0

    def next(self) -> str | None:
        if self.pos >= len(self.toks):
            self.pos += 1
            return None
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def giveback(self) -> None:
        self.pos -= 1


# compiler_type.cpp: parsegraph. `labels` is the C++ label list — a list of
# (name, node) pairs, newest last; `rnode` is a one-element list standing in
# for the `intptr_t* rnode` out-parameter. `searchtype` is the compiler's
# searchtype(env, name) and returns the core node of a declared type.
def parsegraph(ts: Any, searchtype: Callable[[str], TNode | None], mono: int,
               rec: int, labels: list[tuple[str, TNode]], newvars: int,
               rnode: list[int]) -> TNode:
    tok = ts.next()
    if tok is None:
        raise MtlError("Compiler : uncomplete type reaches EOF")
    for name, node in reversed(labels):          # searchlabel_byname
        if name == tok:
            return node
    if tok[0] == "w" and isdecimal(tok[1:]):
        n = createnodetype(TYPENAME_WEAK)
        labels.append((tok, n))
        return n
    if tok == "_":
        return createnodetype(TYPENAME_WEAK)
    if tok[0] == "u" and isdecimal(tok[1:]):
        if mono:
            raise MtlError(f"Compiler : polymorphism ({tok}) not accepted here")
        if not newvars:
            raise MtlError(f"Compiler : cannot accept linked variable {tok} here")
        n = createnodetype(TYPENAME_UNDEF)
        labels.append((tok, n))
        return n
    if tok[0] == "r" and isdecimal(tok[1:]):
        i = mtl_atoi(tok[1:])
        if i < 0 or i >= rec:
            raise MtlError(f"Compiler : recursivity out of range {i} [0 {rec}[")
        n = createnodetype(TYPENAME_REC)
        n.rec = i
        rnode[0] = 1
        return n
    if tok == "tab":
        n = createnodetype(TYPENAME_TAB)
        n.a = parsegraph(ts, searchtype, mono, rec + 1, labels, newvars, rnode)
        return n
    if tok == "list":
        n = createnodetype(TYPENAME_LIST)
        n.a = parsegraph(ts, searchtype, mono, rec + 1, labels, newvars, rnode)
        return n
    if tok == "fun":
        n = createnodetype(TYPENAME_FUN)
        if newvars:
            n.a = parsegraph(ts, searchtype, mono, rec + 1, labels, 1, rnode)
            nblab = 0
        else:
            before = len(labels)
            n.a = parsegraph(ts, searchtype, mono, rec + 1, labels, 1, rnode)
            nblab = len(labels) - before
        n.b = parsegraph(ts, searchtype, mono, rec + 1, labels, newvars, rnode)
        if nblab:                                 # removenlabels(labels,nblab)
            del labels[len(labels) - nblab:]
        return n
    if tok == "[":
        items: list[TNode] = []
        while True:
            t = ts.next()
            if t is None:
                raise MtlError("Compiler : uncomplete type reaches EOF")
            if t == "]":
                return createnodetuple(items)
            ts.giveback()
            items.append(parsegraph(ts, searchtype, mono, rec + 1, labels, newvars, rnode))
    if islabel(tok):
        q = searchtype(tok)
        if q is None:
            raise MtlError(f"Compiler : unknown type {tok}")
        if q.args is None:
            return q
        n = createnodetypecore(q.tdef)
        t = ts.next()
        if t is None:
            raise MtlError("Compiler : '(' expected (found EOF)")
        if t != "(":
            raise MtlError(f"Compiler : '(' expected (found '{t}')")
        n.args = [parsegraph(ts, searchtype, mono, rec + 1, labels, newvars, rnode)
                  for _ in q.args]
        t = ts.next()
        if t is None:
            raise MtlError("Compiler : ')' expected (found EOF)")
        if t != ")":
            raise MtlError(f"Compiler : ')' expected (found '{t}')")
        return n
    raise MtlError(f"Compiler : unknown token {tok}")


# compiler_type.cpp: parse_rnode — ties `rN` nodes to their N-th ancestor
# through the `actual` field. `stack` is the ancestor list, parent last.
def parse_rnode(p: TNode, stack: list[TNode]) -> None:
    c = p.code
    if c in (TYPENAME_WEAK, TYPENAME_UNDEF):
        return
    if c == TYPENAME_CORE and p.args is None:
        return
    if c == TYPENAME_REC:
        p.actual = stack[len(stack) - 1 - p.rec]
        return
    stack.append(p)
    if c == TYPENAME_CORE:
        for x in p.args:
            parse_rnode(x, stack)
    elif c == TYPENAME_FUN:
        parse_rnode(p.a, stack)
        parse_rnode(p.b, stack)
    elif c in (TYPENAME_LIST, TYPENAME_TAB):
        parse_rnode(p.a, stack)
    elif c == TYPENAME_TUPLE:
        for x in p.items:
            parse_rnode(x, stack)
    stack.pop()


# compiler_type.cpp: creategraph (the three overloads: fresh label list, a
# caller's label list, or a string source).
def creategraph(ts: Any, searchtype: Callable[[str], TNode | None], mono: int,
                labels: list[tuple[str, TNode]] | None = None) -> TNode:
    if labels is None:
        labels = []
    rnode = [0]
    n = parsegraph(ts, searchtype, mono, 0, labels, 0, rnode)
    if rnode[0]:
        parse_rnode(n, [])
    return n


def creategraph_str(src: str, searchtype: Callable[[str], TNode | None],
                    mono: int = 0) -> TNode:
    return creategraph(StringTokens(src), searchtype, mono)


# compiler_type.cpp: recechograph. `anc` is the ancestor stack (parent
# last); `labels` the variables already named, newest last — a new
# variable is named after the count so far, u and w sharing the counter.
def recechograph(out: list[str], p: TNode, anc: list[TNode],
                 labels: list[tuple[str, TNode]]) -> None:
    p = actualtype(p)
    for i in range(len(anc)):
        if anc[len(anc) - 1 - i] is p:
            out.append(f"r{i}")
            return
    anc.append(p)
    v = p.code
    if v == TYPENAME_CORE:
        out.append(p.tdef.name)
        if p.args is not None:
            out.append("(")
            for i, x in enumerate(p.args):
                if i:
                    out.append(" ")
                recechograph(out, x, anc, labels)
            out.append(")")
    elif v == TYPENAME_FUN:
        out.append("fun ")
        recechograph(out, p.a, anc, labels)
        out.append(" ")
        recechograph(out, p.b, anc, labels)
    elif v == TYPENAME_LIST:
        out.append("list ")
        recechograph(out, p.a, anc, labels)
    elif v == TYPENAME_TAB:
        out.append("tab ")
        recechograph(out, p.a, anc, labels)
    elif v == TYPENAME_TUPLE:
        out.append("[")
        for i, x in enumerate(p.items):
            if i:
                out.append(" ")
            recechograph(out, x, anc, labels)
        out.append("]")
    elif v in (TYPENAME_UNDEF, TYPENAME_WEAK):
        for name, node in reversed(labels):          # searchlabel_byval
            if node is p:
                out.append(name)
                break
        else:
            name = ("u" if v == TYPENAME_UNDEF else "w") + str(len(labels))
            labels.append((name, p))
            out.append(name)
    anc.pop()


# compiler_type.cpp: echograph
def echograph(p: TNode) -> str:
    out: list[str] = []
    recechograph(out, p, [], [])
    return "".join(out)


# compiler_type.cpp: reccopytype
def reccopytype(p: TNode) -> TNode:
    p = actualtype(p)
    if p.copy is not None:
        return p.copy
    c = p.code
    if c == TYPENAME_CORE and p.args is None:
        return p
    if c == TYPENAME_TUPLE:
        q = TNode(TYPENAME_TUPLE)
        q.items = [None] * len(p.items)  # type: ignore[list-item]
        p.copy = q
        for i, x in enumerate(p.items):
            q.items[i] = reccopytype(x)
        return q
    if c == TYPENAME_WEAK:
        return p
    if c == TYPENAME_CORE:
        q = createnodetypecore(p.tdef)
    else:
        q = createnodetype(c)
    p.copy = q
    if c == TYPENAME_FUN:
        q.a = reccopytype(p.a)
        q.b = reccopytype(p.b)
    elif c in (TYPENAME_LIST, TYPENAME_TAB):
        q.a = reccopytype(p.a)
    elif c == TYPENAME_CORE:
        q.args = [reccopytype(x) for x in p.args]
    return q


# compiler_type.cpp: recresetcopy
def recresetcopy(p: TNode) -> None:
    p = actualtype(p)
    if p.copy is None:
        return
    p.copy = None
    c = p.code
    if c == TYPENAME_WEAK:
        return
    if c == TYPENAME_CORE:
        if p.args is not None:
            for x in p.args:
                recresetcopy(x)
    elif c == TYPENAME_FUN:
        recresetcopy(p.a)
        recresetcopy(p.b)
    elif c in (TYPENAME_LIST, TYPENAME_TAB):
        recresetcopy(p.a)
    elif c == TYPENAME_TUPLE:
        for x in p.items:
            recresetcopy(x)


# compiler_type.cpp: copytype
def copytype(p: TNode) -> TNode:
    q = reccopytype(p)
    recresetcopy(p)
    return q


# compiler_type.cpp: recgoweak — UNDEF becomes WEAK throughout the graph.
def recgoweak(p: TNode) -> None:
    p = actualtype(p)
    if p.copy is not None:
        return
    c = p.code
    if c == TYPENAME_WEAK:
        return
    p.copy = True
    if c == TYPENAME_CORE:
        if p.args is not None:
            for x in p.args:
                recgoweak(x)
    elif c == TYPENAME_FUN:
        recgoweak(p.a)
        recgoweak(p.b)
    elif c in (TYPENAME_LIST, TYPENAME_TAB):
        recgoweak(p.a)
    elif c == TYPENAME_TUPLE:
        for x in p.items:
            recgoweak(x)
    elif c == TYPENAME_UNDEF:
        p.code = TYPENAME_WEAK


# compiler_type.cpp: echonode
def echonode(code: int, p: TNode) -> str:
    return {
        TYPENAME_CORE: p.tdef.name if p.tdef else "",
        TYPENAME_UNDEF: "u*",
        TYPENAME_WEAK: "w*",
        TYPENAME_TAB: "tab",
        TYPENAME_LIST: "list",
        TYPENAME_TUPLE: "tuple",
        TYPENAME_FUN: "fun",
    }.get(code, "")


def _unif_children(s: TNode, t: TNode, ns: int, recurse: Callable[[TNode, TNode], None]) -> None:
    """The `if (ns==nt)` tail shared by recunif and recunifbigger."""
    if ns in (TYPENAME_TAB, TYPENAME_LIST, TYPENAME_FUN):
        recurse(s.a, t.a)
        if ns == TYPENAME_FUN:
            recurse(s.b, t.b)
    elif ns == TYPENAME_TUPLE:
        if len(s.items) != len(t.items):
            raise MtlError([], MTLERR_TYPE)
        for x, y in zip(s.items, t.items):
            recurse(x, y)
    elif ns == TYPENAME_CORE:
        if s.args is None and t.args is None:
            return
        if s.args is None or t.args is None:
            raise MtlError([], MTLERR_TYPE)
        if len(s.args) != len(t.args):
            raise MtlError([], MTLERR_TYPE)
        for x, y in zip(s.args, t.args):
            recurse(x, y)


# compiler_type.cpp: recunif
def recunif(s: TNode, t: TNode) -> None:
    s = actualtype(s)
    t = actualtype(t)
    if s is t:
        return
    ns, nt = s.code, t.code
    ok = True
    if (ns not in (TYPENAME_UNDEF, TYPENAME_WEAK)
            and nt not in (TYPENAME_UNDEF, TYPENAME_WEAK)
            and ns != nt):
        ok = False
    elif ns == nt == TYPENAME_CORE and s.tdef is not t.tdef:
        ok = False
    if not ok:
        raise MtlError(
            f"Compiler : {echonode(ns, s)} cannot be unified with {echonode(nt, t)}",
            MTLERR_TYPE)
    vt, vs = t.actual, s.actual
    if ns == TYPENAME_UNDEF or (ns == TYPENAME_WEAK and nt != TYPENAME_UNDEF):
        s.actual = t
        if ns == TYPENAME_WEAK:
            recgoweak(t)
            recresetcopy(t)
    else:
        t.actual = s
        if nt == TYPENAME_WEAK:
            recgoweak(s)
            recresetcopy(s)
    if ns == nt:
        try:
            _unif_children(s, t, ns, recunif)
        except MtlError:
            t.actual, s.actual = vt, vs                  # restoreactual
            raise


# compiler_type.cpp: unif
def unif(x: TNode, y: TNode) -> None:
    try:
        recunif(x, y)
    except MtlError as e:
        e.add("Compiler :")
        e.add(" " + echograph(x))
        e.add("does not match with")
        e.add(" " + echograph(y))
        raise


# compiler_type.cpp: unif_argfun — [fun [arg]] on the stack: unify the
# function's argument tuple with the call's, hand back the result type.
def unif_argfun(fun: TNode, arg: TNode) -> TNode:
    fun = actualtype(fun)
    unif(fun.a, arg)
    return fun.b


# compiler_type.cpp: argsfromfun
def argsfromfun(f: TNode) -> TNode:
    return actualtype(f).a


# compiler_type.cpp: recunifbigger — the proto check: the function's type
# must be at least as general as its prototype's.
def recunifbigger(s: TNode, t: TNode) -> None:
    s0 = s
    s = actualtype(s)
    t = actualtype(t)
    if s is t:
        return
    ns, nt = s.code, t.code
    ok = True
    if (ns not in (TYPENAME_UNDEF, TYPENAME_WEAK)
            and nt not in (TYPENAME_UNDEF, TYPENAME_WEAK)
            and ns != nt):
        ok = False
    elif ns == nt == TYPENAME_CORE and s.tdef is not t.tdef:
        ok = False
    if not ok:
        raise MtlError(
            f"Compiler : {echonode(ns, s)} cannot be unified with {echonode(nt, t)}",
            MTLERR_TYPE)
    vt, vs = t.actual, s.actual
    if nt == TYPENAME_UNDEF and ns != TYPENAME_UNDEF:
        raise MtlError(
            f"Compiler : {echonode(ns, s)} is smaller than {echonode(nt, t)}",
            MTLERR_TYPE)
    if ns == TYPENAME_UNDEF and s0 is not s:
        raise MtlError("Compiler : u* already unified", MTLERR_TYPE)
    if ns == TYPENAME_UNDEF or (ns == TYPENAME_WEAK and nt != TYPENAME_UNDEF):
        s.actual = t
        if ns == TYPENAME_WEAK:
            recgoweak(t)
            recresetcopy(t)
    else:
        t.actual = s
        if nt == TYPENAME_WEAK:
            recgoweak(s)
            recresetcopy(s)
    if ns == nt:
        try:
            _unif_children(s, t, ns, recunifbigger)
        except MtlError:
            t.actual, s.actual = vt, vs
            raise


# compiler_type.cpp: unifbigger
def unifbigger(x: TNode, y: TNode) -> None:
    try:
        recunifbigger(x, y)
    except MtlError as e:
        e.add("Compiler :")
        e.add(" " + echograph(x))
        e.add("does not match with or is smaller than")
        e.add(" " + echograph(y))
        raise
