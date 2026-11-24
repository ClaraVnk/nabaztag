# A native firmware for the Nabaztag v1 (2005) -- design, phases 1 and 2

Violet's v1 firmware is a PIC18 interpreter for Violet's VM. It cannot be
updated over the network (`../v1-icsp/VM-ESCAPE.md`), and on Clara's rabbit
(Lazare: 1.4b, PIC18LF6525, 48 KB of flash, AT45DB081B serial flash, Prism
2.5 card with station firmware 1.4.2) it has 94 bytes free
(`../v1-icsp/IMPROVEMENTS.md`). This is its replacement, written in C and
flashed over ICSP with a PICkit 3 (done four times on 2026-11-24, the
original restored each time). Phase 1 is the skeleton end to end, phase 2
the loader that makes the firmware updatable over the network (section 7)
and the first size pass (section 2); the second size pass (2026-11-25,
section 2) put the hash in assembly and the loader under 8 KB, the
application now starts at 0x2000. All proven in the emulator only: no
rabbit, no PICkit, no write to any PIC.

What runs (2026-11-24, `../emulator/tools/v1-native-proof.sh`, the numbers
are the emulator's, with the PIC's cycle count as its clock):

| Step | Measured |
|---|---|
| Image | phase 1: 22 920 bytes of 49 152 (46 %), RAM 1 015 bytes. Phase 2: loader 12 014 B (0x0000-0x2FFF), application 27 670 B from 0x3000, RAM 1 470 / 1 793 B. After the second size pass (2026-11-25, section 2): loader 8 144 B (0x0000-0x1FFF), application 27 278 B from 0x2000, RAM 1 408 / 1 794 B |
| Boot to the card associated | 579 ms (200 ms of it the two waits copied from Violet's pin sequence, below) |
| DHCP lease | 614 ms |
| DNS answer | 1 102 ms (the hop's ARP first, then the query) |
| First poll sent | 1 592 ms; answered and parsed (status line, frame 03), the connection closed by both sides at 1 613 ms |
| Polls | every 10 s, 3 in a 40 s run, each a connection opened, read, closed |
| Ears | each moved 2 holes after the first answer, one motor at a time, the emulator's sensors counted |
| Watchdog (512 ms, on) | never fired |
| Serial flash | AT45DB081B recognised by its density bits, page 0 written and read back, the boot counter at 2 on the second run |
| Loader (phase 2) | slot A seeded in 10.1 s, an update downloaded in 1.3 s, verified in 9.9 s, 3 472 blocks written and read back in 0.8 s, the new image polling 14 s after its download; twelve power cuts, a bad key, a corrupt row, a corrupt slot and a crashing image all end on a polling rabbit (section 7.5). With the hash in assembly (2026-11-25): seeded in 5.7 s, verified in 5.4 s |
| SHA-256 on the PIC (2026-11-25) | FIPS 180-4's "abc", the 448-bit message, the million 'a' and the empty message, RFC 4231's HMAC cases 1-5: all equal to Python's on the emulated PIC; 12 034 µs per 64-byte block (`../emulator/tools/v1-sha-proof.sh`) |
| Audio (phase 2) | the OKI start sequence, the 29 toggles and the 12 000 data bytes identical to Violet's for the same tone; the bursts split where the file sits in the serial flash (ours 226, Violet's 188) (`../emulator/tools/v1-audio-proof.sh`) |

## 1. Prior art, read first

- **Newbaztag** (Niels Moseley, MIT; `../vendor/newbaztag`, and
  github.com/trcwm/Newbaztag `firmware/new/src`): a C firmware for the
  PIC18F6627 built with XC8, a serial command protocol (LEDs, motors,
  timer, homing) and no network -- his card was dead and he meant an ESP
  module. What it gave: the pin list (`../vendor/newbaztag/doc/components.md`), the TLC5922 data
  order (first word shifted = top LED's red), the MSSP setting (CKE 1, CKP 0),
  the timers as hole counters (T0CON 0xA8, T3CON 0x87), the OKI register
  table and its 6 MHz clock from CCP1, ADCON1 0x0E / CMCON 7 copied from the
  original, SPBRG 38 at 9600. Nothing was copied: it is XC8-specific and
  interrupt-driven, and its motor pins (RD1) contradict Violet's code.
- **Violet's own 1.4b and 2.0b**, through the emulator that was written from
  them (`../emulator/v1/*.c`) and `../v1-icsp/IMPROVEMENTS.md`: the card's
  bus protocol (8-bit accesses, the odd byte acts), the RIDs Violet writes
  (FC00, FC02, FC03, FC06, FC07, FC2A, FC84, FCB0, FCB3, FCB4), the TX
  descriptor (data length at 44, 802.3 header at 46, LLC/SNAP payload at 60),
  the motor pins (RF0-RF3), the poll's exact shape, the OKI sequence
  (section 3 of IMPROVEMENTS.md), the AT45 opcodes, and the two lessons the
  network study paid for: resolve the server again after failures, show
  the owner where it stopped.
- **hostap** (Linux, GPL; `hostap_hw.c`, `hfa384x.h`): the Prism 2
  programmer's model -- register offsets, command codes, BAP handshake, the
  RID and descriptor layouts, `TX_RECLAIM`. Read for the facts, nothing
  copied (the licence would not fit this repository's MIT anyway).
- **uIP** (BSD): read for its shape; not used. One connection, no copies
  is what we want, but uIP's generic parts (its many `#ifdef`s, 32-bit
  checksum arithmetic, its own timers) cost more on a PIC18 than a stack
  written for exactly one job. Microchip's TCP/IP stack was not considered:
  its licence binds it to Microchip hardware and the emulator is not that.

## 2. Toolchain: SDCC 4.5.0, pic16 port, gputils 1.5.2

`Containerfile`, Alpine 3.21, both built from pinned sources (sha256 in the
file): no distribution packages them (Alpine 3.21 and edge, Rocky 9 and 10
with EPEL were checked). Build: `podman build -t v1native-toolchain -f
Containerfile .`, then `podman run --rm -v "$PWD":/src:Z -w /src
v1native-toolchain make`; one command, reproducible, `out/18f6525/full.hex`
and the sizes. The one assembly file, `src/loader/sha256.asm`, goes through
gputils' `gpasm -c` and links with SDCC's objects.

Why not XC8: Microchip's free mode runs with most of the optimiser off
("Free" is a licence tier; the output is routinely 30-50 % larger than PRO
on PIC18), and the compiler cannot be redistributed, so a Containerfile
could only fetch it from Microchip's site behind a licence agreement,
which is not reproducible. SDCC is GPL, pins by version, and its
`device/non-free` tree carries Microchip's register headers for the
PIC18LF6525 (usable, not GPL; `--use-non-free`). Its pic16 code is not
small -- banked RAM access and the software stack cost -- but measured, not
guessed: the table above. `--obanksel=2` and the peephole optimiser are the
options that matter; `--stack-auto` was not used (no recursion, no
interrupt; static locals are smaller on this port). Measured on this code:
22 920 bytes with the peephole optimiser, 24 284 without it;
`--fommit-frame-pointer` changes nothing. What is big: every function that
touches a 32-bit value (TCP's sequence numbers: `tcp_rx` 1.2 KB, `net_poll`
1.3 KB), and the port's banked access to RAM.

**The size pass of phase 2** (2026-11-24), measured on the linker maps:

| Lever | Effect |
|---|---|
| `log_dec` by repeated subtraction instead of `/ 10` and `% 10` | the 16-bit divide and modulo library routines (330 B) are no longer linked; `log_dec` itself 214 B, in the application only (the loader logs in hex) |
| the serial flash address without a 32-bit shift (`flash_addr`) | the 32-bit shift routine gone; 186 B for the function |
| the CONFIG words and the loader's own code out of the application | the application carries no boot code; one `goto __startup` at 0x3000 (`src/app/entry.asm`) |
| shorter log strings | about 150 B |
| the port's 32-bit code, learnt on SHA-256 | one 32-bit operation per statement on static words: an add is 8 instructions; a compound expression keeps its temporaries in RAM (the round written as one expression: 2.7 KB; four-byte helpers with pointer arguments: 4.3 KB for the file; the final form: `compress` 2.4 KB, the file 3.9 KB). Indexing a 32-bit array by a byte calls a 314-byte multiply: an 8-bit offset on a byte pointer does not |

The application is 27 670 B in phase 2 against 22 920 in phase 1; of the
difference, `update.c` is 1.3 KB, the shared state record 0.9 KB, the slot
stream 0.5 KB, the OKI 1.3 KB, the frame 10 and the image request in
`net.c` 0.4 KB, the application's copy of the UART and flash drivers the
rest; the levers above gave back about 0.9 KB. Not done: TCP's
sequence numbers as 16-bit deltas (`tcp_rx` 1.3 KB, `net_poll` 1.4 KB,
`tcp_send` 0.6 KB today; perhaps 0.5 KB to gain), the DHCP option walk as a
table, and the application calling the loader's UART, SPI and flash
routines through a jump table instead of carrying its own copy (about
1.5 KB; it binds the two images' ABIs together, so later).

**The loader was 12 014 B** after phase 2, not the 4 KB the phase-1 plan
reserved: with SDCC's pic16 output, HMAC-SHA-256 was 3.9 KB (4.4 with its
tables), the serial flash and slot driver 1.3 KB, the state record 0.9 KB,
the UART log 0.5 KB, the loader's own logic 2.6 KB, the C start-up and the
constants the rest.

**The size pass of 2026-11-25** brought it to **8 144 B** (8 106 on the
6627), measured on the linker maps at each step, SDCC's options unchanged
(`--opt-code-size`, `--obanksel=3`, `--fommit-frame-pointer` change
nothing; `--extended` would save 644 B but the 6525 has no extended
instruction set; `--pno-banksel` 156 B but the data is banked):

| Lever | Loader | Note |
|---|---|---|
| SHA-256 and the HMAC in PIC18 assembly (`src/loader/sha256.asm`) | 12 014 → 9 010 | 1 106 B of code and 288 B of tables instead of 4.4 KB: all the working words in the access bank (no BANKSEL in the round), little-endian words so that one routine adds any word to any word, the block buffer *is* the 16-word schedule ring (bytes stored at `blen ^ 3`), rotations as byte moves then carry chains the shorter way round, the HMAC with SDCC's own calling convention (arguments at FSR2 + 2) so that `sha256.h` did not change. The C tools/sha256-host.c went with the C hash: Python's `hashlib` is the reference now, on the emulated PIC (7.5) |
| SDCC's `crt0` instead of `crt0iz` for the loader | − 150 | no initialised data; no static of the loader relies on a zero before it is written |
| 64-byte rows instead of 32-bit byte counts and addresses in the loader (`hdr_rows`, `pic_seek(row)`, the slot loops) | − 354 | the 6627's 0x2000..0x17FFF is 1 408 rows; the length check is three bytes of the header |
| the state record on one array at constant addresses, one pass for sum and magic (`src/common/state.c`) | − 124 | SDCC computes a fresh pointer for every `p[i]`; the application gains the same |
| `slot_write_page` a macro over `flash_write` | − 168 | the wrapper's parameter shuffling cost more than the add at the two call sites |
| `log_hex16` dropped (two `log_hex8`), `block_write` with `*p++`, the header filled in one loop | − 70 | |
| speed: the bit steps of a rotation inlined, the word moves of the round direct | + 14 | 86 000 → 72 000 cycles per block, 14.3 → 12.0 ms |

What is left in the 8 KB: the loader's own logic 2.7 KB (of which `main` 860 B and the seed 580 B), the hash 1.4 KB, the serial flash driver 1.3 KB, the state record 1.1 KB, the UART log and its strings 0.7 KB. The 6525's hardware write protection covers 0x0000-0x07FF in any case (section 7.2), and what the loader protects itself with is that it never writes below APP_BASE. The margin is 48 B on the 6525: a change to the loader must be paid for in the same file.

The PIC's own linker script is `18f6525-native.lkr`: gputils' with banks
2-5 merged, because gplink places an array inside one 256-byte bank and the
frame buffer is 340 bytes.

## 3. Architecture

One loop, no interrupt (Violet's firmware had none; the emulator's PIC core
has none either, so what runs there runs here):

```
main: clrwdt; timer_poll; prism_poll; net_poll; ears_poll; LEDs by state
```

| File | What | Notes |
|---|---|---|
| `src/board.h` | pins, constants, every module's interface | one header, so the pin map is in one place |
| `src/hw.c` | CONFIG bytes, TMR1 tick (10.92 ms), UART1 log, MSSP, TLC5922, AT45DB | CONFIG is Lazare's 14 bytes verbatim |
| `src/prism.c` | the card: bus, commands, BAPs, RIDs, init, join, events, TX, a byte-wise RX reader | |
| `src/net.c` | ARP, IPv4, ICMP echo, UDP, DHCP client, DNS resolver, one TCP client, the HTTP poll and the answer's frame parser | |
| `src/ears.c` | motors and hole counters, non-blocking, a stall timeout | |
| `src/main.c` | boot order, the configuration page, the LED diagnostic, the ear demonstration | |

Design choices:

- **Nothing is copied on the way in.** A received frame is read word by
  word from the card's buffer through BAP1 (`rx_byte()`); the parsers
  consume it in order and `rx_seek()` jumps. A 1400-byte TCP segment costs
  no RAM, which is what lets the answer to a poll be any size (Violet's
  wait program is 33 KB).
- **One frame buffer on the way out**, 340 bytes, from the 802.3 header;
  the DHCP discovery is the largest frame. Checksums are one's-complement
  sums with the carry folded as it comes, in 16 bits.
- **Config bits are Lazare's**: brown-out reset on (2.7 V), watchdog on at
  ~512 ms, power-up timer on, no code protection. The main loop clears the
  watchdog; every wait inside a driver (the card's events, the flash's
  busy bit, `delay_ms`) clears it too and has a bound. Brown-out stays on
  because it is what cuts a stalled motor on Lazare (RUNBOOK, 2026-11-24).
- **The LED diagnostic Violet never had** (IMPROVEMENTS.md #4): bottom red
  at boot, blue when the card answered, green when associated; middle green
  with a lease, middle-left green with the server's address, top green
  when a poll was answered. A rabbit that stops says where.
- **The server's name is resolved again** after three failed polls
  (IMPROVEMENTS.md #3), and DHCP is redone if DNS never answers.
- **Version string `v=30`** in the poll, so the Terrier tells this firmware
  from Violet's (12, 14, 20): `nabv1.py` stores `v=` as `v1_version`, as an
  integer compare (`fw_at_least`).
- **One motor at a time**, always (two at once restarted Lazare); a hole
  that does not come within 3 s stops the motor and logs "stalled".

## 4. Memory map

### 4.1 PIC flash, 48 KB (0x0000-0xBFFF)

| Range | Size | Phase 2 | Later |
|---|---|---|---|
| 0x0000-0x1FFF | 8 KB | **the loader** (section 7): 8 144 B used (12 014 and 12 KB until 2026-11-25), with the CONFIG words; the reset vector, the AT45 and slot driver, HMAC-SHA-256 in assembly, the 8-byte block write, the state record, a UART log in hex. WRTB can protect 0x0000-0x07FF on the 6525 (left off until the loader has run on the rabbit) | 48 B free: nothing |
| 0x2000-0xBFFF | 40 KB | the application, 27 278 B, linked from 0x2000 (`--ivt-loc`, `entry.asm`): phase 1 plus the update path, the state record, the OKI | WPA2 (~6 KB: CCMP + 4-way handshake, PMK computed off-rabbit), the Prism flashing path (~2 KB), a program layer of our own (open) |

A Violet-VM compatibility layer (~20 KB) does not fit and is not planned.
Left for the application today: 13.7 KB (40 960 − 27 278), against 8.7 KB
before the loader shrank and the base moved: WPA2 and the Prism flashing
path fit without the other size levers (the 16-bit TCP, the jump table);
on the 2.0b board's 96 KB none of this is tight.

### 4.2 PIC RAM, 3 968 bytes

| Use | Bytes | Phase |
|---|---|---|
| Application, measured (udata + the 256-byte stack, `size.py` from the map) | 1 794 | 2 |
| of which the frame buffer | 340 | 1 |
| of which the update's page buffer (528: a 161 page) and header | 592 | 2 |
| SDCC's stack (its default; nothing recurses, 128 would do) | 256 | 1 |
| Loader, measured (its own RAM; the application starts over it): a 528-byte page, the hash's 59 bytes in the access bank and 160 banked (state, block, key pad) | 1 408 | 2 |
| WPA2: PTK, the replay counter, AES round keys (the WPA2 study on the branch emu/v1-wpa-cost) | ~450 | 3 |
| Left for the application | ~1 700 | |

### 4.3 Serial flash (AT45DB081B on Lazare, AT45DB161 on 2.0b boards)

The PIC executes only its own flash (Harvard), so the serial flash is
storage. Both chips have 4 096 pages; the 081B's are 264 bytes
(1 081 344 B, 9 address bits per page), the 161's 528 (2 162 688 B, 10
bits). **The layout is by page number**, identical on both: the code
addresses pages and reads the density bits once (`flash_init()`); every
region simply holds twice as much on the 161. Writes go through buffer 1
and the "write with erase" opcode (0x83), so a region is a set of pages
and nothing needs a sector erase.

| Pages | 081B | 161 | Region | Phase |
|---|---|---|---|---|
| 0-1 | 528 B | 1 056 B | **Configuration**, two copies (A/B with a sequence number, the newer wins; a write cut by the brown-out loses one copy, not the configuration): magic `NAB1`, format, boot counter today; then server name, language, SSID, WEP key or **WPA2 PMK (32 B)**, the flags (opt-ins). The loader's per-rabbit HMAC key is *not* here (the serial flash is readable by anyone with the board): it is in the PIC's data EEPROM (section 7) | **1** (page 0 only) |
| 2-3 | 528 B | 1 056 B | **The loader's state record**, two copies (section 7.1): active slot, boot counter, confirmed flag, the version to skip | **2** |
| 4-15 | 3.2 KB | 6.3 KB | **Ring log**: 32-byte records (tick, event, 24 bytes of detail), 99 / 198 records, read back over the serial port or sent with a poll | 3 |
| 16-207 | 50.7 KB | 101.4 KB | **Image slot A**: a header page (`struct app_hdr`, the HMAC at 16, pending at 12, complete at 48, written last) and the image packed on the following pages; 36 KB of PIC18LF6525 application fits in 140 pages of 264 B, a PIC18F6627's 84 KB in 163 pages of 528 B: the same slot serves both boards. Seeded from the PIC at the first boot | **2** |
| 208-399 | 50.7 KB | 101.4 KB | **Image slot B**: the application downloads into whichever slot is not active; the loader installs a pending slot once its HMAC checks and keeps the other as the fallback | **2** |
| 400-911 | 135 KB | 270 KB | **Prism station firmware** (sf010704: ~171 KB as S-records, under 100 KB as the binary the card's download command takes: primary + secondary images with their PDR fix-ups, per `prism2_srec`) for flashing the card from the rabbit | 3 |
| 912-975 | 16.9 KB | 33.8 KB | **Program layer**: a small interpreted layer of our own, non-critical behaviour fetched from the Terrier (animations, ear choreographies, LED scripts); or Violet's frame 05 if compatibility is ever wanted (33 KB fits on the 161 only) | 3 (open) |
| 976-4095 | 823 KB | 1 647 KB | **Sounds**: a directory page at 976 (`struct snd_dir`: `SND1`, count, autoplay, then first page and data length per sound), the Audi files from 977 with their 16-byte header at byte 0 of a page; OKI ADPCM at 16 kHz is 8 KB/s, so 103 s on the 081B and 206 s on the 161 | **2** (played; written by the proof, not yet by the application) |

Phase 1 needs only page 0; phase 2 writes the state record and the slots,
and plays from the sound region. The ring log is reserved, not written.

## 5. The network path, as the card sees it

The card runs 802.11 (and WEP, phase 2) itself; the host hands it 802.3
frames with an LLC/SNAP (RFC 1042) payload and gets the same back: this is
what Violet's firmware did, what the emulator models from it, and what
hostap does for a Prism in its default (802.3) mode. The TX descriptor is
60 bytes: control word at 12 (`0x0006`: 802.3 frame, TX events), data
length at 44, the 802.3 header at 46, payload at 60. One TX buffer of
1 600 bytes allocated at boot and reclaimed after each frame.

Joining: RIDs FC00 (BSS), FC02 (SSID), FC07 (2 304), FC2A (open), FC28
(WEP off), FCB0 (rates), then ENABLE; the link is the info frame F200 with
status 1. WEP (phase 2, cheap): FC23 default key, FC24 the key, FC28 flags,
FC2A shared or open; the card does RC4.

The HTTP poll is Violet's byte for byte but for `v=30` and a `Host:`
header (the Terrier is behind a reverse proxy; Violet's servers had one
name). The answer's status line is logged, the headers skipped, the body
parsed as Violet's frames (`7F`, then type + 3-byte length each, `FF`) and
listed; frame 03's value (the poll interval) is logged, not yet honoured:
10 s is phase 1's interval.

## 6. What phase 2 did, what is left

Done in phase 2 (2026-11-24, `../emulator/tools/v1-loader-proof.sh`):

- **The loader** (section 7): a permanent boot block at 0x0000-0x1FFF
  (0x2FFF until 2026-11-25), two
  image slots in the serial flash, HMAC-SHA-256 under a per-rabbit key,
  the copy row by row with the PIC's self-write sequence, read-back, the
  fallback to the other slot, the boot counter for an image that never
  confirms itself. The application is linked from 0x2000.
- **The hash in assembly** (2026-11-25, `src/loader/sha256.asm`, section
  2): the loader 12 014 → 8 144 B, the seed and the check of an image
  5.7 s instead of 10; FIPS 180-4 and RFC 4231 vectors equal to Python's
  on the emulated PIC (`../emulator/tools/v1-sha-proof.sh`).
- **The update path in the application** (`src/app/update.c`): our frame
  10 in the poll's answer, `GET /vl/v1/fw`, the image streamed into the
  other slot a page at a time, the header page last, then `reset`.
- **The OKI path** (`src/app/oki.c`, `../emulator/tools/v1-audio-proof.sh`):
  Violet's boot table (22 register writes, measured on its bus log), its
  start sequence (45, 35 rate, 29, 33), bursts of up to 64 bytes into
  register 01 with 29 toggled around each, 33/29 to 0 at the end; an Audi
  file played from the serial flash's sound region behind a directory
  page. The data bytes reach the chip exactly as Violet's do (compared
  byte for byte, 12 000 of them); `OKI_SWAP_NIBBLES` swaps them for the
  test on the real rabbit.
- **Size** (section 2): the log's decimal routine without the division
  library, the flash address without 32-bit shifts, the configuration bits
  out of the application; the loader measured.
- The emulator learnt what the proofs needed (`../emulator/v1`): the
  6525's 8-byte write blocks and 48 KB (`--pic 6525`), the write aimed at
  TBLPTR as the datasheet says, a RESET instruction that resets, the data
  EEPROM read path and its content from the hex, the PIC's flash kept
  across runs (`--pic-flash`) so that a run cut short is a power cut.

Left, in the order the Terrier's users would feel it:

1. **Audio, the rest**: a sound fetched from the Terrier (frame 13,
   section 8) into the sound region, as the image is; the volume
   registers (0x63, 0x39) from a setting; the nibble order and the ADPCM
   table on the real rabbit (IMPROVEMENTS.md #1): two builds, one with
   `OKI_SWAP_NIBBLES`, the 440/880 Hz tone of `v1-audi.py`.
2. **WPA2** after the card is reflashed to station 1.7.4 (`../v1-icsp/
   prism-fw.sh`): host-side CCMP and the 4-way handshake, PMK from the
   Terrier's page (computed off-rabbit), ~6 KB and 450 B of RAM per the
   WPA2 study; the card must do the station firmware's part.
3. **Configuration page and a serial console**: server, SSID, key, language
   written over J3 (and later from the Terrier), the two-copy page.
4. **The rest of the protocol** (section 8): ears, LEDs, sounds by frames
   of our own; the poll interval from frame 03; the ring log; the button
   (a click, a double click, a long press in `sd=`, `nabv1.py parse_sd`).

## 7. The loader

Naboot's question on the tag (`../docs/OTA-ANTIBRICK.md`) was "where can a
second image live?": nowhere, so the window of a power cut stays open
there. The v1 has a 1 MB serial flash next to a 48 KB PIC: the second image
lives in it, and the PIC's own flash is only ever a *copy* of a slot whose
HMAC was checked. Everything the loader does follows from that.

### 7.1 Where things are

| | |
|---|---|
| Loader | PIC flash 0x0000-0x1FFF, linked at 0 with the CONFIG words (`loader-18f6525.lkr`, `src/loader/config.c`); the application is linked from 0x2000 (`app-18f6525.lkr`, `--ivt-loc=0x2000`; 0x3000 until 2026-11-25). `make` gives both, `full.hex` for the PICkit and the emulator, `app-N.img` for the Terrier (`mkimage.py`) |
| Key | 32 bytes in the PIC's data EEPROM at 0x000, programmed with the firmware over ICSP (`mkimage.py full` puts it at 0xF00000 in the hex). All 0xFF = no key: the application runs, no image is ever installed. The Terrier keeps the same key per rabbit and signs with it; the application never reads it |
| Slot A, slot B | serial flash pages 16-207 and 208-399 (section 4.3): page 0 of a slot is its header, the image follows packed on 264-byte pages (528 on the 161). 44 KB of PIC18LF6525 image fits in 171 pages |
| Slot header (64 B) | `struct app_hdr` (board.h): `NBV1`, format 1, board (0x25 = 6525, 0x27 = 6627), version, length (a multiple of 64), *pending* (1: install me) at 12; the HMAC at 16; `complete` = 0xA5 at 48, written last |
| Application header | the same first 16 bytes at 0x2040 in the PIC, so that the loader can seed a slot from the PIC and `mkimage.py` can patch the length after the link |
| State record (16 B) | pages 2 and 3, `struct lstate`: active slot, tries, flags (confirmed), skip_ver, a sequence number and a checksum: two copies, the newer valid one wins, a write goes to the other page. A write cut by the power loses one copy, never the record |
| HMAC | HMAC-SHA-256(key, header[0..11] ‖ image). SHA-256 and the HMAC in PIC18 assembly (`src/loader/sha256.asm`, section 2; `sha256.h` is the C API, with SDCC's calling convention): the block buffer is the 16-word schedule ring, the working words in the access bank, the watchdog cleared per block; checked against FIPS 180-4 and RFC 4231 on the emulated PIC (`../emulator/tools/v1-sha-proof.sh`) |

### 7.2 At power-on

1. UART, SPI, the serial flash's density; the key from the EEPROM; the state.
2. **A pending slot**: for the slot that is not active, if its header says
   pending and complete, and its version is not `skip_ver`: check the
   header (magic, format, board, length within 64..44 KB and a multiple of
   64), then the HMAC over the whole slot. Good: copy it into the PIC
   (7.3), read the PIC back against the slot, record it as active with
   `tries` 0 and the confirmed flag off. Bad: record its version in
   `skip_ver` so that the HMAC is not recomputed at every boot. One state
   write either way.
3. **The PIC must hold the active slot**: if there is no active slot yet
   (first boot after ICSP) and there is a key and an application, the
   application in the PIC is written to slot A with the HMAC the key gives
   it ("seeded"), so that a fallback exists from the first update on.
   Otherwise the PIC is compared byte for byte with the active slot; a
   difference (a power cut during a copy, a corrupted row) means the copy
   is done again, and if that slot cannot be installed (its header or HMAC
   broken too), the other slot is installed instead.
4. **The boot counter**: an image that has not confirmed itself gets
   `LS_MAX_TRIES` (5) boots; the sixth falls back to the other slot and
   records the version in `skip_ver`. The application confirms by writing
   the state with the flag once the Terrier has answered a poll.
5. `STKPTR = 0`, `goto 0x2000`. No application (0xFFFF at 0x2000): a log
   line and a loop that feeds the watchdog.

The loader writes PIC rows from 0x2000 up only, by construction; WRTB in
CONFIG6H can protect 0x0000-0x07FF on the 6525 against the application on
top (left off until the loader has run on the rabbit, as the firmware must
stay readable and replaceable over ICSP; the 6627's BBSIZ can cover the
whole 4 KB).

### 7.3 The copy

Per 64-byte row from the slot's byte stream: erase the row (TBLPTR at the
row, EECON1 = EEPGD|FREE|WREN, 55h/AAh on EECON2, WR), then each write block
(8 bytes on the 6525, DS39612 section 6.5; 64 on the 6627, DS39646): TBLPTR
at block-1, `TBLWT+*` for each byte so that the pointer stays inside the
block, EECON1 = EEPGD|WREN, 55h/AAh, WR; the watchdog cleared after every
block, the CPU stalled by the part during the write (about 2 ms per block
on the real chip: a 23 KB image is some 6 s of writes on the 6525, under a
second on the 6627; the emulator's writes are instant). The slot's bytes
come 64 at a time across page boundaries (`slot_seek`/`slot_next`).

### 7.4 The application's side (`src/app/update.c`)

The poll's answer may carry our frame 10 (section 8). A version above ours
and not `skip_ver` is fetched as soon as the poll's connection is closed:
`GET /vl/v1/fw?sn=<MAC>&v=<ours>&b=<board>`, same TCP client, the body sent
to `update_byte()` instead of the frame parser. The first 64 bytes are the
header (checked: magic, format, board, length); the target slot's header
page is blanked, then the image bytes fill a page buffer written to the
slot page by page; when `length` bytes are in and the server has closed,
the header is written with *pending* = 1 and the complete mark, and the
application executes `reset`. A download cut anywhere leaves a slot without
a complete header, which the loader ignores, and the next poll offers the
version again. The target is the slot the loader is not running from, so
the running image is never overwritten by a download.

### 7.5 What the emulator proved (`../emulator/tools/v1-loader-proof.sh`)

Lazare's board in the emulator (`--pic 6525 --flash-chip 081`), the PIC's
cycle count as the clock, the application 27 712 B (v30 to v33 differ by
their version only, v33 by a loop that never feeds the watchdog). The
times are phase 2's; the run of 2026-11-25 with the hash in assembly and
the application at 0x2000 (27 328 B, 427 rows) follows the table:

| Run | What happened | Measured |
|---|---|---|
| 1. first boot | no state, a key: slot A seeded from the PIC, then the application polls | HMAC + 105 page writes in 10.0 s; slot A's HMAC equals Python's `hmac` over the same 12 + 27 712 bytes (the C SHA-256 checked against the standard, tools/sha256-host.c); no PIC flash write |
| 2. a good update | frame 10 offers v31 at 2.0 s; downloaded into B; reset; the loader checks, copies, reads back, runs v31; v31 polls and confirms | download 27 712 B in 1.39 s; HMAC check 9.9 s; 433 rows erased, 3 464 blocks of 8 written and read back in 0.81 s; v31's banner 11.1 s after the reset, its first poll answered 1.6 s later, the state confirmed |
| 3. a bad signature | v32 signed with another key: downloaded, pending; the loader refuses it | "bad hmac" after 9.9 s, `skip_ver` = 32, v31 runs on, no PIC flash write; the application does not fetch v32 again although every poll offers it |
| 4. twelve power cuts | the run cut at random points, a third each during the download, the HMAC check, the copy and the state write; then a restart | every restart ends on v31 polling: a cut before the header page is written leaves no pending slot and the next poll offers v31 again; a cut in the HMAC check or the copy finds the slot pending and installs it again (a half-written PIC is simply overwritten); a cut after the state write finds the PIC equal to B |
| 5. a corrupt row | a byte of the application flipped in the PIC's flash | "PIC differs from B", B copied again, v31 polls |
| 5. a corrupt slot | the same, and a byte of slot B's image flipped | B fails its HMAC; "fall back to A", A copied, v30 polls |
| 6. a broken image | v33 never clears the watchdog | installed, then a watchdog reset every 512 ms; the loader counts five boots, "never confirmed", `skip_ver` = 33, falls back to A, v30 polls |

Nothing was written to the PIC's flash in runs 1 and 3 (the emulator counts
every erase and block write). The seed and each HMAC check cost about 10 s
of the PIC's time for 27 KB (SDCC's 32-bit code: 150 000 cycles per
64-byte block; a 44 KB image would take 16 s), the copy under a second
here and an estimated 7 s on the part (DS39612's 2 ms per block).

**2026-11-25, the hash in assembly, the same six runs, PASS**: slot A seeded
in 5.7 s (HMAC over 27 328 B, equal to Python's); the HMAC check of the
update 5.4 s, 427 rows erased and 3 416 blocks written and read back
in 0.74 s, v31's banner 6.5 s after the reset; "bad hmac" after
5.4 s; the twelve cuts, the corrupt row, the corrupt slot and v33 as
before. The block costs 72 000 cycles (12.0 ms, 12 034 µs per block
measured over the million 'a' of `v1-sha-proof.sh`), 2 times faster
than the C, not the 10 an unrolled round would give: the rotations are
still bit by bit through the carry (28 bit steps and 9 byte moves per
round) and the eight round variables are moved down a word at the end of
each round; what remains was left in favour of the 48 B of margin
(section 2). A 44 KB image would take about 8.5 s.

**The vectors on the PIC** (`../emulator/tools/v1-sha-proof.sh`, the test
image `src/loader/shatest.c` linked like the loader, the watchdog on):
SHA-256 of "", "abc", the 448-bit message, a 1000-byte pattern and the
million 'a'; HMAC-SHA-256 of RFC 4231's cases 1 to 5 (6 and 7 take a
131-byte key, above the 64 `hmac_init` accepts; the rabbit's keys are 32
bytes) and of the pattern under a 32-byte key: eleven digests, all equal
to Python's `hashlib` and `hmac`. The first build of the assembly got
every digest wrong: gpasm's `EQU` of a relocatable label keeps the offset
within the section and loses the relocation, so every `lfsr` on a round
variable pointed 0x0B too low; text macros instead.

### 7.6 What it could not establish

- **The 6525's write block.** DS39612 gives 8 holding latches and 8-byte
  write blocks for the PIC18F6525/6621/8525/8621; the emulator models that
  under `--pic 6525`. The first update on Lazare should be read back over
  ICSP once, whole, and compared with the slot: that is the proof of the
  sequence on the real part.
- **Write time.** The part's ~2 ms per block and ~2 ms per row erase are
  the datasheet's; the watchdog (512 ms) is cleared per block, with a
  margin of two orders of magnitude, but the figure is not measured.
- **Flash endurance.** 10 000 cycles per row on the PIC: one update is one
  cycle per row; the serial flash takes 10 000 to 100 000 per page, the
  state pages take one write per boot while an image is unconfirmed, and
  one per update after that.
- **A brown-out during a row write** leaves a row the part may have half
  programmed; the read-back against the slot catches it at the next boot
  (that is the "PIC differs" path), which the proof forces by corrupting a
  row.
- **Ed25519** was not attempted: a 255-bit field multiplication on an
  8-bit core with no hardware multiply beyond 8x8 is tens of kilobytes of
  code or minutes per signature in portable C; the key here is a shared
  secret the Terrier holds per rabbit, which is what the adoption already
  gives a tag's owner. The weakness is the one noted in section 9: anyone
  with a PICkit on J2 reads the EEPROM, and can flash anything anyway.

## 8. The protocol with the Terrier beyond the poll (design)

The Terrier keeps answering `p3.jsp` as it does for Violet's firmware
(`nabv1.py`): `7F`, frames `<type><len:3>`, `FF 0A`. It can tell this
firmware by `v=` (30 and above, `fw_at_least`) and append frames of our
own, which Violet's firmware would never see. The application ignores
types it does not know (it already skips them by length), so the Terrier
may add frames freely. Frames 03 (poll interval) and 04/05/06 keep their
meaning; ours start at 0x10:

| Frame | Body | What the rabbit does |
|---|---|---|
| 10 update | version (2, big-endian) | fetches `/vl/v1/fw` when newer than its own and not refused before (7.4). **Implemented.** The Terrier must serve `GET /vl/v1/fw?sn=&v=&b=` with the `app-N.img` made by `mkimage.py image --key <this rabbit's key>` for the board `b=` asks (0x25 or 0x27), `Content-Length` set, and keep one key per rabbit (32 bytes, generated at ICSP time with the owner, entered on the rabbit's page). The Terrier side is the private repository and is not changed here |
| 11 ears | left, right (2 x 1 byte, 0-16 holes from the top) | moves one ear then the other, as `ears.c` does; reports the position with the next poll in `sd=` as Violet's program did (`parse_sd`) |
| 12 leds | 5 LEDs x 1 byte (bits: R, G, B), a duration (1) | sets the TLC5922; the diagnostic LEDs (section 3) resume after the duration |
| 13 sound | index (1), length (3) | `GET /vl/v1/snd?sn=&i=` into the sound region (pages 976+), then plays it through the OKI (section 6, left); the directory page at the region's start |
| 14 sleep | until (2: minutes) | stops polling for that long, LEDs off, the night |
| 15 config | server name, SSID, key, language, flags (TLV) | written to the configuration page (both copies); takes effect at the next boot |
| 16 confirm | none | the Terrier saw this version: the application confirms the loader's state now instead of at the first answered poll (optional; the first poll already does it) |

The request keeps Violet's shape (`sn`, `v`, `st`, `tc`, `sd`) so that the
Terrier's existing parsing, adoption and rate limit apply unchanged; `b=`
(the board) is only on `/vl/v1/fw`. Nothing of this needs the Terrier for
the loader's proofs: `../emulator/tools/v1_native_warren.py --offer N
--image FILE` plays its part.

## 9. Risks, and what the emulator could not establish

- **The card's other pins.** The emulator models the bus (PORTE, PORTD,
  RF5-7, RG4) and the COR write and nothing else: the card's reset line,
  its power switch, card detect and /IOIS16, which Violet's hardware
  surely has, are not in the model. Measured instead (Violet's 1.4b under
  `--trace-pins`, 2026-11-24): besides the bus it drives **RG0 and RG3 low
  at 3 ms** (with PORTE as outputs) and **RF4 low at 284 ms, 110 ms before
  the COR write** at 394 ms; the card's reset or power enable are likely
  among them. This firmware sets the same three pins the same way, in the
  same order and with the same spacing (`prism.c bus_init`); phase 2 reads
  the FCC schematic (`vendor/newbaztag/doc/schematics`) to name them.
- **The flash's chip select is RB4**, measured: Newbaztag's two pages
  disagree (RB3 or RB4), so Violet's 1.4b was run with `--trace-pins
  --trace-bus`: every serial flash operation (opcodes E8, 82) sits between
  `pins B: DF -> CF` and `CF -> DF`, bit 4; RB3 never moves. This code uses
  RB4 and keeps RB3 (the flash's RST per `components.md`) high.
- **Timing on the real card.** The emulator's card answers in the same
  instruction; the real one takes up to tens of milliseconds for
  INITIALIZE and the BAP busy bit is real. The waits are bounded
  (`WAIT_LOOPS`, ~100 ms) and logged; too short on hardware means one
  constant to raise.
- **The RIDs Violet writes that we do not** (FC03 channel, FC06 system
  scale, FC84 basic rates, FCB3, FCB4): their values are not in the
  emulator's log (it logs sizes, not values). If the real card refuses to
  associate, read them from the 1.4b code (the RID write routine at 0x35C8)
  and add them.
- **SDCC's output on hardware** has not run anywhere but the emulator's
  core, which executes the standard PIC18 set exactly; the CONFIG bytes
  are Lazare's. Bank selection bugs would show in the emulator too (same
  core), so the residual risk is the peripherals' timing, above.
- **The loader's key.** A per-rabbit HMAC key must be readable by the
  loader and by nobody else; with code protection off (Lazare's CONFIG,
  so that the firmware can always be read back) it is readable over ICSP.
  Phase 2 decides: CPD on the data EEPROM alone, or accept that a PICkit
  on J2 beats everything anyway (it does: it can flash anything).
- **RAM on the 2.0b boards** is the same 3 968 bytes (PIC18F6627); their
  flash is 96 KB. One source, two link scripts.
