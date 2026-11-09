# Tools — Metal bytecode disassembler

A single-file Python disassembler for `mtl_compiler` (RedoXyde/mtl_linux)
output. Stdlib only, drop anywhere.

## What it does

Reads a `.bin` produced by `mtl_compiler` (the same blob the on-device
VM loads via `loaderInit()`) and pretty-prints:

1. **Header summary** — globals section size, code size, function count.
2. **Globals** — recursively decoded (nil / int / string / tuple of any),
   in the order the VM allocates them on the stack at boot.
3. **Functions** — each fun shown with `nargs`, `nlocals`, byte range,
   and a per-instruction disassembly with:
   - byte offset within the code section
   - opcode mnemonic (e.g. `OPSecholn`, `OPexec`, `OPgoto`)
   - decoded operand for the ones that take immediates (jump target,
     local/global index, immediate int)
   - inline annotations: builtin name (e.g. `; Secholn`) and resolved
     function index for `OPexec` callsites (`; → call fun#42`).

It also auto-detects the **`amber<hex_size>` HTTP wire wrapper** that
`/vl/bc.jsp` adds in front of the raw `.bin` and peels it transparently —
so you can disassemble the file as the rabbit downloads it OR the raw
`mtl_compiler` output, doesn't matter.

## How to use

```sh
# Pretty-printed text (default — verbose, but greppable)
python3 mtl_dis.py bytecode/bootcode_hybrid.bin

# Skip the globals dump (faster, smaller — when you only care about code)
python3 mtl_dis.py bytecode/bootcode_hybrid.bin --no-globals

# Resolve function names from the .mtl source (huge legibility win):
# instead of `fun#206`, see `fun#206 wifiInit`, and OPexec annotations
# read `; → MACecho` instead of `; → call fun#25`.
python3 mtl_dis.py path/to/boot.0.0.0.13.bin \
    --src path/to/boot.0.0.0.13.mtl --no-globals

# JSON output (for tooling / further processing)
python3 mtl_dis.py bytecode/bootcode_hybrid.bin --json > bc.json
```

Name resolution handles `proto NAME ARITY;;` forward-declarations
correctly (so `proto main 0;;` at the top of `boot.0.0.0.13.mtl`
reserves index 0 for `main` even though the definition is at the
bottom of the file). It warns when the source and bin disagree by
more than ±5 funs — usually that means the source has `ifdef` branches
that weren't preprocessed, and the annotations will be best-effort.

## Structural validation — `--check`

Before flashing a freshly-built `.sim`, gate it on structural sanity:

```sh
python3 mtl_dis.py path/to/boot.0.0.0.13.bin --check
# OK — 217 functions, 17111 bytes of code, no structural issues.
# exit 0

