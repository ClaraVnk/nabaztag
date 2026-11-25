# Can a downloaded program escape Violet's VM on the Nabaztag v1?

A study, 2026-11-24/25, of whether the bytecode a v1 downloads from its server
can write or jump outside the sandbox Violet's firmware runs it in — the one
thing that would let the Terrier install a native loader without a PICkit
(IMPROVEMENTS.md §5). Study and emulator only: nothing was flashed, no rabbit,
no network to the Terrier. Every claim cites an address in one of the two
dumps or a run of `../emulator/tools/vm-escape-proof.sh`.

## Verdict

- **1.4b (Lazare, PIC18LF6525): no.** No VM instruction can write or branch
  outside the interpreter's arena in RAM; the native program counter is never
  reachable.
- **2.0b (Newbaztag's rabbit, PIC18F6627): no.** Same interpreter, same
  bounds, same verdict (it is one code base built twice, IMPROVEMENTS.md §1).

The escape is closed twice over: the interpreter bounds every address the
program can form, and even an arbitrary RAM write could not run native code of
ours, because the PIC18 is a Harvard machine (the program counter indexes
flash, never RAM) and neither firmware contains a single `TBLWT` or `EECON`
access — so there is no existing native gadget that writes flash for a
redirect to land on. **A loader still needs ICSP (a PICkit) the first time;
the VM is not a way around it.**

## How the proof runs

`../emulator/tools/vm-escape-proof.sh` (PASS/FAIL, shellcheck-clean), measured
on the dev VM 2026-11-24/25:

```
== 1. static: no flash/EEPROM-write opcode in either firmware
   violet-v1-1.4b.hex: TBLWT=0  EECON/EEDATA/EEADR=0
   nabaztag.hex:       TBLWT=0  EECON/EEDATA/EEADR=0
== 2. dynamic: the hostile program's stores, audited
   2.0b: 6405 indirect stores from the VM band [07A00,09080]; destinations A73..CFE; outside the arena 0xA70-0xCFF: 0
   1.4b: 6492 indirect stores from the VM band [07200,08C00]; destinations A70..CFE; outside the arena 0xA70-0xCFF: 0
== 3. control: native code writes a flash row on the same emulator
   control: flash: write row 00100 (latch[0]=A5)
PASS
```

1. **static** — `pic18dis.py dis | grep` on both dumps: zero `TBLWT`, zero
   access to `EECON1`/`EECON2`/`EEDATA`/`EEADR` (0xFA6-0xFA9). The only
   program-memory access in either firmware is `TBLRD` (strings and the opcode
   table). No native code writes flash or the data EEPROM, so a control-flow
   redirect, even if one were possible, has no gadget to reach.
2. **dynamic** — the hostile program `../emulator/tools/violet-vasm/escape.vasm`, assembled by
   Violet's own assembler, drives every store and load the VM has (`STR r,i`,
   `STR r,r`, `STR r,i,r`, the `LDR` forms, `BSR`/`RTS`) at the largest index a
   register or an immediate can carry, in an endless loop. `nabv1 --vm-audit
   LO HI` records the destination of every indirect byte store made from inside
   the interpreter. Over ~6 400 stores per firmware the destinations span only
   **0xA70-0xCFE** — the arena below — and not one lands in an SFR, the hardware
   return stack or `EECON1`. It never writes flash or the data EEPROM.
3. **control** — a native stub (`../emulator/tools/selfwrite_stub.py`:
   `TBLWT` + the `EECON1` WREN/EEPGD, 55h/AAh, WR sequence) **does** write a
   flash row on the same emulator. So the silence in step 2 is a measurement,
   not a model that cannot write flash. The emulator gained the flash-write
   path for this (`pic18.c` holding latches on `TBLWT`, `board.c` the `EECON1`
   state machine for program memory), the one Violet's firmware never uses.

## The VM in PIC RAM

The interpreter keeps the program's whole world in RAM 0x000-0xCFF, every piece
indexed by an 8-bit quantity added to a constant high byte, so no index can
carry past its 256-byte window:

