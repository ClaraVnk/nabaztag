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

## Measured (2026-11-16, SDCC 4.4.0, plain C)

| Piece | Cycles | Time on a v1 |
|---|---|---|
| AES-128, one block (FIPS-197 C.1) | 43 848 | 7.3 ms |
| SHA-1, one block ("abc") | 119 207 | 19.9 ms |
| HMAC-SHA-1, short message (RFC 2202 #2) | 480 383 | 80 ms |
| CCMP, one 16-byte block | 89 140 | 14.9 ms |

Projected: the 4-way handshake about 0.9 s; CCMP 1.1 KB/s (a 300-byte poll
0.28 s, a 1500-byte frame 1.4 s); PBKDF2 from the passphrase 5.4 min.

## What it says

- **Feasible in principle.** The handshake fits the access point's timeouts;
  a poll every 12 s costs a fraction of a second.
- **AES has to be hand-written assembly.** SDCC's code is the upper bound:
  CCMP is two AES per 16 bytes and nearly all of the per-frame time.
- **PBKDF2 does not belong on the rabbit.** It runs once per passphrase:
  the configuration page can compute the 32-byte PMK in the browser, or the
  rabbit once at first start and keep it in its serial flash.
- Not counted: moving each frame through the card's BAP twice (CCMP reads
  the plaintext for the MAC, then writes the ciphertext), and RAM: the
  firmware's own use of the 3 936 bytes is still to be measured.

## What only a real v1 can tell

WPA2 with host-side encryption needs the card's **station firmware 1.7.0 or
newer**: the RSN element of the association request goes in RID `FC4D`
(generic element), which Linux's hostap driver sets only from 1.7.0
(`hostap_hw.c`, `sta_fw_ver >= PRISM2_FW_VER(1,7,0)`, v6.1). The emulator answers
1.7.x, a guess; the real card's version is read from RID `FD20`, which takes
a probe firmware flashed over ICSP.