python3 mtl_dis.py path/to/corrupted.bin --check
# fun#42@0x0070: unknown opcode 200
# fun#88 (httpflash)@0x0123: OPgoto target 0x4000 is outside the function body [0x0100..0x0200)
# FAIL — 2 structural issue(s).
# exit 1
```

The checks:
- Every opcode is one of the 153 known opcodes
- Every operand fits inside the file
- Every `OPgoto`/`OPelse` target lands inside the function body
- Every `OPexec` is preceded by something pushable, and when the
  index is an immediate (`OPint <idx>` / `OPintb <idx>`), the index
  is within `[0, nbfun)`
- Every funtable entry lands inside the code section

This is no substitute for hardware testing, but it catches the
class of bugs the C++ compiler would otherwise let through to a
runtime crash — the kind of post-patch foot-gun that bricked Nabi
the first time. Wire it into CI / pre-flash hooks.

## Why it exists

Phase 7 (full Python rewrite of `mtl_compiler`) is a 2–4 week project.
This is a **stepping stone**: a tool that's immediately useful for
debugging hybrid bytecodes, understanding how the compiler emits code,
sanity-checking that our patches landed correctly, and — when we
eventually start Phase 7a — exposing the bytecode format clearly.

Concrete uses already today:

- **Verify our `mdns_boot_tick` hook landed in `fun loop`** by greping the
  disassembly for the new fun index and confirming the OPexec wires up.
- **Inspect the `httpflash` rewrite** to confirm `OPverifySig` (opcode 152)
  appears in the right place and the old `flashFirmware`-without-check
  path is gone.
- **Read globals** to find env defaults (e.g. `confGetServerUrl` default
  is among the globals as a string).
- **Spot-check function arity / locals counts** when bisecting a brick.

## Assembler — `mtl_asm.py`

The inverse of the disassembler: given a structured description of a
Metal program (globals + functions + instructions), it produces a `.bin`
**byte-for-byte identical** to what the C++ `mtl_compiler` emits for the
same source. This is the base layer of a future full Phase 7a Python
`mtl_compiler`: the parser/codegen builds the structured description,
this module turns it into bytes.

Used as a library:

```python
from mtl_asm import Encoder, Op
enc = Encoder()
enc.add_global(42)
enc.add_global("hello")
main_ = enc.new_function(nargs=0, nlocals=0)
main_.intb(1).else_("else_branch")        # if 1 then …
main_.intb(42).goto("end")
main_.label("else_branch").intb(99)        # else 99
main_.label("end").ret()
open("out.bin", "wb").write(enc.emit())
```

Self-test (round-trips a hand-built program through the disassembler +
checker):

```sh
python3 mtl_asm.py --self-test
```

`./run-tests.sh` runs that self-test, every fixture in `fixtures/`, and
the `bootcode.hybrid` round-trip in one go. Use it before trusting any
byte-identity claim in this file.

### Validation status

Cross-validated byte-for-byte against `mtl_compiler` (C++, RedoXyde
`mtl_linux`, pinned commit `7e557a15`) for:

| Source | Bytes | Tested for |
|---|---|---|
| `fun main=42;;` | 20 | minimal — OPintb + OPret |
| `var g=42;; const k="hello";; fun helper a=a+1;; fun main=helper g;;` | 49 | globals (int + string), function args, OPgetlocalb / OPgetglobalb, OPadd, OPexec call sites |
| `fun main = if 1 then 42 else 99;;` | 30 | control flow — OPelse + OPgoto with forward labels |

These three are `fixtures/tiny.mtl`, `fixtures/biggish.mtl` and
`fixtures/jump.mtl`, checked by `./run-tests.sh` against committed C++
output. The 20 / 49 / 30 are bodies; the files are 17 bytes longer.

All three round-trip exactly: `cmp` reports BYTE-IDENTICAL. That covers
all the encoder's tricky bits — relative-to-pcbase jump offsets, the
goofy `int << 1` tagging of int globals, the tuple/string discriminator
bits in the globals section, and the 3-byte `[nargs:u8][nlocals:u16]`
function header.

### .masm text assembly

`mtl_asm.py` accepts a text assembly format (`.masm`) and assembles it:

```sh
python3 mtl_asm.py program.masm -o program.bin
```

Format is line-oriented; example:

```
// .masm — Metal assembly (line-oriented, 1:1 with opcodes).
.global int 42
.global string "hello"

.fun main 0 0
    OPgetglobalb 0
    OPintb 1
    OPexec                  // call helper (fun#1)
    OPret
.end

.fun helper 1 0
    OPgetlocalb 0           // arg `a`
    OPintb 1
    OPadd
    OPret
.end
```

- Opcodes use their full vbc.h mnemonic (`OPintb`, `OPgoto`, …).
- Jumps take a label name (`OPgoto end`); labels are `name:` (often
  indented for readability). Forward labels are back-patched on close.
- `.global TYPE VALUE` for globals (`nil` / `int N` / `string "..."`
  / `bytes 0x.. 0x..` / `tuple` … `.end`).
- `.fun NAME nargs nlocals` opens a function; `.end` closes it.

### Round-trip: bin → masm → bin

`mtl_dis.py --format masm` emits .masm that `mtl_asm.py` accepts:

```sh
# Disassemble a bin into a .masm source
python3 mtl_dis.py program.bin --format masm > program.masm