| What | Where | How it is addressed |
|---|---|---|
| VM program counter (an **offset**, not a PIC address) | 0x00F:0x010 | advanced by `incf 0x0F; addwfc 0x10` in every handler |
| scratch | 0x000-0x010 | direct |
| VM registers r0-r15 | 0xA70-0xA7F | `lfsr 0,0xA70` + (opcode nibble & 0x0F) |
| current instruction's bytes | 0xA69-0xA6C | copied by the fetch loop |
| **VM data memory** | 0xB00-0xBFF | `lfsr 0,0xB00` + (8-bit index) |
| **VM call/arg stack** | 0xC00-0xCFF | `lfsr 0,0xC00` + (8-bit stack pointer at 0xA7F) |
| frame bytecode + files | serial-flash page cache at 0x4D0-0x9CF | read, bounds-checked against the frame length |

Addresses below are 1.4b's; 2.0b's handlers are the same code at shifted
addresses (the table in parentheses).

## Every memory and control instruction, and its bound

The opcode table has 200 entries (`0x00-0xC7`) at 0x16F3 (2.0b 0x1CFF,
3-byte entries); dispatch at 0x8B64 (2.0b 0x90D0) reads an entry into
PCLATH:PCL. Two guards in the fetch loop at 0x8A8E (2.0b 0x90F6) frame every
instruction:

- **opcode bound** (0x8B58): `movff 0xA69,TBLPTRL; movlw 0xC8; cpfslt TBLPTRL;
  bra 0x8998` — an opcode byte ≥ 0xC8 (200) never indexes the table.
- **program-counter bound** (0x8AC2-0x8AE4 and 0x8B00-0x8B1A): the effective
  read pointer (frame base 0x0A5-0x0A8 + the VM PC) is compared to the frame
  end stored at 0x204-0x207; the sign bit of the difference (`btfsc 0xA4,7`)
  sends a pointer past the end to 0x7144 (the VM stops). So a wild branch
  target cannot make the fetch read outside the frame buffer.

### Loads and stores (the write primitives)

| Op | Handler | Address it forms | Bound |
|---|---|---|---|
| `LD r,i` / arithmetic to a register | 0x72E4 etc. | 0xA70 + (nibble & 0x0F) | r0-r15, 0xA70-0xA7F |
| `STR r,i` (0x60) | 0x7300 → 0x7C48 | 0xB00 + imm8 | 0xB00-0xBFF |
| `LDR r,i` (0x50) | 0x72E4 | 0xB00 + imm8 | 0xB00-0xBFF |
| `STR r,r` (0x96) | 0x7BCA (2.0b 0x8324) | 0xB00 + (register value, 8-bit) | 0xB00-0xBFF |
| `STR r,i,r` (0x97) | 0x7C08 (2.0b 0x8362) | 0xB00 + (register value + imm, 8-bit) | 0xB00-0xBFF |
| `LDR r,r` / `LDR r,i,r` (0x94/0x95) | 0x7B74 / 0x7B96 | 0xB00 + 8-bit index | 0xB00-0xBFF |

The decisive line in every store is `lfsr 1,0xB00; addwf FSR1L,f; movlw 0;
addwfc FSR1H,f` (0x7BF6, 0x7C4A, 2.0b 0x8350): the base high byte is the
constant **0x0B**, the index is a single byte added to a zeroed `FSR1L`, so the
low-byte add never carries and `FSR1H` stays 0x0B. The maximum address a store
can form is **0xBFF**. The register index is always masked to four bits
(`andlw 0x0F`), so it never leaves the 16-register file. No store encodes, or
can compute, an address at or above 0xC00 for data, let alone an SFR (0xF60).

### The VM stack (`BSR`/`RTS`/`PUSH`/`PULL`)

`BSR` (0x81E4) and `PUSH` (0x8066) push through `lfsr 0,0xC00; addwf FSR0L,f;
movlw 0; addwfc FSR0H,f` with the stack pointer (RAM 0xA7F) as the 8-bit index:
the window is **0xC00-0xCFF**, high byte 0x0C, carry-free. A runaway recursion
wraps the pointer inside 0xC00-0xCFF; it cannot climb into the SFRs.

### Control transfers (`BRA`, `BEQ`…`BLS`, `BSR`, `RTIJ`, `BRAT`, `BSRT`)

