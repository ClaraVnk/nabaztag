# What could be improved in the Nabaztag v1's own firmware

A study, 2026-11-24, of Violet's two PIC firmwares for the v1 (2005), the way
Naboot improved the v2's: what an owner would gain, what the code says, what
it would cost on this PIC and this card, and how the emulator would prove it
before a rabbit. Nothing here was flashed. Every claim about the firmware
cites an address in one of the two dumps or a run of `../emulator/tools/v1-study.sh`.

| | 1.4b (Lazare) | 2.0b (Newbaztag's rabbit) |
|---|---|---|
| Dump | `dumps/violet-v1-1.4b.hex` | `../vendor/newbaztag/firmware/original/nabaztag.hex` |
| PIC | PIC18LF6525, 48 KB flash | PIC18F6627, 96 KB flash |
| Program | up to 0xBFA1; **94 bytes free** | up to 0xC8DB; **46 884 bytes free** (0xC8DC-0x17FFF) |
| CONFIG1H | 22 (HS oscillator) | 02 (HS oscillator) |
| CONFIG2L / 2H | 0A / 0F: brown-out reset on, watchdog on (1:128, ~512 ms) | 18 / 1E: both off |
| Serial flash | AT45DB081B, 264-byte pages | AT45DB161 |
| Poll | `/vl/FR/p3.jsp?sn=&v=14&st=&tc=` (strings at 0x16B6-0x16E2) | the same with `&ex=` and `v=20` (0x1CBD-0x1CEE) |
| Configuration page | English, "Nabaztag Manual Configuration 1.4b" (0x10F0), fields Server name, Local IP/mask/gateway, Dns server, Proxy, Wep, Dhcp, Serial (0x118F-0x127C) | bilingual with a style sheet (0x10F0-0x18CE), the same fields plus "Serial extension" (`ex`) and "Violet Platform" (the server) |
| VM opcode table | 200 entries of 2 bytes at 0x16F3, dispatch at 0x8B64 | 200 entries of 3 bytes at 0x1CFF, dispatch at 0x90D0 |

Tools, all in this repository: `pic18dis.py` (disassembler, function table,
strings, a diff by function, the unreachable code), `../emulator/tools/v1-study.sh`
(the network broken under the firmware, and what it feeds the sound chip),
`../emulator/tools/violet-vasm/v1-audi.py` and `sound-test.vasm` (a test sound
and the program that plays it). Commands in `docs/RUNBOOK.md`.

## 1. 1.4b and 2.0b, by function

`pic18dis.py diff dumps/violet-v1-1.4b.hex ../vendor/newbaztag/firmware/original/nabaztag.hex`:
261 functions in 1.4b, 269 in 2.0b; **132 identical** by fingerprint
(mnemonics and literals, addresses masked), **89 changed** by a few
instructions, 40 only in 1.4b, 48 only in 2.0b. It is one code base built
twice, not two firmwares:

- Most of the 89 "changed" functions differ by one tail: 2.0b's build
  shares a `clrf 0; clrf 1; return` or `movlw; return` tail between
  functions (`return` in 1.4b becomes `bra` to a common tail in 2.0b), and
  literal addresses of strings moved. The card bus routines (1.4b
  0x3084/0x3120 vs 2.0b 0x37A0/0x383C), the poll builder (1.4b 0xA5E4 vs 2.0b
  0xACF6, 83 instructions, identical but for the string addresses), the TCP
  and HTTP code, the OKI sound path (below) are the same code.
- **New in 2.0b** (no match in 1.4b): the configuration record in serial
  flash with its magic `G` at offset 0x71, defaults written at first boot
  (0xC34E-0xC4EE, from the boot code at 0xC4F0 called at 0x6A52); the `ex=`
  serial extension that this record carries; **writes through the serial
  flash page cache** (0xBA62 flushes a slot, 0xBF84 writes a byte and marks
  the slot dirty at RAM 0x4CB+slot; the cache itself, five 256-byte slots
  with tags at RAM 0x200 and buffers from 0x4D0, read-only, is in both:
  1.4b 0xB42C = 2.0b 0xBBDC); the bilingual configuration page; and about
  1.5 KB at 0x9E7A-0xA56E (0xA2CE and four helpers) that references no
  string and that I did not identify.
- **Only in 1.4b**: nothing an owner would notice. The 40 "only in A" are
  smaller copies of 2.0b functions that the fuzzy match did not pair
  (0x44AC/0x4CBE are the same scan and join code as 2.0b 0x4BCA/0x5400 with a
  different split), and the English-only page.
- Reconnect, DHCP, DNS and the poll behave identically on both (section 2
  measured both). The watchdog and brown-out difference is in CONFIG, not in
  code: 1.4b's code never lets the watchdog expire (`clrwdt` at 0x29A4 in
  2.0b sits in the sound loop too), the emulator confirmed it on 2026-11-24.

