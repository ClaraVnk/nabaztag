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