# Re-assemble — should match byte-for-byte
python3 mtl_asm.py program.masm -o program.rt.bin
cmp program.bin program.rt.bin       # → identical
```

This round-trip is validated on the largest non-trivial inputs we
have:

| Bytecode                          | Size       | .masm lines | Result |
|-----------------------------------|------------|-------------|--------|
| Hand-written `jump`               | 30 B       | 13          | ✅ identical |
| Hand-written `biggish`            | 49 B       | 13          | ✅ identical |
| Real `boot.0.0.0.13.bin`          | 31 437 B   | 11 132      | ✅ identical |
| Real `bootcode_hybrid.bin`        | 103 525 B  | 34 447      | ✅ identical |

The last one is the hybrid Violet bytecode running on every Naboot
rabbit. So our Python toolchain can disassemble, edit, and reassemble
production bytecode byte-for-byte.

### What's NOT in mtl_asm.py yet

- A `.mtl` source parser. The text we ingest is the line-oriented IR
  `.masm`, not the original Metal source. The `.mtl` → AST → encoder
  layer is in `mtl_comp.py` (see below).
- A type checker. That lives in `mtl_comp.py` + `mtl_types.py` (the
  port of `compiler_type.cpp`), not here: the encoder takes the bytes it
  is given.
- Float globals (`mtl_float`). Encoder only does int/string/tuple/nil.
  Easy add, just not needed yet.

## `mtl_comp.py` — `.mtl` source compiler (Phase 7a v1)

A Python compiler from `.mtl` source to `.bin`, **byte-identical to the
C++ `mtl_compiler`** for the supported subset, and **typed the way the
C++ types** — see *Type checking* below. Built on top of the encoder;
uses recursive descent that mirrors `compiler_term.cpp`'s precedence
ladder, with the type inference of `compiler_type.cpp` (ported in
`mtl_types.py`) called at the same points.

```sh
python3 mtl_comp.py program.mtl -o program.bin   # envelope included
python3 mtl_comp.py program.mtl -o body.bin --bare
python3 mtl_comp.py program.mtl --types          # + one `Compiler : name : type` line per declaration
python3 mtl_comp.py program.mtl --check          # type-check only, write nothing
```

### Validated byte-for-byte against mtl_compiler (C++)

23 programs, committed in `fixtures/` next to the C++ reference each one
compiles to (`NAME.bin`, the bytes) and the types it printed for it
(`NAME.types`). Re-check them all, plus the 13 ill-typed programs in
`fixtures/illtyped/`, `mtl_asm --self-test` and the `bootcode.hybrid`
round-trip, with no container and no hardware:

```sh
./run-tests.sh          # one line per fixture; non-zero exit on any diff
```

Sizes below are the **body**; every `.bin` also carries the 17-byte
`amber`/`Mind` envelope (see *Which tool deals in which form*).

| Test               | Body  | What it exercises |
|--------------------|-------|-------------------|
| `tiny.mtl`         | 20 B  | proto + fun returning int constant |
| `jump.mtl`         | 30 B  | if/then/else with both branches |
| `biggish.mtl`      | 49 B  | var + const + multi-fun + user fun call |
| `rich.mtl`         | 51 B  | Secho (builtin) + set global + let-in + sequencing |
| `real_prog.mtl`    | 138 B | 4-level nested let, recursion, chained ifs |
| `char.mtl`         | 20 B  | char literal `'A'` |
| `funptr.mtl`       | 33 B  | function pointer reference `#fname` |
| `cons.mtl`         | 31 B  | list construction `a :: b :: nil` (right-assoc) |
| `tuple.mtl`        | 28 B  | tuple `[a b c d]` |
| `loop.mtl`         | 43 B  | `while COND do BODY` |
| `forloop.mtl`      | 60 B  | `for i=0; i<5; i+1 do ...` |
| `match.mtl`        | 68 B  | `type ... ;;` + `match x with (Cons -> y) | (Cons p -> y)` |
| `match2.mtl`       | 51 B  | match with `_` wildcard fallback |
| `array.mtl`        | 30 B  | array literal `{a b c d e}` |
| `statemachine.mtl` | 272 B | 4-constructor sum + nested match + if + globals |
| `realprog2.mtl`    | 254 B | sum + 5 funs + nested match + for + let + chained ifs + modulo |
| `ifdef.mtl`        | 49 B  | `ifdef NAME { ... } else { ... }` + `ifndef` |
| `call.mtl`         | 44 B  | `call FUN [a b c]` (dynamic dispatch via OPcallrb) |
| `forfor2.mtl`      | 54 B  | `for VAR=INIT; COND do BODY` (Form 2, auto-increment) |
| `destruc.mtl`      | 50 B  | `let TUPLE -> [a b c] in ...` (structured destructuring) |
| `struct.mtl`       | 38 B  | `type Point=[x y z];;` + `[x:1 y:2 z:3]` + `.field` read |
| `setfield.mtl`     | 46 B  | `set p.x = 99` (struct field write via OPstore) |
| `rec_result.mtl`   | 60 B  | recursive fun whose result type is fixed by the other branch (`f : fun [I] S`) |