## 2. What the firmware does when the network breaks (measured)

`../emulator/tools/v1-study.sh` runs Violet's wait program behind the emulator's
gateway and breaks the network under the firmware. Both firmwares gave the
same timeline to the second; the numbers are 1.4b's.

| Scenario | What the firmware does | Needs unplugging? |
|---|---|---|
| Nothing listens on port 80 (connection refused) | a poll every 10.8 s is refused; after 4 refusals it re-initialises the card (INITIALIZE, ENABLE at 55.9 s), redoes DHCP, and polls again; **it never asks DNS again** (one `DNS p.nabaztag.com` at 4.1 s in a 400 s run) | no |
| The access point disappears for 20 s (`--link-down 30 20`, the card reports link status 2 then 1) | polls into the void meanwhile; the next poll leaves **0.2 s after the card reports the link back** (link 50.3 s, poll 50.5 s) | no |
| The access point never comes back (`--link-down 30 0`) | the card is re-initialised every 52.7 s (69.1, 121.8, 174.5 s), for ever | no, if the card re-associates on ENABLE |
| No DNS answer (`--dns-mute 60`) | the query is repeated every 2.26 s, 20 times, then the card is re-initialised (58.0 s) and DHCP redone; the first answer after 60 s is taken (61.3 s) and the polls start | no |
| The server closes or vanishes between two polls | nothing to notice: a poll is one short connection, closed by the rabbit after the answer (`v1: closed` in the log); the next one reconnects | no |

So the v1 does not have the v2's disease (a session held open that needs a
power cycle after the server drops): its protocol is a connection per poll,
and every failure path ends in a card re-initialisation after about 52 s.
Two real weaknesses remain:

- **The server's address is resolved once per power-on** (no DNS query after
  the first answer, across card re-initialisations). If the Terrier's address
  changes, every v1 stays on the old one until unplugged. Cheap to fix (a DNS
  query before each connection that fails, or every N polls), and the fix is
  proven by `--dns-mute` followed by a changed answer. Cost: the code that
  sends the query exists (it runs at boot); a call site and a flag, under
  100 bytes.
- **Nothing tells the owner where it stopped.** The firmware writes single
  letters on its serial port (`W`, then `WLAN04Terrier` once joined, the
  log in `../v1-serial/README.md`) and shows nothing on the LEDs for "no
  DHCP", "no DNS", "server refused". The LEDs are driven by the VM program,
  which does not run until the first answer; a v2-style diagnostic would be
  a few LED colours set by the firmware before the program exists. 2.0b has
  the room for it; 1.4b does not (section 4).

## 3. The sound: what the firmware feeds the OKI ML2870A

