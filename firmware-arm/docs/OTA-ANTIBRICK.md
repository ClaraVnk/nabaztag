# A firmware update that leaves a brick: what can be done about it in 15 KB

Study and first implementation, 2026-11-11. Image measured: `lean-reclaim`,
111,716 bytes of the 126,976 an image may occupy.

**The answer in four lines.** There is no second non-volatile memory on the
board, and two images do not fit in one flash, so an update interrupted by a
power cut cannot be made recoverable on this hardware as it stands: the
window is the whole write, about two seconds, and it stays. What *can* be
closed, and is closed by the two steps this study adds to `lean-reclaim`
(328 bytes), is every other way the write itself produces a brick: a sector
or a word that does not take the first time, the watchdog during the write,
an image that is signed but is not a firmware, an image that overruns the
configuration sector. Everything below is measured on the built image, in
`emulator/flashsim` or in `rabbit-vm`; what rests on an assumption about the
real part says so.

## 0. Is there anywhere else to put an image?

No. Looked for in the sources, the linker script and the pin definitions:

| candidate | what it is | verdict |
|---|---|---|
| internal flash, sectors 0-30 | 31 x 4 KB, the image | the only place code can live |
| internal flash, sector 31 (`0x1F000`) | the configuration page (`conf.bin`: SSID, PMK, server, adoption key, RNG seed) | 4 KB, in use, rewritten at run time |
| `CS_FLASH` (`PO4.1`) | a chip-select named for an SPI flash, declared for PCB `LLC2_3` only, driven high once and never used | absent from `LLC2_4c`, the board `common.h` builds for; no driver, no evidence a part was ever fitted |
| rt2501 dongle EEPROM | MAC address and RF calibration, read over a USB vendor request | a few hundred bytes the radio needs; not ours to write |
| RFID tags (`write_eeprom_rfid`) | 4-byte blocks on a tag held to the nose | not on the board |
| external RAM, 1 MB at `0xD0000000` | VM heap, USB pool, `.extbss` | volatile; survives a soft reset, not a power cut |
| VS1003 audio codec | RAM patches loaded at boot | volatile |

`ExtROM` is declared with length 0. The flash is 128 KB and that is all there is.

## 1. What an update does today, in order

Both ways in -- the runtime's OTA (`bytecode/fwota.mtl`) and the bootloader's
upload page (`httpflash` in `boot.0.0.0.13.mtl`) -- end in the same opcode.

1. **The image arrives in the VM heap** (external RAM, from `0xD0010000`):
   as hex inside a `.sim` (upload page, OTA `.sim` form) or as bytes into one
   buffer (OTA `.bin` form, `FW_MAX_BYTES` = 126,976).
2. **`verifySig`**: Ed25519 over the plain image, the watchdog fed from
   inside the hash and the field arithmetic (`verify_wdt`). On success the
   SHA-512 of the image is kept (`nab_flash_fp`).
3. **`flashFirmware`** (`OPflashFirmware`, `vinterp.c`): the two magic words,
   then `nab_fw_verified` hashes the image again and compares -- only the
   bytes a signature covered can go further. Nothing looked at the length or
   the content until this study (section 3, `flash_precheck`).
4. **`sysFlash`** (`vlog.c`, in flash): interrupts off, then a veneer into
   **`flash_uc`, which runs from internal RAM** (`.ramfunc`, copied there by
   cstartup with `.data`). From here on no instruction is fetched from the
   flash -- `flashsim` checks this on every run.
5. **For each 4 KB of the image, sector 0 first** (`write_uc_flash_sec`):
   - `set_bit(TBGCON,0x80)`, meant to halt the watchdog (see section 2, F);
   - the sector is copied to `buffer_temp` (external RAM) and the new bytes
     laid over it -- only the last, partial sector keeps anything old;
   - `FLACON=0x03`, the six-write erase command, a bounded wait on `FLACON`;
     the sector is read back and must be `0xFF` throughout (`flash_verify`);
   - for each of the 1,024 words not already `0xFF`: `FLACON=0x03`, the
     three-write program command, four data bytes, a bounded wait, the four
     bytes read back (`flash_verify`).
6. **After the last sector**: the watchdog is reprogrammed to overflow in
   256 us, `nab_reset_reason=3`, `while(1)`. The reset is the success path;
   there is no other.