The fixtures were rewritten from these descriptions when the suite was
committed; the originals were never in the repo. 13 of the 22 come out
at the byte count the old table claimed, which is good evidence they are
the same programs. The other 9 are a different program exercising the
same feature — the description is the contract, the byte count is a
historical note.

Until this suite's references were actually committed, `run-tests.sh`
reported 41 of 43 checks failing on a fresh checkout: `firmware-arm/
.gitignore` ignores `*.bin`, so the "committed" references never left
the machine that made them. `fixtures/.gitignore` now re-includes them.

### Type checking

`mtl_comp.py` runs the C++ compiler's type inference, call for call.
`mtl_types.py` is a port of `compiler_type.cpp` (node graphs, `recunif`,
`reccopytype`, `recgoweak`, `recunifbigger`, `recechograph`); every
`parse*` in `mtl_comp.py` makes the same `unif`/`copytype` calls at the
same places as its C++ counterpart in `compiler_term.cpp`,
`compiler_prog.cpp`, `compiler_var.cpp` and `compiler_file.cpp`, and the
natives carry the signatures of `stdlib_core.cpp` (`strlen : fun[S]I`,
`hd : fun[list u0]u0`, …) plus the firmware's `verifySig`, `netSaePw`,
`netSaeMs` and `pageRender`. The C++ types in one left-to-right pass
while it emits, and so does this; there is no separate inference pass.

