# A native firmware for the Nabaztag v1 (2005) -- design, phase 1

Violet's v1 firmware is a PIC18 interpreter for Violet's VM. It cannot be
updated over the network (`../v1-icsp/VM-ESCAPE.md`), and on Clara's rabbit
(Lazare: 1.4b, PIC18LF6525, 48 KB of flash, AT45DB081B serial flash, Prism
2.5 card with station firmware 1.4.2) it has 94 bytes free
(`../v1-icsp/IMPROVEMENTS.md`). This is its replacement, written in C and
flashed over ICSP with a PICkit 3 (done four times on 2026-11-24, the
original restored each time). Phase 1 is the skeleton end to end, proven in
the emulator only: no rabbit, no PICkit, no write to any PIC.

What runs (2026-11-24, `../emulator/tools/v1-native-proof.sh`, the numbers
are the emulator's, with the PIC's cycle count as its clock):

| Step | Measured |
|---|---|
| Image | 22 920 bytes of 49 152 (46 %), RAM 1 015 bytes of 3 968 (25 %) |
| Boot to the card associated | 579 ms (200 ms of it the two waits copied from Violet's pin sequence, below) |
| DHCP lease | 614 ms |
| DNS answer | 1 102 ms (the hop's ARP first, then the query) |
| First poll sent | 1 592 ms; answered and parsed (status line, frame 03), the connection closed by both sides at 1 613 ms |
| Polls | every 10 s, 3 in a 40 s run, each a connection opened, read, closed |
| Ears | each moved 2 holes after the first answer, one motor at a time, the emulator's sensors counted |
| Watchdog (512 ms, on) | never fired |
| Serial flash | AT45DB081B recognised by its density bits, page 0 written and read back, the boot counter at 2 on the second run |

## 1. Prior art, read first

- **Newbaztag** (Niels Moseley, MIT; `../vendor/newbaztag`, and
  github.com/trcwm/Newbaztag `firmware/new/src`): a C firmware for the
  PIC18F6627 built with XC8, a serial command protocol (LEDs, motors,
  timer, homing) and no network -- his card was dead and he meant an ESP
  module. What it gave: the pin list (`doc/components.md`), the TLC5922 data
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
v1native-toolchain make`; one command, reproducible, `out/nabv1native.hex`
and the sizes.

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
1.3 KB), and the port's banked access to RAM. Levers if room runs short,
in order: the log strings and their calls (about 1.5 KB), the 32-bit
arithmetic in TCP (sequence numbers can be kept as 16-bit deltas on one
connection), the DHCP option walk written as a table.

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

| Range | Size | Phase 1 | Later |
|---|---|---|---|
| 0x0000-0x0FFF | 4 KB | the application starts at 0 | **the loader**: reset vector, AT45 read, HMAC-SHA-256, 64-byte row write, a UART line; write-protected by WRTB (the 18F6525's boot block is 0x0000-0x07FF: the loader should fit 2 KB, or protection covers only its first half) |
| 0x1000-0xBFFF | 44 KB | the application (22 920 bytes used today, linked from 0) | WPA2 (~6 KB: CCMP + 4-way handshake, PMK computed off-rabbit), OKI audio (~1.5 KB), the Prism flashing path (~2 KB), a program layer of our own (open), a Violet-VM compatibility layer if ever wanted (~20 KB: not planned) |

Left after phase 1 for all of that: about 22 KB, the 4 KB of the loader
reserved. Enough for WPA2, audio, the Prism flashing path and the loader
(about 14 KB together), not for a Violet-VM layer on top. The application must be linked from 0x1000 when
the loader exists; today's link at 0 is only phase 1's.

### 4.2 PIC RAM, 3 968 bytes

| Use | Bytes | Phase |
|---|---|---|
| Measured today (udata + the 256-byte stack, `size.py` from the map) | 1 015 | 1 |
| of which the frame buffer | 340 | 1 |
| SDCC's stack (its default; nothing recurses, 128 would do) | 256 | 1 |
| WPA2: PTK, the replay counter, AES round keys (`v1/wpa/README.md`, branch emu/v1-wpa-cost) | ~450 | 2 |
| Audio: one serial flash page as the OKI FIFO's source | 264 | 2 |
| Loader: a 64-byte row, a SHA-256 state | ~200 | 2 |
| Left | ~2 000 | |

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
| 0-1 | 528 B | 1 056 B | **Configuration**, two copies (A/B with a sequence number, the newer wins; a write cut by the brown-out loses one copy, not the configuration): magic `NAB1`, format, boot counter today; then server name, language, SSID, WEP key or **WPA2 PMK (32 B)**, the flags (opt-ins). The loader's per-rabbit HMAC key is *not* here (the serial flash is readable by anyone with the board): it goes in the PIC's data EEPROM, see risks | **1** (page 0 only) |
| 2-15 | 3.7 KB | 7.4 KB | **Ring log**: 32-byte records (tick, event, 24 bytes of detail), 115 / 231 records, read back over the serial port or sent with a poll | 2 |
| 16-207 | 50.7 KB | 101.4 KB | **Image slot A** (known-good): a header page (length, version, HMAC-SHA-256, a "complete" flag written last) and the PIC image; 48 KB of PIC18LF6525 flash fits in 187 pages of 264 B, and a PIC18F6627's 96 KB in 187 pages of 528 B: the same slot serves both boards | 2 (loader) |
| 208-399 | 50.7 KB | 101.4 KB | **Image slot B** (next): the loader copies B into the PIC only once its HMAC checks, keeps A as the fallback, and the first boot of a new image that does not confirm itself within N boots goes back to A | 2 (loader) |
| 400-911 | 135 KB | 270 KB | **Prism station firmware** (sf010704: ~171 KB as S-records, under 100 KB as the binary the card's download command takes: primary + secondary images with their PDR fix-ups, per `prism2_srec`) for flashing the card from the rabbit | 3 |
| 912-975 | 16.9 KB | 33.8 KB | **Program layer**: a small interpreted layer of our own, non-critical behaviour fetched from the Terrier (animations, ear choreographies, LED scripts); or Violet's frame 05 if compatibility is ever wanted (33 KB fits on the 161 only) | 3 (open) |
| 976-4095 | 823 KB | 1 647 KB | **Sounds and animations**: OKI ADPCM at 16 kHz is 8 KB/s, so 103 s on the 081B and 206 s on the 161; a directory page every 64 pages | 2 (audio) |

Phase 1 needs only page 0 (and proves the driver: read, write with erase,
read back, across runs with the emulator's `--flash FILE`). The ring log,
the slots and the rest are reserved, not written.

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

## 6. What phase 2 needs

1. **Audio**: the OKI ML2870A path is read (IMPROVEMENTS.md section 3:
   CCP1 PWM at 6 MHz on RC2, the init table, 0x35 rate, 0x29/0x33 start,
   register 0x01 bursts of 64, FIFO status in 0x06), the emulator logs the
   registers; a sound fetched over the poll's connection is written to the
   serial flash page by page (the RX reader streams, the page buffer is
   264 bytes), then played. The nibble order and the ADPCM table are still
   to be settled on the real rabbit (IMPROVEMENTS.md #1): two test files.
2. **A protocol with the Terrier**: keep `p3.jsp` so the Terrier needs
   nothing new for the first firmware, and add our own answer frames (the
   Terrier can tell `v=30`): a frame for "play sound N from URL", "ears to
   L/R", "LEDs", "sleep until", "config: server, SSID, key", "update:
   image at URL, HMAC", so that Violet's VM is not reimplemented. The
   Terrier side is `nabv1.py` (private repo, read only here).
3. **The loader** (section 4): Naboot-style, image in slot B over HTTP,
   HMAC-SHA-256 with a per-rabbit key written at ICSP time, copied into
   the PIC row by row, "complete" flag last, fallback to slot A. The
   emulator's core already has `TBLWT` and the `EECON1` write path
   (`emulator/v1/board.c`).
4. **WPA2** after the card is reflashed to station 1.7.4 (`../v1-icsp/
   prism-fw.sh`): host-side CCMP and the 4-way handshake, PMK from the
   Terrier's page (computed off-rabbit), ~6 KB and 450 B of RAM per the
   WPA2 study; the card must do the station firmware's part (1.7.x can
   send raw 802.11 and receive with encryption off).
5. **Configuration page and a serial console**: server, SSID, key, language
   written over J3 (and later from the Terrier), the two-copy page.
6. **The poll interval from frame 03**, the ring log, button handling (a
   click, a double click, a long press reported in `sd=` as Violet's did,
   `nabv1.py parse_sd`).

## 7. Risks, and what the emulator could not establish

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