7. **Reset**: vector at 0, cstartup (clock, stacks, `.data` and `.ramfunc`
   copied from the end of the image to RAM, `.bss` cleared), `main()`.
   The radio does not always come back from this reset; "orange after a
   flash, unplug it" is that, not a bad image.

Counted on the built images (`flashsim flash`, `lean-reclaim` over itself
rebuilt): **28 erases and 27,972 word programs, 111,888 bytes**, sector 0 to
sector 27, in that order. Nothing is written twice and nothing is kept: from
the first erase to the last word the flash is neither image.

How long that takes is not known. Oki's comment in `mem.c` says 63 ms for
one sector, erase and write; `flashsim` splits that as 22 ms + 1,024 x 40 us
(parameters), which gives **2.09 s for 28 sectors** including 0.36 s of the
CPU's own copying and checking. Every millisecond figure below is that
assumption multiplied out; the operation counts are exact.

## 2. Every way to end up with a brick

"Recoverable" means by the owner, without opening the rabbit. JTAG (the Pi
station, `jtag/`) recovers all of them.

| | cause | when it is fatal | recoverable before | after this study |
|---|---|---|---|---|
| A | **power cut during the write** | from the first erase to the last word: 28,000 of 28,000 operations, ~2.08 s of ~2.09 | no | **no** -- unchanged, see section 3 |
| B | **a word or a sector that does not take** (worn cell, marginal erase; this project has one such bit on file, `0x1F9`, sector 0) | at once: `flash_uc` *returned the address to its caller*, which is in the flash it had just half-rewritten | no -- the report was the brick | **yes if any of 12 attempts succeeds**; a cell that is dead for good is still a brick |
| C | **erase that never completes** (`FLACON` never says done) | same as B, after a 2^20-read wait | no | as B |
| D | **partial write** by any other route (the writer giving up) | as B | no | the writer no longer gives up |
| E | **the same image flashed again** (owner clicks twice, the warren resends) | the whole 2 s window, for nothing | no | **window is zero**: no sector differs, nothing is erased |
| F | **watchdog during the write** | if the halt in step 5 is not honoured and the write outlasts 2.09 s: mid-flash reset, same as A | no | **closed**: fed before every sector, longest silence 76 ms |
| G | **signed image that is not a firmware** (wrong file signed, truncated build) | first erase | no | refused before the first erase if the length or the reset vector is wrong; a well-formed image that crashes later is H |
| H | **signed image that does not boot** (the 2026-06-20 brick: a data abort in early init) | at the next reset | no | **no**. Only a gate before signing helps: `preflight.py`, the emulator, and now `flashsim boot` |
| I | **image that boots but never joins Wi-Fi or the server** | never fatal by itself | yes, if its config mode works: head button at power-on, upload an older signed `.sim` | same -- see "trial boot" below |
| J | **image too long** (> 126,976 bytes through the upload page, which has no length check) | erases the configuration sector, then issues erase commands past the end of the flash | config: yes (re-enter it); the rest: undefined | refused before the first erase |
| K | **configuration sector lost** (power cut while saving settings) | never | yes: `confInit` finds no magic and starts from defaults; the owner re-enters Wi-Fi, the rabbit is re-adopted | same |

On F: `TBGCON` accepts a write only after `0x5A` has been written to it --
`wdt_start` and `reset_uc` both do that -- and `write_uc_flash_sec` does not,
so its halt is probably ignored and the 2.09 s window probably runs through
the whole write with interrupts off. With Oki's 63 ms the model then resets
a 31-sector image (1.0.0.3, 123,564 bytes) at 2.09 s with four sectors to
go, and that image *was* installed over the air on 2026-11-10. So either the
halt works or a sector takes under ~58 ms; nobody has measured which, and the
margin on an ageing flash is unknown. Feeding the watchdog between sectors
makes the question not matter.

## 3. What fits in 15 KB, measured

### Done in this study (in `lean-reclaim`, +328 bytes, 14,932 left)

