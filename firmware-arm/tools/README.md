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

# JSON output (for tooling / further processing)
python3 mtl_dis.py firmware/bootcode_hybrid.bin --json > bc.json
```

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