Measured against the real `mtl_compiler` (RedoXyde/mtl_linux @ 7e557a1,
the build image's copy), which prints the type of every declaration
while it compiles:

- the production `bootcode.mtl`: **1209 of 1209** `Compiler :` lines
  identical, string for string, `u0`/`w0` numbering included
  (`fixtures/oracle/bootcode.types`), and the bytes still `cmp` clean;
- the bootloader `boot.0.0.0.13.mtl`: **427 of 427**
  (`fixtures/oracle/boot.0.0.0.13.types`);
- the 23 fixtures: **93 of 93** (one `.types` file per fixture, e.g. `fixtures/tiny.types`).

The variable numbering needed no normalising: `recechograph` names a
variable after the count of variables already named while printing that
one type, `u` and `w` sharing the counter, so it depends only on the
graph's shape and the print order, both of which are mirrored.

Both sources are not in the repo (Violet's bootcode is fetched at build
time; the bootloader lives in nabgcc), so `run-tests.sh` checks them
only when told where they are:

```sh
BOOTCODE_MTL=path/to/bootcode.mtl BOOTCODE_REF=path/to/reference-cxx-output.bin \
BOOT_MTL=path/to/boot.0.0.0.13.mtl ./run-tests.sh
```

**Refusals.** `fixtures/illtyped/` holds 13 programs the C++ refuses,
each confirmed on the real compiler before it was counted, with the
C++'s own words in `NAME.err`. `mtl_comp` refuses every one of them with
the same lines and writes no file:

| Fixture | What is wrong | The C++'s verdict |
|---|---|---|
| `state_in_int.mtl` | a `State` stored in a global initialised to `0` (the first draft of `statemachine.mtl`) | `State cannot be unified with I` |
| `arg_count.mtl` | `add3 1 2` | `unexpected term ';;'` / `function requires 3 arguments` |
| `arg_type.mtl` | `strlen 42` | `S cannot be unified with I` / `[S]` vs `[I]` |
| `match_wrong_cons.mtl` | a `Msg` matched against a `Wifi` constructor | `Wifi cannot be unified with Msg` |
| `list_mixed.mtl` | `1 :: "two" :: 3 :: nil` | `[S list S]` vs `[S list I]` |
| `struct_field_type.mtl` | `[x: "one"]` on a field declared `x: I` | `S cannot be unified with I` |
| `struct_field_use.mtl` | an unannotated field set to `1`, then to `"one"` | `S cannot be unified with I` |
| `proto_mismatch.mtl` | `proto twice 2;;` then `fun twice a = …` | `fun [I] I` is smaller than `fun [w0 w1] w2` / `prototype does not match` |
| `if_branches.mtl` | `then` an I, `else` an S | `S cannot be unified with I` |
| `cond_not_int.mtl` | `if "yes" then …` | `S cannot be unified with I` |
| `const_set.mtl` | `set K = 2` on a `const` | `'K' is a const, it cannot be set` |
| `for_init_type.mtl` | `for s = "a"; 1 do …` (Form 2 increments `s`) | `S cannot be unified with I` |
| `global_weak_reuse.mtl` | `var acc = nil` set to a `list I`, then a `list S` | `list S` vs `list I` |

A fourteenth candidate, a recursive function returning `f (n - 1)` on
one branch and `"done"` on the other, is *accepted* by the C++
(`f : fun [I] S`: a function's own result variable is shared, not
copied, inside its body); it is now `fixtures/rec_result.mtl`.

Two things the port changed in the codegen path, both read off the C++:
`tcpListen` takes **1** argument (`corecode[]`; the old `BUILTINS` table
said 2), and `- 0x10` is `OPintb 16; OPneg`, not `OPint -16`
(`parsea6` folds the sign only into a *decimal* literal). Neither
occurs in the production bootcode, which is why the bytes had matched
before.

Typing covers `.mtl` only. `mtl_pyc.py` does **not** share this: it
walks a Python AST and emits opcodes through its own copy of the
codegen (it imports only `BUILTINS`, `Scope` and `wire_wrap` from
`mtl_comp`), so none of these `unif` calls run for it. Giving it the
same guarantee means re-threading each typing call through its AST
walker, in the C++'s evaluation order — a separate job. Until then a
clean `mtl_pyc` run says nothing about types; compile the equivalent
`.mtl` through `mtl_comp --check` first.

`forloop.mtl` is interesting because the C++ codegen uses a clever
"skip-NEXT-on-first-iteration" trampoline pattern (NEXT is emitted in
bytecode BEFORE BODY, with the first iteration goto-ing past NEXT
straight to BODY, and subsequent iterations falling back into NEXT
from a goto at the end of BODY). Our Python codegen mirrors this
exactly — the bytecode order is identical.

### Supported `.mtl` subset

Top-level:
- `proto NAME ARITY;;` — reserve a fun index (forward decl)
- `var NAME [= EXPR];;` — mutable global (int/string/nil initializer)
- `const NAME = EXPR;;` — same shape as var; `set` on it is refused as
  the C++ refuses it (`'K' is a const, it cannot be set`)
- `proto NAME = fun [TYPES] TYPE;;` — a proto with an explicit type
- `type NAME = [f1 f2: TYPE …];;` — struct; `type NAME;;` — declared,
  defined later
- `ifdef NAME { … } [else { … }]`, `ifndef NAME { … }`
- `fun NAME [arg1 arg2 ...] = body;;`
- `type NAME = Cons1 | Cons2 _ | Cons3 [type];;` — sum-type
  declaration. Each constructor gets an auto-incrementing tag (0, 1,
  2, …); a bare name is CODE_CONS0 (no payload), with `_` or a type
  after the name it's CODE_CONS (one-arg). Constructors are referenced
  later as values (`initS`, `connected n`) and as match patterns
  (`(initS -> …)`, `(connected x -> …)`).

Expressions:
- Int literals (decimal, hex `0x..`)
- String literals `"..."` with `\n \t \r \\ \" \DECIMAL \$HEX` escapes
- Char literals `'a'` (single char, no escapes inside)
- `nil`
- Identifier — local var / global / builtin call / user fun call
  (juxtaposition is application: `MACecho netMac 0 1`)
- `#FUN` — function pointer (used for callbacks: `loopcb #handler`)
- `EXPR ; EXPR` — sequence (returns last; drops earlier values)
- `EXPR :: EXPR` — cons (right-assoc)
- `[a b c]` — n-tuple
- `&& ||` — short-circuit logical
- `== != < > <= >=` — comparison
- `+ - * / %` — arithmetic
- `& | ^ << >>` — bitwise + shift
- `!` (logical not), `-` (negation), `~` (bitwise not)
- `{a b c}` — n-element array (mutable; same opcode shape as tuple)
- `if EXPR then EXPR [else EXPR]`
- `while COND do BODY`
- `for VAR=INIT; COND; NEXT do BODY` — NEXT is auto-assigned to VAR
- `match X with (Cons -> body) | (Cons x -> body) | (_ -> default)`
  — sum-type pattern match. Each case is either a constructor name
  (CONS0, no binding), `Cons x` (binds payload to local `x`),
  `Cons _` (drops payload), or `_` (wildcard catch-all). Codegen
  uses `OPfirst` (tag fetch) + `OPeq` + `OPelse` chain, with
  back-patched gotos to the end label — byte-identical to the C++.
- `let VALUE -> NAME in BODY` — bind VALUE to a fresh local NAME;
  `let VALUE -> [a b _] in …` destructures
- `set NAME = EXPR` — mutate a global or local; `set NAME.f.g = EXPR`
  and `set NAME.(EXPR) = …` write a field or an index
- `[f1: v1 f2: v2]` struct creation, `x.f` / `x.(i)` reads, `call F [args]`
- `( EXPR )` — grouping

### Real boot.0.0.0.13.mtl — BYTE-IDENTICAL ✅

`python3 mtl_comp.py boot.0.0.0.13.mtl -o boot.py.bin` produces
36 263 bytes that match the C++ `mtl_compiler` output byte-for-byte
(`cmp` reports identical). 217 functions, 16 971 bytes of code,
17 028 bytes of globals — including the multi-KB `page_a` constant
cons-list that ships the embedded captive-portal HTML.

The Phase 7a v6 fixes that closed the last gaps:

- **Universal-newlines suppressed.** Python's default `read_text()`
  collapses CRLF → LF, but `boot.0.0.0.13.mtl` (and many older Metal
  sources) contains intentional `\r\n` byte sequences inside string
  literals. Fixed by reading the source as bytes and decoding latin-1.
- **`\<control-char>` line-continuation** in string literals. The C++
  parser eats the backslash and ALL subsequent control chars (< 32),
  collapsing source-level line-breaks with indentation. Python's
  string lexer now does the same byte-for-byte.
- **User-fun-shadows-builtin name resolution.** boot.mtl defines its
  own `fun itobin2 i = ...` that overrides the builtin opcode with
  the same name. The C++ resolver treats user-defined refs and
  builtins as a single namespace, last-write-wins. Python now checks
  `funs_by_name` BEFORE `BUILTINS`.

That's it — three fixes worth ~6 KB across the boot binary, all
isolated to the lexer + name-resolution layer.

### Real production `bootcode.mtl` — BYTE-IDENTICAL

The full Violet bootcode (374 365 bytes of source, 10 677 lines,
latin-1) compiles to the 105 377-byte body the C++ `mtl_compiler`
emitted for it — 472 functions, 58 911 bytes of code.

Three more gaps had to close first, each read off the reference
binary rather than guessed:

- **Integer literals wider than 32 bits.** `md5bytes` writes the MD5
  length masks at their natural 48-bit width (`0xFFFFFF000000&md5len`).
  The literal lands in a C++ `int`, so it wraps; the reference emits
  `OPint -16777216` at code offset 0x0D00. `mtl_comp` used to raise.
- **Computed index on the write side of `set`.** `set md5bloc.(j*2) = …`
  and `set ears.i = …`. The read side already parsed `.(EXPR)`; the
  write side only accepted declared field names. Same single-namespace
  rule as the read side: a step that is not a declared field name IS an
  index expression, evaluated inline with nothing pushed in front of it
  (reference 0x0DBD..0x0DDB).
- **Int globals whose tag shift overflows.** `const MSG_IDLE=0x7fffffff;;`
  — the `n << 1` tagging is done in a 32-bit word, so the top bit falls
  off. The reference globals section holds `fe ff ff ff` at offset
  18 156. The encoder used to raise.

`mtl_comp.py` emits the 17-byte `amber`/`Mind` envelope by default,
because `mtl_compiler` does — see *Which tool deals in which form*.

### Not yet supported

`update x with [...]` (record updates with explicit copy), the
`(a :: b)` list pattern in a `let`, multi-char escapes in char literals
`'\n'`, parametric types `type T(a) = …` beyond parsing them. A function
pointer to a native (`#Secho`) now compiles as the C++ does (`OPint` of
minus the opcode) but no fixture anchors it to a reference binary yet.

None of these occur in the production `bootcode.mtl`. Float literals and
float globals are still rejected outright.

These come incrementally — each is ~30-100 lines added with a fresh
byte-identical test case to anchor it.

## `mtl_pyc.py` — Python source compiler (Phase 7b)

The "view as Python" forward path: write Metal programs in idiomatic
Python 3 source and compile them to the same `.bin` the C++
`mtl_compiler` would emit from the equivalent `.mtl`. The rabbit still
executes Metal bytecode; Python is purely contributor-facing.

```sh
python3 mtl_pyc.py program.py -o program.bin
```

### Validated byte-for-byte from Python source

19 of them, in `fixtures/*.py`. Each is compared against the *same*
committed C++ reference as its `.mtl` twin, so the claim is that both
spellings converge on the bytes `mtl_compiler` emits — not merely that
Python and MTL agree with each other. `./run-tests.sh` checks them.

| Test               | Python idiom → MTL feature |
|--------------------|----------------------------|
| `tiny.py`          | `def main(): return 42` |
| `jump.py`          | `if … else` returning value |
| `rich.py`          | `Secho("hi")` builtin call + sequencing |
| `real_prog.py`     | nested assignments, chained ifs |
| `loop.py`          | `while x < 5: …` |
| `forfor2.py`       | `for i in range(n): …` (Form 2) |
| `char.py`          | `ord('A')` compile-time char |
| `funptr.py`        | bare function name as value (function pointer) |
| `cons.py`          | `lst(a, b, c)` builds a cons-chain |
| `tuple.py`         | Python list literal `[a, b, c, d]` → n-tuple |
| `array.py`         | `arr(a, b, c, d, e)` builds a mutable array |
| `match.py`         | Python 3.10 `match: case Cons: … case Cons(x): …` |
| `match2.py`        | match with `case _:` wildcard |
| `statemachine.py`  | sum-type `class State` + nested match |
| `struct.py`        | `class Point: x: int` + `Point(x=…)` + `p.x` |
| `setfield.py`      | `p.x = 99` struct field write |
| `ifdef.py`         | top-level `if FLAG:` / `if not FLAG:` → ifdef/ifndef |
| `call.py`          | `call_(f, a, b)` dynamic dispatch |
| `destruc.py`       | `[a, b, c] = expr` structured destructuring |

**There is no `biggish.py`**, though the old table listed one. `mtl_pyc`
always reserves fun#0 for `main`, and `fixtures/biggish.mtl` has no
`proto main 0;;`, so its `main` lands *last* — the two bins are the same
66 bytes with `helper` and `main` swapped in the funtable. Any MTL source
that omits `proto main` is outside what `mtl_pyc` can reach.

### Python ↔ MTL idiom mapping

| Python | MTL |
|--------|-----|
| `def f(a, b): return body` | `fun f a b = body;;` |
| `def main(): …` (always fun#0) | `proto main 0;; fun main = …;;` |
| `x = expr` (first time, in fn) | `let expr -> x in <rest>` |
| `x = expr` (already a local) | `set x = expr` |
| `x = expr` (top-level) | `var x = expr;;` (or `const …`) |
| `if a: b else: c` (stmt) | `if a then b else c` |
| `while c: body` | `while c do body` |
| `for i in range(n): body` | `for i=0; i<n do body` (Form 2) |
| `match x: case Cons: … case Cons(p): …` | `match x with (Cons -> …) | (Cons p -> …)` |
| `[a, b, c]` literal | `[a b c]` n-tuple |
| `[a, b, c] = expr` | `let expr -> [a b c] in …` |
| `arr(a, b, c)` | `{a b c}` array |
| `lst(a, b, c)` | `a :: b :: c :: nil` (right-assoc cons) |
| `ord('A')` | `'A'` (char literal) |
| `call_(f, a, b)` | `call f [a b]` |
| `class S: idle = (); done = (int,)` | `type S = idle | done _;;` |
| `class T: x: int; y: int` | `type T = [x y];;` |
| `T(x=1, y=2)` | `[x:1 y:2]` |
| `obj.field` | `obj.field` |
| `obj.field = v` | `set obj.field = v` |
| `if FLAG:` top-level | `ifdef FLAG { … }` |
| `if not FLAG:` top-level | `ifndef FLAG { … }` |

User-defined functions shadow builtins (same single-namespace rule as
the C++ compiler — last definition wins). Function names known at
module load time are resolved bare; arguments are positional.

### Idiomatic vs byte-identical

Python `for i in range(N)` has no explicit step expression, so
`mtl_pyc` emits MTL **for Form 2** (auto-increment). The C++ compiler
emits a different (Form 1) trampoline pattern when given the `.mtl`
syntax `for i=0; i<n; i+1 do …`, which has explicit step semantics
Python's `range` doesn't carry. Both forms are functionally identical;
the bytecode is just laid out differently. If byte-equivalent Form 1
output is needed, write the loop with explicit `while` in Python.

## `mtl_depyc.py` — bytecode → Python view (Phase 7b reverse)

The complement of `mtl_pyc`: read a `.bin` and emit a Python-flavored
rendering. Useful for reviewing what's deployed on the rabbit when only
the bytecode is available.

```sh
python3 mtl_depyc.py program.bin              # writes Python to stdout
python3 mtl_depyc.py program.bin --src program.mtl -o view.py
```

If `--src` is given, function names are resolved from the `.mtl`
source's `fun NAME=…` / `proto NAME …` decls (else functions appear as
`fun_0`, `fun_1`, …). This is best-effort: the decompiler reconstructs
if/else, returns, arithmetic and basic calls; un-recognised patches
fall through to literal `mtl.OPxxx(...)` calls so nothing is silently
dropped. Output is **not** guaranteed to recompile to the same bytes —
it's a reading aid, not a round-trip transform.

## Which tool deals in which form

A Metal `.bin` exists in two shapes, and the tools here are deliberately
split between them:

| Shape | 17 extra bytes? | Who produces it |
|---|---|---|
| **envelope** `"amber" + size_hex(8) + body + "Mind"` | yes | `mtl_compiler`, `mtl_comp.py`, `mtl_pyc.py` |
| **body** alone | no | `mtl_asm.py`; `mtl_dis.py` reads either and works on the body |

The envelope is the **compiler's own output format**, not something the
serving layer bolts on. An earlier version of this file, and the
docstring of `mtl_dis._strip_amber_wrapper`, blamed `/vl/bc.jsp` for it.
That was wrong, and it mattered: it is why `mtl_comp.py` shipped for a
while writing files 17 bytes short of what a rabbit will load. Two
measurements settle it:

- `bytecode/build-bytecode.sh` runs `"$CC" -s bootcode.mtl "$OUT"` with
  nothing between the compiler and the file, and `$OUT` — the blob that
  becomes `/data/bootcode.hybrid` — carries the envelope.
- `proto main 0;; fun main= 42;;` straight out of the real
  `mtl_compiler` is **37 bytes**: 20 of body and 17 of envelope. So does
  every one of the 23 committed references in `fixtures/`.

The reader on the device is `boot.mtl::getbytecode`, which does
`strsub content 13 len` — it expects the envelope.

So: **anything served to a rabbit keeps the envelope.** `mtl_comp.py
--bare` and `mtl_pyc.py --bare` drop it, which is what you want when
feeding `mtl_asm`/`mtl_dis` or diffing against `.masm` output, and is
never what you want in `/data`.

## Format references

All format details are derived from the canonical implementations:

- File layout: `nabgcc/src/vm/vloader.c::loaderInit / loaderSizeBC`
- Globals encoding: `vloader.c::loaderInitRec`
- Instruction set: `nabgcc/inc/vm/vbc.h` + `nabgcc/src/vm/vinterp.c`
- Stdlib opcode table: `mtl_linux/src/vcomp/stdlib_core.cpp`

The opcode + builtin-name tables in `mtl_dis.py` are vendored from those
sources. They drift when upstream adds opcodes — update via:

```sh
# When upstream changes:
grep -E '^#define OP[a-zA-Z]' .../nabgcc/inc/vm/vbc.h    # opcode list
sed -n '/corename\[\]/,/^};/p' .../stdlib_core.cpp        # builtin names
```

## Known limits

- No function-name resolution. The `.bin` doesn't carry names; we show
  `fun#42` instead of `fun foo=`. Adding name resolution requires either
  parsing the `.mtl` source alongside the `.bin` (Phase 7a territory)
  or a debug-info side-file that `mtl_compiler` doesn't currently emit.
- No control-flow reconstruction (if/then/else, for, match). Just raw
  bytecode + resolved jump targets. Reconstructing the high-level
  structure is Phase 7b ("view as Python" layer).
- Globals are shown as values; we don't yet correlate `OPgetglobalb 42`
  back to the matching global slot label. Mechanical to add.