**`flash_no_return`** -- 160 bytes of `.ramfunc` (flash and RAM). Once the
first sector is erased the writer finishes or resets trying: a failing
sector is erased and written again up to four times; if one still refuses,
the rest are written anyway and the image is gone over again, three passes;
a sector that already holds its bytes is skipped *before any erase*; the
watchdog is fed before every attempt; `flash_uc` never returns, and the
address of a sector that refused to the end goes out in the next boot URL
(`&r=...`, `crash_report`) if there is a next boot. Closes B, C, D, E, F.

**`flash_precheck`** -- 176 bytes of code, 8 fewer of bytecode. Before
`sysFlash`: length between 16 KB and `0x1F000`; reset vector an
`ldr pc,[pc,#n]` whose target is an ARM address inside the image. A refusal
answers `0xE1` and erases nothing. No version comparison of any kind: an
older signed image must stay flashable, and all six images in `releases/`
pass. The same step repairs the upload page's failure branch, which tested
for an answer (`nil`) the opcode never gives and so could not be reached.
Closes G (in part) and J.

**`emulator/flashsim`** -- no flash cost. The built image's own writer and
reset on an ARM7 model, with a flash that can lose power at any operation.
It is what every number in this document comes from, and `flashsim boot` is
a gate an image can be put through before it is signed (H).

### Evaluated and not done

**Rewrite from the RAM copy while the power holds.** Done, above. It is the
only one of these that costs almost nothing and removes a whole class.

**Order of writing.** No order helps against a power cut. Any state between
the two images is dead whichever sector went first, because nothing that
survives can fetch the rest: 0 of 31 sectors are identical between 1.0.0.2
and 1.0.0.3 (or any two releases), so there is no "unchanged part" to keep
for last. Skipping identical sectors is in; it only pays for case E.

**A boot guard in sector 0, never rewritten.** Prototyped for size: vectors
through a fixed jump table at `0x08001000`, a table-driven CRC-32 of the
image (10 instructions a byte, ~0.15 s at boot on the ring oscillator) and
a rescue that blinks the belly through SPI1 come to **692 bytes** (272 +
420), plus a 12-byte header. Sector 0 then holds nothing else, so the real
cost is **4.1 KB**, or ~0.8 KB if constants that never change
(`rt2501_firmware`, 1,600 bytes; the SHA-512 table, 640) are moved in to
fill it. With "skip identical sectors" it would indeed never be rewritten
by an update between guarded images, and still be rewritten -- as today --
by a return to an older image, so it costs nothing in rollback.
What it buys: after a cut, a rabbit that says "my firmware is damaged"
instead of a dark one, and never runs half an image. What it does not buy:
a way back. Without a network stack the guard can blink; it cannot fetch.
A config mode is the whole stack (next item). The only other channel on
the board is the UART pads (a ~1.5 KB XMODEM loader), which needs the
rabbit opened and an adapter -- less than JTAG, but not "without tools".
**Not built**: it moves the vectors and the entry point, which is the one
change the emulator cannot fully vouch for (clock at reset, the LED driver),
for a light. It becomes worth its sector together with a resumable delta
(below).

**A fallback image, whole or compressed.** Measured on `Nab.bin`:

| | bytes | zlib -9 | LZMA | LZMA + Thumb filter |
|---|---|---|---|---|
| whole image | 111,716 | 70,790 (63%) | 63,096 (56%) | 60,253 (54%) |
| code (`.text`) | 76,192 | 52,627 (69%) | 47,340 (62%) | 44,547 (58%) |
| boot bytecode | 26,415 | 12,395 (47%) | 11,032 (42%) | -- |

Thumb code does not compress below 58%. A compressed copy of the image
needs 60-71 KB plus a decompressor (inflate ~1.5 KB, LZMA ~4 KB); 15 KB are
free. For image + compressed copy + guard to fit, the image must come down
to about **75 KB: 37 KB to regain.**

**A minimal rescue that can only re-download.** From the link map, what a
rabbit needs to fetch and install an image over WPA2:

