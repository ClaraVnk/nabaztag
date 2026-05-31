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
python3 mtl_dis.py firmware/bootcode_hybrid.bin

# Skip the globals dump (faster, smaller — when you only care about code)
python3 mtl_dis.py firmware/bootcode_hybrid.bin --no-globals

# Resolve function names from the .mtl source (huge legibility win):
# instead of `fun#206`, see `fun#206 wifiInit`, and OPexec annotations
# read `; → MACecho` instead of `; → call fun#25`.
python3 mtl_dis.py path/to/boot.0.0.0.13.bin \
    --src path/to/boot.0.0.0.13.mtl --no-globals

# JSON output (for tooling / further processing)
python3 mtl_dis.py firmware/bootcode_hybrid.bin --json > bc.json
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

### Validation status

Cross-validated byte-for-byte against `mtl_compiler` (C++, RedoXyde
`mtl_linux`, pinned commit `7e557a15`) for:

| Source | Bytes | Tested for |
|---|---|---|
| `fun main=42;;` | 20 | minimal — OPintb + OPret |
| `var g=42;; const k="hello";; fun helper a=a+1;; fun main=helper g;;` | 49 | globals (int + string), function args, OPgetlocalb / OPgetglobalb, OPadd, OPexec call sites |
| `fun main = if 1 then 42 else 99;;` | 30 | control flow — OPelse + OPgoto with forward labels |

All three round-trip exactly: `cmp` reports BYTE-IDENTICAL. That covers
all the encoder's tricky bits — relative-to-pcbase jump offsets, the
goofy `int << 1` tagging of int globals, the tuple/string discriminator
bits in the globals section, and the 3-byte `[nargs:u8][nlocals:u16]`
function header.

### What's NOT in mtl_asm.py yet

- A parser. You build the program by direct Encoder API calls. The
  parser → AST → Encoder calls path is Phase 7a proper.
- A type checker. Metal has a small but real type system (used to
  disambiguate `strcmp` vs `vstrcmp`, choose `OPset*` variants, etc.).
  Phase 7a needs to port it.
- Float globals (`mtl_float`). Encoder only does int/string/tuple/nil.
  Easy add, just not needed yet.

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
