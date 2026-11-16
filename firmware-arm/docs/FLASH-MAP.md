# Where the flash goes

Measured 2026-11-16 on Naboot 1.2.0.1, rebuilt unsigned on the dev VM
(`./build.sh --mode naboot-1.2.0.1 --remote <dev-vm> --runtime podman`):
the same 126 952 bytes as the release. The usable flash is 126 976 bytes
(`sys/ml67q4051.ld` less the configuration sector): **24 bytes are left.**

```sh
python3 tools/flash-map.py bin/naboot-1.2.0.1/Nab.elf bin/naboot-1.2.0.1/Nab.bin
```

| Group (name patterns, roughly) | -O1 (release) | -Os |
|---|---|---|
| Embedded bytecode `dumpbc` (boot program, config pages) | 27 400 | 27 400 |
| TLS, X.509, RSA, ECDSA, P-256 | 20 441 | 18 131 |
| Wi-Fi: rt2501, 802.11 | 19 650 | 16 914 |
| Metal VM | 15 594 | 14 562 |
| Other (main, init, libgcc division, RNG…) | 11 715 | 10 315 |
| WPA2/WPA3: EAPOL, SAE, AES, SHA-1 | 9 864 | 8 690 |
| USB host | 6 090 | 5 272 |
| Audio, LEDs, motors, RFID | 5 668 | 5 054 |
| Ed25519, X25519, SHA-512 (TweetNaCl) | 5 561 | 4 795 |
| IP, DHCP, DNS, TCP, HTTP, mDNS | 1 122 | 1 032 |
| **Image** | **126 952** (24 spare) | **114 224** (12 752 spare) |

Inside `dumpbc`, 7 331 bytes are text, about 6 900 of them the
configuration pages (HTML, CSS, JS; the shared stylesheet alone 2 777).

## What -Os costs in time

`tools/cost/run.sh` (qemu-arm, executed instructions, the firmware's crypto
sources), `OPT=-O1` against `OPT=-Os`, 2026-11-16:

| Case | -O1 | -Os | |
|---|---|---|---|
| X25519 (the key exchange) | 50 651 199 | 52 325 350 | +3.3 % |
| Ed25519 verify, 64 bytes | 170 829 941 | 176 973 308 | +3.6 % |
| P-256 scalar multiplication | 45 294 471 | 45 295 365 | 0 % |
| AES-128, one block | 7 296 | 7 020 | -3.8 % |
| AES-128-GCM, 1 KB | 827 549 | 795 381 | -3.9 % |

The key exchange that takes 18 s of its 60 s on a real ARM7 would take
about 18.6 s.

## The levers, cheapest first

1. **-Os instead of -O1: 12 728 bytes, measured, and FAILED on the test
   rabbit** (`--mode naboot-1.2.0.2`: 1.2.0.1 with `opt_size`; see below). Every hardware-validated image
   was -O1, and a layout change has woken an old bug here before: it ships
   only after the emulator proofs, a crypto timing check (the key exchange
   needs 18 s of its 60 on a real ARM7) and Nabazprout.
2. **The configuration pages**: about 6.9 KB of text in the bytecode; a
   tighter stylesheet and shared fragments, perhaps 1 to 2 KB.
3. **Radio tables for chips the rabbit does not have** (`RF5225RegTable`,
   `RF5226RegTable`, `RF2528RegTable`, 280 bytes each): ~560 bytes, once
   `rfic_report` says which RF chip every rabbit seen so far carries.
4. **TLS certificate checking** (X.509, RSA-PSS, ECDSA): several KB. A pinned
   server key would do the same job for one server; a design decision, not a
   size tweak.
5. **One signature scheme** (ECDSA P-256 is already there for TLS and SAE):
   would drop Ed25519 and SHA-512, ~3 KB, but changes the release key every
   rabbit trusts. Last resort.

Not a lever: rewriting in assembly (10 to 20 % of the C at most, months of
work, a new bug surface).

## Naboot 1.2.0.2 in flashsim (2026-11-16)

The built ARM image on the Unicorn ARM7 model (`emulator/flashsim`), unsigned:

- `boot`: main() after 4 559 instructions, `.data` in place;
- 1.2.0.1 writes 1.2.0.2: 28 erases, reset, the new image boots; watchdog
  fed 28 times, longest unfed 76 ms;
- 1.2.0.2 writes 1.2.0.1 back (testers must be able to return): 31 erases,
  boots;
- `sweep --mid`: a power cut in the middle leaves a mixed flash that does not
  boot, as for every update on this hardware (`OTA-ANTIBRICK.md`), no new
  fatal window.

The native emulator proofs (section 4 of the RUNBOOK) compile the C for the
host, so a compiler flag on the ARM build is invisible to them; the
bytecode inside the image is the same 27 400 bytes but for seven: the
version byte (1 -> 2) and the build mark, twice.

## Does -Os change the security?

The source is the same, so are the checks: an optimisation level does not
remove a test the C asks for. What it can change:

- **timing**: a compiler may turn constant-time code into a branch.
  `tools/cost/ct.sh` (SAE's hunting-and-pecking, the WPA3 password path)
  at `OPT=-O1` and `OPT=-Os`, 2026-11-16: the pairs stay equal at both
  levels (`pwe_round1` = `pwe_round12`, 161 060 360 and 162 240 014
  instructions; `dec_valid`/`dec_invalid` one apart at both, as before);
- **undefined behaviour**: a latent C bug can show at one level and not the
  other; the host tests (`firmware-arm/tests`, ASan and UBSan) are the net,
  and the test rabbit the last word.

## On Nabazprout: it does not come back (2026-11-16)

Sent over the air to the test rabbit only, at 17:33:23: the 114 224 bytes
arrived in the session in 4.1 s and were installed. Then the nose and belly
stayed orange; after unplugging it ten seconds, the first belly light
blinked orange and the Terrier never saw the rabbit again: no `locate.jsp`,
no XMPP, for more than 30 minutes. The way back: its configuration page
(head button at power-on), uploading 1.2.0.1.

So the image starts (the bootloader runs) but the rabbit never reaches the
network. Nothing the emulators run could see it: the native emulator
compiles the C for the host, and flashsim stops at `main()`; the radio's
bring-up (USB host, rt2501) is on neither. Read and ruled out: `DelayMs`
reads a `volatile` counter; `hcd_transfer_wait` re-reads `urb->status`
after an opaque call. The cause is not known.

**-Os does not ship as a whole.** Next, if it is taken up again: -Os file by
file, keeping `src/usb`, `src/net` and `src/hal` at -O1, on the bench rabbit
with the JTAG DCC telemetry (`jtag/listen.sh`) to see where it stops.