| | bytes |
|---|---|
| startup, `main`, IRQ, libgcc/newlib pieces | 4,300 |
| USB host (`hcd`, `usbhcore`, `usbctrl`, `hcdmem`) | 6,257 |
| rt2501 driver, with its 1,600-byte firmware | 8,599 |
| 802.11 + EAPOL + SHA-1/MD5/HMAC + AES (WPA2 only; `ieee80211.o` is 12,043 with SAE) | ~15,500 |
| the Metal VM (`vinterp`, `vmem`, `vloader`, `vlog`, `vnet`) -- IP, TCP, DHCP and HTTP are bytecode | 16,088 |
| Ed25519 verify + SHA-512 (`tweetnacl`, `nab_sig`) | 7,230 |
| flash writer, LEDs, SPI | 2,036 |
| bytecode: DHCP, ARP, TCP, DNS, HTTP client, join, flash (estimate: the 26,415-byte bootloader less its pages, config server and mDNS) | ~13,000 |
| **total** | **~73,000** |

About 46 KB compressed. It is the same 37 KB short as the compressed copy,
because the minimum is most of the firmware: the network stack is in
bytecode and the bytecode needs the VM.

**A "trial" of the new image.** The new image proves it reaches the server
or -- what? There is no old image to return to, so a trial has nothing to
roll back *to*. What exists already is the manual version: an image whose
config mode works can be given an older signed `.sim` from a phone. A trial
counter could at most drop into config mode by itself after N boots without
a server, which helps an owner who does not know the head-button gesture and
nobody else; it does nothing for an image whose radio or VM is what broke.
Not built. The useful form of this is before signing: the emulator's
config-upload scenario run against the image about to be released.

**A resumable delta.** The one design that would close A for some updates.
Stage a compressed in-place delta in the free sectors (harmless if cut),
then apply it sector by sector from a guard that keeps a journal and one
spare sector, so that after a cut the guard finishes the job from flash
alone. Measured between real releases (bsdiff / detools):

| from -> to | bsdiff+bz2 | detools in-place, heatshrink | sequential, LZMA |
|---|---|---|---|
| 1.0.0.2 -> 1.0.0.3 (constant-time SAE) | 3,325 | 5,713 | 3,189 |
| 1.0.0.0 -> 1.0.0.2 (two releases) | 13,679 | 17,437 | 12,696 |
| stable -> 1.0.0.0 | 29,035 | -- | -- |

With 12 KB of whole free sectors, less one for the journal and the sector
in flight, about 8 KB of delta: a point release fits, a feature release does
not, and going back needs the reverse delta. It needs the guard (4 KB), a
patcher and decompressor in it (~2-3 KB), a signature over the delta rather
than the image, and the warren serving one delta per pair of versions.
A project, not a step; recorded here with its numbers.

**Pre-checks before the first erase.** Done (`flash_precheck`). What they
cannot do is say an image *works*; that is `flashsim boot` and the
emulator, before the signature.

### What to do now, what needs flash, what is impossible

- **Now** (done here): `flash_no_return`, `flash_precheck`, `flashsim`.
  Put `flashsim boot` and one `flashsim flash` from the previous release in
  the release procedure, next to `preflight.py`.
- **Next, with the 14.9 KB that are left**: the boot guard *with* the
  resumable delta, if point releases are judged worth protecting; the guard
  alone only changes what a brick looks like.
- **With 37 KB regained** (image at ~75 KB): a compressed recovery image
  behind the guard, which is the first design that survives any power cut
  and any image that does not boot.
- **Impossible on this board**: two images side by side (needs the image
  under 61 KB); making a cut during a full rewrite recoverable by any
  ordering or bookkeeping; switching the dongle's power to bring the radio
  back after the reset (the root hub does not implement port power
  switching -- measured over JTAG, 2026-11-07).

## 4. What is proven, and on what

`../emulator/flashsim/flashsim.py`, on the built `Nab.elf` of `lean-reclaim`
before (`base`, 111,716 B) and after (`nr`, 112,044 B) the two steps. The
resident image is booted, then its own `flash_uc` -- the copy cstartup put
in RAM -- writes the other image.