Read in 2.0b at 0x2890 (ADPCM start), 0x28FE (data), 0x273E (MIDI score),
0x25E0/0x25F4 (the chip's clock), 0x0670-0x08FE (the streaming task), and
found byte for byte in 1.4b at 0x217E, 0x21EC, 0x202C, 0x1ECE/0x1EE2,
0x0664. Confirmed on the emulator's register log with a test sound at each
rate (`tools/v1-study.sh ... sound`).

**The chip's clock.** CCP1 in PWM mode on RC2, Timer2 prescaler 1, PR2 = 0,
duty 2/4: a square wave at Fosc/4 = **6 MHz** with the HS crystal (0x69A4
`movlw 0` before `call 0x25E0`; 1.4b 0x639E). Both firmwares.

**The container.** The sound is a file of the program frame (`/name file
x.audi` in Violet's assembler puts it after the bytecode with a directory of
4-byte ends; `WAVPLAY rN` plays file N). The firmware reads the directory
(0x3EA-0x5C0: start = `end[N-1]`, end = `end[N]`), then **one header byte**:
offset 8 of the file, the high byte of the big-endian sample rate:

| byte 8 | register 0x35 | rate |
|---|---|---|
| 0x1F (8 000 Hz) | 3 | 8 kHz |
| 0x7D (32 000 Hz) | 7 | 32 kHz |
| anything else (0x3E = 16 000 Hz, what Violet's server and the Terrier send) | 6 | 16 kHz |

(0x2890: `xorlw 0x7D` -> 7, `xorlw 0x1F` -> 3, else 6.) Nothing else in
the 16-byte header is read: not `Audi`, not `80 01 01 04`, not the three
zeros, not the 3-byte length. The data starts at offset **16** (`addwf 0x10`
at 0x662) and ends where the directory says.

**The start.** Register 0x63 (volume, from the program's `VOL`), 0x39 (ADPCM
volume) at `STARTMUSIC`; then 0x35 <- rate code, 0x29 <- 1 (ADPCM interrupt
enable), 0x33 <- 1 (ADPCM start), a 10 ms wait. Measured at 10 835 ms on the
emulator for the 16 kHz file: `35 <- 06`, `29 <- 01`, `33 <- 01`.

**The data.** Register **0x01** is the ADPCM FIFO: one register byte, then a
burst of up to **64 data bytes** in one chip select (0x28FE, `movlw 0x40` at
0x736), the bytes exactly as they are in the file, no nibble swap, no
transformation; the ADPCM interrupt (0x29) is turned off for the burst and
on again after it. Measured (16 kHz file, 1.4b and 2.0b alike): `35 <- 06`,
`29 <- 01`, `33 <- 01`, then bursts of 64, 64, 64, 14 (the serial flash
page boundary), 64, 64 ... bytes, the first one 12 ms after the start. The
streaming task (0x670) reads register 0x06 (FIFO status) and refills when
bit 0 says so; the PIC reads the file through its serial flash page cache.
At the end, 0x33 <- 0 and 0x29 <- 0 (`stopADPCM`, 0x28DC).

So, for the question that matters most right now (sounds from the Terrier
play on Lazare but are unintelligible): **the firmware imposes nothing on
the ADPCM bytes but the rate code and the 16-byte offset.** What makes the
sound intelligible or not is between the encoder and the chip:

1. the **nibble order**: the Terrier encodes Dialogic/VOX, first sample in
   the high nibble (what `sox -t vox` writes). OKI's own chips disagree
   among themselves: the MSM6295 plays the high nibble first, the MSM6258
   the low nibble first (MAME's `okim6258.cpp`, `m_nibble_shift` starts
   at 0). The ML2870A's order is not in any datasheet I could find
   (the part is undocumented on the web; its sibling ML2860 is a PCM/FM
   ringer chip). **The test is two files on the real rabbit**:
   `v1-audi.py 16000 1.5 a.audi` and the same with `--low-first`: a 440 Hz
   tone that doubles to 880 Hz half-way. The right order plays the tone;
   the wrong one plays noise. This costs nothing on the firmware.
2. the **algorithm**: Dialogic ADPCM (12-bit, 49 steps, the MSM6258 family)
   is the assumption behind the Terrier's encoder. If the chip wants a
   different table, the tone test plays a tone with a wrong timbre or a
   drift; a sweep of candidate tables is then cheap on the server side.
3. the **rate**: excluded. Violet's captured sounds carried `3E 80` (16 000)
   and played with register 0x35 = 6, the same path the Terrier's take.

The emulator cannot settle 1 or 2: its OKI is a register log, the FIFO
status it returns is 0, so it shows the registers and the first bursts and
not the pace (`../emulator/tools/v1-study.sh` prints both).

## 4. Room: what fits where

- **1.4b: nothing fits.** 94 bytes free after the program (0xBFA2-0xBFFF).
  `pic18dis.py dead` (a walk from the reset vector through calls, branches,
  skips and the VM's opcode table) finds **896 bytes of unreachable code**,
  in pieces: 136 at 0x0070, 166 at 0x688E, 66 at 0x5F9C, 44 at 0x39BC, 40 at
  0x37BA, and seven pieces of 24-32 bytes. Reclaiming them means moving code
  around the holes; a single change of a few hundred bytes (the DNS
  re-query, a LED diagnostic) would fit only after that compaction, which is
  itself a rebuild of a firmware we have no source for. The English page
  text (0x10F0-0x1600, about 1.3 KB) is the other reserve, at the price of
  the page. Anything larger (WPA2: about 6 KB of code and 450 bytes of RAM
  per the WPA2 study (v1/wpa/README.md on the branch emu/v1-wpa-cost)) **cannot go on a PIC18LF6525**.
- **2.0b: 46 884 bytes free** above 0xC8DC, 6 KB of them reachable-but-dead
  elsewhere not needed. WPA2, a DNS re-query, a LED diagnostic and a loader
  all fit, in flash. RAM is the limit: 3 936 bytes on the 18F6627, 172 never
  touched and 2 157 never read in a 140 s run (`--ram-map`, 2026-11-16), to
  be re-measured on the sound and configuration paths before anything claims
  them.
- A **1.4b rabbit can run 2.0b's code only with the bigger PIC**: the
  PIC18LF6525 and the PIC18F6627 are pin-compatible (TQFP-64, the same
  pinout in DS39612 and DS39646), the serial flash differs (081B vs 161,
  the 2.0b firmware addresses 264-byte pages the same way: the emulator runs
  2.0b on either model). Swapping a TQFP-64 is a hot-air job, not a
  firmware one; it turns a 1.4b rabbit into a 2.0b rabbit with 46 KB free.

## 5. An update path

The firmware never writes its own flash: no `TBLWT`, no `EECON1` access in
either dump (`pic18dis.py dis | grep -c tblwt` gives 0; the only program-
memory access is `TBLRD` for strings and the opcode table). So:

- **ICSP is the only path today**, five wires on J2 and a PICkit
  (`README.md`), and the only one 1.4b will ever have (section 4).
- **On 2.0b, a small loader is realistic**: the PIC18F6627 writes its own
  flash in 64-byte blocks (`TBLWT` + `EECON1` with the WR sequence, DS39646
  ch. 6). A loader at the top of flash (about 1 KB: receive a block over the
  poll's HTTP connection, verify, write, boot the new image) with the image
  signed by Ed25519 is what Naboot does on the v2; on the PIC an Ed25519
  verify is too slow (SHA-512 alone is a problem), **HMAC-SHA-256 with a
  per-rabbit key written at ICSP time** is the realistic signature (SHA-256
  about 0.1 s per 64-byte block at the measured SHA-1 cost). Risks: a write
  interrupted by the brown-out or the watchdog bricks the rabbit unless the
  loader itself never moves and the image is written block by block with a
  valid-image flag last; the first installation of the loader is still an
  ICSP job on every rabbit, so the loader only pays if more than one update
  follows. Proof on the emulator: its PIC18 core has no `TBLWT` either
  (`pic18.c`), it needs the write path and the block-erase timing first, then
  an image written over `--port80` and booted in the same run.

## 6. The Wi-Fi card: reading its station firmware (unparks WPA2 or buries it)

**Measured on Lazare, 2026-11-24 (`wifi-probe.py`): station firmware
1.4.2.** Below 1.7.0: WPA2 is buried for this card as it stands (RUNBOOK,
the v1 section).

WPA2 on the v1 is parked until the card's station firmware version is known
(1.7.0 at least for host-side encryption, `hostap_hw.c`). Neither firmware
ever reads it: a 400 s run with `--trace-bus` shows **only RID writes**
(FC00, FC02, FC03, FC06, FC07, FC2A, FC84, FCB0, FCB3, FCB4) and no RID read
of FD0B (NIC identity) or FD20 (station identity).

A probe is cheap, on either PIC:

- the card's RID read is the routine the firmware already has for its own
  purposes (the access command at 0x3CDA in 2.0b, BAP read at 0x3F64-0x4054;
  1.4b 0x35C8, 0x3852-0x3998): issue ACCESS (0x0021) for 0xFD20, read the
  record through BAP0: 4 words after the length and type, `version
  major.minor.variant`;
- write the 8 bytes on the serial port next to the `W` the firmware already
  prints at boot (the UART write is at 0x1926 in 1.4b, `setup_UART` in
  2.0b): about 60 bytes of code, in 1.4b's 94 free bytes or in the dead
  piece at 0x0070, a `call` patched into the boot sequence after ENABLE;
- read it with the Raspberry Pi already wired to Lazare's serial port
  (`../v1-serial`), then write the original back and verify, as
  `no-brownout.py` did on 2026-11-24.

The emulator answers 1.7.x (a guess, `prism.c` line 179) and would prove the
probe's code, not the card's answer. If the card says less than 1.7.0, WPA2
is over for the v1 whatever the PIC; if more, 2.0b-class rabbits can have it
and 1.4b-class rabbits only with the bigger PIC.

## 7. The list, in order

| # | Improvement | For the owner | Evidence | Cost | Risk | Proof |
|---|---|---|---|---|---|---|
| 1 | **Fix the sound from the server side** (nibble order / table), no firmware change | voices one can understand | section 3: the firmware passes the bytes through | two test files, a few minutes with Lazare | none | `v1-audi.py` at both orders on the real rabbit; the emulator shows the registers |
| 2 | **Station firmware probe** over serial, 1.4b and 2.0b | decides WPA2 once and for all | section 6 | ~60 bytes, ICSP write then restore | the ICSP write itself (done once already) | emulator runs the patched image; the card's answer needs the rabbit |
| 3 | **Re-resolve the server's name** (before a retry, or every N polls), 2.0b | a server that moves does not strand the rabbits | section 2, `--dns-mute`, the single DNS query | under 100 bytes, calls existing code | low; the retry path is the measured one | `--dns-mute` then a changed answer, the poll follows |
| 4 | **A LED diagnostic before the program runs** (no AP / no DHCP / no DNS / refused), 2.0b | the "it does nothing" rabbit says why | section 2: single letters on serial only | a few hundred bytes; the TLC5922 driver exists (the boot clears the LEDs, `leds:` in the emulator's log) | low | each scenario of `v1-study.sh` shows its colour in `leds:` |
| 5 | **A loader with signed images over the poll**, 2.0b | updates without a PICkit | section 5 | ~1 KB loader + HMAC-SHA-256, a flash-write model in the emulator first | a half-written image; mitigated by flag-last writes | emulator: write path, then an image installed over HTTP |
| 6 | **WPA2**, 2.0b-class only, after #2 says >= 1.7.0 | the v1 on a modern network | the WPA2 study (v1/wpa/README.md on the branch emu/v1-wpa-cost) | ~6 KB, 450 B RAM, 0.8 s handshake | the card's BAP write into RX buffers (unknown) | the WPA2 access point in the emulator, then the rabbit |
| 7 | **1.4b: brown-out off** stays a per-rabbit CONFIG change, not a firmware one | a rabbit that restarts on an ear | `no-brownout.py`, 2026-11-24: Lazare's right ear is a motor fault | one CONFIG bit | the reset is what cuts a stalled motor | done once, written back |
| 8 | Poll interval, scan, roaming | nothing to gain: the interval is the server's (frame 03), the card roams by itself (section 2, link back -> poll in 0.2 s) | | | | |

Not worth a line more: TLS on this PIC (Violet's author said so, the
measurements of SHA-1 at 20 ms a block agree). Not established here: the
ML2870A's nibble order and ADPCM table (no datasheet; two files on the
rabbit decide), the card's firmware version (the probe decides), the exact
retry counter in the code (4 refusals then re-initialisation is measured,
not read), and how 2.0b's page cache behaves on the smaller AT45DB081B.