These write the **VM** program counter (the offset at 0x00F:0x010), e.g. `BRA`
at 0x7E2C does `movff 0xA6A,0x010; movff 0xA6B,0x00F`. They never touch the
PIC's program counter, STKPTR or TOS. `BRAT`/`BSRT` add a register to the
target, so the target is program-controlled — but it is still only the VM PC,
re-bounds-checked at the next fetch (above). The worst a crafted branch does is
land on a byte inside the frame that is not an instruction boundary: the
interpreter then decodes frame bytes as bytecode (still interpreted, still
bounded), or runs past the end and stops. No path sets the native PC.

### Reads that touch the frame (`LDT`, `LDTW`, `INPUT`)

`LDT` (0x7C5A) and `LDTW`/`INPUT` read a table in the frame's own file data
through the page cache; they are read primitives (a value into a register),
with no write and no control transfer. Even a mis-indexed read only copies an
adjacent RAM byte into a VM register — an information leak at worst, bounded by
the same frame-length check, and never a way to execute code.

## Why no write could escape anyway

Suppose, contrary to the above, a store could reach any SFR. It still could not
run native code of ours:

- **Harvard.** The PIC18 program counter is a 21-bit index into flash
  (`pic18.c`: `pic->pc` addresses `pic->flash[]`; there is no execute-from-RAM
  path, and TOSU holds only bits 20:16 of a flash address). Bytes the program
  writes into RAM can never be fetched as instructions. A redirect (writing
  TOSU:TOSH:TOSL and returning) can only land on **existing flash**.
- **No gadget.** Neither firmware has a `TBLWT` or any `EECON` access
  (measured, step 1). There is no existing native routine that writes flash or
  the data EEPROM for a redirect to reuse. The firmware can read its flash
  (`TBLRD`) but never writes it.

So the chain "bytecode → native write → self-flash → loader" has no first link
on this hardware.

## The cheapest remaining route

Unchanged from IMPROVEMENTS.md §5: **ICSP, a PICkit on J2**, to install a
loader the first time. The VM gives no shortcut. Once a loader that *contains*
`TBLWT`+`EECON1` is flashed by ICSP, it — not the VM — can take signed images
over the poll's HTTP connection. The emulator now has the flash-write model to
develop and prove such a loader offline.

## A safe loader, sketched (not built)

- **Where.** A permanent loader at the top of flash (2.0b has 46 KB free above
  0xC8DC; 1.4b has 94 bytes and cannot host one — IMPROVEMENTS.md §4). The
  reset vector enters the loader; the loader validates the main image and jumps
  to it, or stays to receive a new one.
- **Keeping a known-good image bootable.** Write the new image block by block
  into a second region, set a valid-image flag **last**, and only then switch
  the boot pointer; the loader itself never rewrites its own row. A write
  interrupted by the brown-out (1.4b CONFIG2L on) or the watchdog then leaves
  the old image bootable.
- **Signature.** Ed25519 is too slow on this PIC (SHA-512 alone); HMAC-SHA-256
  with a per-rabbit key written at ICSP time is realistic (~0.1 s per 64-byte
  block at the measured SHA-1 cost).
- **Failure modes that still need a PICkit.** A corrupted or half-written
  loader row, a wrong per-rabbit key, a brown-out during the loader's own
  (never-done) self-update. The first installation is always ICSP.

## What this did not establish

- The **exact flash block sizes** of the 6525/6627 were modelled as a 64-byte
  row with 64 holding latches (DS39646C / DS39612C ch. 6); the datasheet PDF
  was not on hand to confirm the write granularity (64 vs 32/8 bytes). The
  verdict does not depend on it — the VM reaches no `EECON` at all — and the
  control stub writes one row to exercise the path.
- The audit covers the stores a program can **steer** (every MOVFF through an
  FSR from inside the interpreter). The interpreter's own direct writes to its
  mechanics (TBLPTR, PCL, FSR setup) are not program-controlled and are not
  audited; they are read above in the dispatch and fetch code.
- Only Violet's instruction set was exercised, not a malformed frame that
  fails to assemble — Violet's assembler (`Vasm.java`) is the only encoder, and
  the firmware range-checks the opcode byte regardless.