| | before | after |
|---|---|---|
| uninterrupted flash of a different image | 28 erases, 27,972 words, reset, boots | same, the watchdog fed 28 times |
| power cut, different image: fatal operations | 28,000 of 28,000 (2,085 ms of 2,091) | 27,918 of 28,000 (2,082 ms of 2,092) |
| power cut, same image again | 27,918 of 27,918 (2,081 ms) | **0 of 0** |
| one word that fails once (`0x1F8`) | returns `-1` after 127 words; flash is neither image; **brick** | one extra erase, **boots** |
| one sector that does not erase once (`0x5000`) | returns `0x5000` at the sixth sector; **brick** | one extra erase, **boots** |
| three faults at once, up to 5 repeats | **brick** at the first | 38 erases, **boots** |
| a word that never takes (`0x9000`) | **brick**, 19 sectors unwritten | **brick**, 1 sector unwritten, reported |
| longest the watchdog goes unfed, 28 sectors | the whole write, 2,087-2,091 ms of 2,090 | 76 ms |
| same, 31 sectors (1.0.0.3), halt ignored | **reset at 2,090 ms, 4 sectors short** | 76 ms |
| instructions fetched from the flash during the write | 0 | 0 |

Reset was emulated on 517 of the interrupted states (every sector
boundary, 200 spread over the rest, each also cut in mid-operation): every
whole image reaches `main()` with `.data` in place, every other state does
not boot. Going back to an older signed image: `nr` writes
`naboot-stable` (107,904 B) and `naboot-1.0.0.3` (123,564 B) and both
boot; the window is the same two seconds as going forward.

The rest of the path, in `rabbit-vm` (`../emulator/scenarios/vm/config-upload.txt`, the
bootloader lifted from the new image, `--flash-out` handing the bytes that
got through to `flashsim`):

| uploaded through config mode | signature | `flashFirmware` | then, in `flashsim` |
|---|---|---|---|
| each of the six images in `releases/`, as signed (107,904 to 123,564 B) | OK | reached, bytes identical to the release | written, boots |
| this image, signed with a throwaway key | OK | reached | written, boots |
| 60,000 random bytes, same key | OK | **refused**, red, error page | -- |
| the image padded to 127,488 B | OK | **refused** | -- |
| its first 8,192 B | OK | **refused** | -- |
| reset vector replaced by `b .` | OK | **refused** | -- |
| reset vector pointing past the end | OK | **refused** | -- |

The throwaway key never left the test machine and the VM was built with its
public half; nothing was signed with the project key. The control, on the
image before the two steps: the random bytes reach `flash_uc`, the resident
writer burns them and the reset finds no instruction at 0; the padded image
reaches it too and its 32nd erase is the configuration sector.

`make check` (35 scenarios) passes before and after. `../emulator/tools/vm-check.sh`
with no warren behind it gives the same 21 passes before and after; its two
failures are the same two (`config-upload` run without an upload, which is
what the table above is, and `server-vanish`, which needs a Terrier).

Not run again here: the runtime's OTA (`fwota.mtl`). It ends in the same
opcode and the same writer; its bytecode is not touched by this work.

**What the model is.** The instructions are the image's own, on Unicorn's
ARM core. The flash is the protocol `mem.c` speaks: `FLACON` bits 0 and 1,
the erase and program command sequences, erase to `0xFF`, programming as
AND. Reads go through the alias at 0, writes to `0x08000000`, as on the
part.

**What it is not, and what would settle each point on a rabbit:**

1. *Timing.* 22 ms and 40 us are a split of Oki's 63 ms. Measure: a build
   that stores the free-running timer at the first erase and before the
   reset into `.extbss`, read back in the boot URL.
2. *The watchdog halt.* Whether `set_bit(TBGCON,0x80)` without the key
   stops it. Measure: read `TBGCON` over JTAG after that line, or a test
   build that waits three seconds between that line and the first erase --
   a reset with nothing written means the halt is ignored. After
   `flash_no_return` nothing depends on the answer.
3. *A flash left in mid-operation.* Modelled as each bit moved or not, at
   random. No verdict depends on it: such a sector is counted as neither
   image's. What a half-erased sector really reads, and whether a later
   erase always recovers it, is the part's business; the retry assumes it
   does, as the erase-verify of `flash_verify` already did.
4. *Re-issuing a command after a failed one.* The retry writes
   `FLACON=0x03` and starts again, which is what the stock writer does
   between any two words. If the controller needed more than that to leave
   an error state (bits 2 and 3 of `FLACON`), the retry would fail the same
   way four times and the outcome would be what it was before: no worse.
5. *Feeding the watchdog with interrupts off, from RAM.* The same two
   writes to `WDTCON` the interpreter makes before every opcode.

Nothing in the two steps changes the order of writing, the vectors or the
start-up code. What would -- the boot guard -- is not built.
