# What WPA2 would cost a Nabaztag v1

The v1's Prism 2.5 card encrypts WEP only. A WPA2 v1 has to run the
4-way handshake (HMAC-SHA-1, the PRF, AES key unwrap) and every frame's
CCMP (AES-128: a counter block and a CBC-MAC block per 16 bytes) on its
PIC18F6627: 24 MHz crystal, HS oscillator, no PLL (`FOSC = 0x02` in the
dump), so 6 million instruction cycles a second.

`cost.c` runs each piece once on a published test vector and marks its start
and end on LATA; `harness.c` runs it on the v1 emulator's PIC18 core
(`../pic18.c`), checks the results and counts the cycles.

```sh
podman build -t v1-wpa-cost -f emulator/v1/wpa/Containerfile .   # from firmware-arm
podman run --rm v1-wpa-cost
```

## Measured (2026-11-16, SDCC 4.4.0; AES also by hand, `aes_gen.py`)

| Piece | Cycles | Time on a v1 |
|---|---|---|
| AES-128, one block (FIPS-197 C.1) | 43 848 | 7.3 ms |
| SHA-1, one block ("abc") | 119 207 | 19.9 ms |
| HMAC-SHA-1, short message (RFC 2202 #2) | 480 383 | 80 ms |
| CCMP, one 16-byte block | 89 140 | 14.9 ms |
| AES-128, one block, **assembly** (same vector) | 2 874 | 0.48 ms |
| CCMP, one 16-byte block, assembly AES, C glue | 7 572 | 1.26 ms |

Projected with the assembly AES: the 4-way handshake about 0.8 s (SHA-1 in
C is most of it); CCMP 12.4 KB/s (a 300-byte poll 24 ms, a 1500-byte frame
0.12 s); PBKDF2 from the passphrase 5.4 min (SHA-1 in C).

## What it says

- **Feasible in principle.** The handshake fits the access point's timeouts;
  a poll every 12 s costs a fraction of a second.
- **AES has to be hand-written assembly, and is.** `aes_gen.py` writes it
  (S-box read with TBLRD from a 256-byte boundary, ShiftRows folded into the
  addressing, xtime on W): 15 times SDCC's, FIPS-197 checked. The C loops
  around it are now a quarter of CCMP's time.
- **PBKDF2 does not belong on the rabbit.** It runs once per passphrase:
  the configuration page can compute the 32-byte PMK in the browser, or the
  rabbit once at first start and keep it in its serial flash.
- Not counted: moving each frame through the card's BAP twice (CCMP reads
  the plaintext for the MAC, then writes the ciphertext).

## RAM and stack

WPA2 needs about 450 bytes: round keys 176 (or none, expanded on the fly at
a cost in time), PTK 48, GTK 16, packet numbers 12, SHA-1 state and block
84, AES state and temporaries 34, the handshake's nonces 64.

`nabv1 --ram-map` on 140 s against the Terrier's v1 handler (boot, join,
polls, program, ears, a hand on an ear, a 3 s press), 2026-11-16:

- **172 bytes never touched**: `0x258-0x2A3` (76) and `0xF00-0xF5F` (96);
- **2 157 never read** (written only), among them `0xD00-0xF5F` (608),
  `0x7D0-0x9D0` (513) and `0xC00-0xCF8` (249): free only if no path this run
  did not take reads them (sound, the configuration page, a scan);
- the hardware stack reached **13 of 31** levels.

## What only a real v1 can tell

WPA2 with host-side encryption needs the card's **station firmware 1.7.0 or
newer**: the RSN element of the association request goes in RID `FC4D`
(generic element), which Linux's hostap driver sets only from 1.7.0
(`hostap_hw.c`, `sta_fw_ver >= PRISM2_FW_VER(1,7,0)`, v6.1). The emulator answers
1.7.x, a guess; the real card's version is read from RID `FD20`, which takes
a probe firmware flashed over ICSP.

## Where WPA2 goes into Violet's firmware (design, 2026-11-16)

Found with `nabv1 --trace-bus`, which now prints the PIC's PC and four return
addresses for every card command and BAP seek, and read in Newbaztag's
`nabaztag_.asm`:

| Address | What it is | Hook |
|---|---|---|
| `0x3EC6` / `p_3E66` | write a card register (W = register, `0xFA:0xFB` = word) | none |
| `0x3CDA` | issue a card command and wait | none |
| `0x3694`-`0x3776` | **send a frame**: BAP1 to the TX buffer (fid in `0x138:0x139`), descriptor words, then command `010B` (TX with reclaim) at `0x375C`-`0x3764` | `call wpa_tx` in place of `0x375A`-`0x3764`: rewrite the buffer as an 802.11 frame (Protected, CCMP header, ciphertext, MIC), then the original command |
| `0x6CC0` → `0x3D88` | RX event taken and acknowledged | before the firmware parses (`0x39E8` reads the descriptor): `wpa_rx` decrypts in the card's buffer and writes back the 802.3 layout the firmware expects (header at 46, LLC at 60); EtherType `888E` (the 4-way handshake) is consumed there and never reaches the firmware |
| RID writes `FC28` (WEP flags), `FC2A` (authentication), `FC24`-`FC27` (keys) at join | WEP setup | WPA2 instead: open authentication, flags = privacy + host encrypt + host decrypt, RSN element (CCMP, PSK) in `FC4D` |

The added code sits in free flash above `0xC8E1` (the firmware's last byte),
its RAM in what `--ram-map` shows untouched; the PMK comes from the
configuration page (computed in the browser, or once by the rabbit).

Still to be proven on the emulator, in this order: the card model in
host-encrypt mode with an access point that speaks WPA2 (authenticator and
CCMP on the host side, `../../src/vapcrypto.c` has the PRF and key wrap),
then `wpa_tx`/`wpa_rx` and the handshake on the PIC, then a v1 polling the
Terrier through WPA2.

Two things only the real card can answer: its station firmware version
(1.7.0 at least), and whether it accepts BAP writes into a received frame's
buffer (`wpa_rx` decrypts in place; otherwise it needs a copy through RAM).
