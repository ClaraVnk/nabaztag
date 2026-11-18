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

`firmware-arm/tools/cost/run.sh` (qemu-arm, executed instructions, the firmware's crypto
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
2. **The configuration pages**: 7 039 bytes of text. Moving them to C
   frees nothing (see below). Served gzipped, the stylesheet and the three
   fixed pages give back 2 232 bytes (`gzip_pages`), the setup page 2 696
   more with its values in /v.js (`gzip_setup`); both measured below.
3. **Radio code for chips the rabbit does not have** (`rf2528_only`):
   1 192 bytes. Every rabbit that has reported says RF2528 (30 of 40, 2026-11-18). The
   warren must send it only to a rabbit that has reported RfIcType 2.
4. **Unreachable bootloader functions** (`prune_unreachable`): 472 bytes.
5. **TLS certificate checking** (X.509, RSA-PSS, ECDSA): several KB. A pinned
   server key would do the same job for one server; a design decision, not a
   size tweak.
6. **One signature scheme** (ECDSA P-256 is already there for TLS and SAE):
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
  `firmware-arm/tools/cost/ct.sh` (SAE's hunting-and-pecking, the WPA3 password path)
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

**-Os does not ship as a whole.**

## Three more tries, and the end of -Os (2026-11-17)

`firmware-arm/tools/os-diff.sh` compares two builds' assembly listings file by file
(`firmware-arm/tools/os-diff.py`: memcpy/memset calls, stores and loads by width).

1. **`naboot-1.2.0.2-safe`**: -Os without store merging, strict aliasing or
   loops turned into memset/memcpy (`opt_size_safe`). 12 016 bytes spare;
   the byte stores of the association path are back (ieee80211.c `strb`
   326 → 319, against 187 at plain -Os). Same failure on Nabazprout. A JTAG
   snapshot (`firmware-arm/jtag/snapshot.py` with that build's ELF) read the cause: the
   main loop runs, but the Wi-Fi dongle never enumerates (`rt2501_dev`,
   `usb_devmap` 0) and the VM never starts.
2. **`naboot-1.2.0.2-usb`**: -Os everywhere but `src/usb/`, which stays at
   -O1 (`opt_size_usb_o1`; its listings identical to 1.2.0.1's). 10 600 bytes
   spare.
3. **`naboot-1.2.0.2-usb-tel`**: the same with `dcc_telemetry` (9 636 bytes
   spare). Live over `listen.sh`: the dongle is up (`K1`), SAE passes (`W2`
   → `W3`), then association falls back to idle (`W0`), twice. Telemetry
   lines came every 40 to 80 s, against 7 to 17 s at -O1. Twenty JTAG
   samples found no -Os function eating the time: 7 in `rt2501_receive`
   (-O1 code), 8 in the main loop and `play_check`, the rest scattered.

Each -Os image passed flashsim and failed on the rabbit with a different
symptom the emulators cannot see. **-Os is stopped.** The flash is to be
won by moving stable pieces of the bytecode to C, one at a time, each tried
on the rabbit (`phase8-rom` measures it).

## The pages in C: no flash either (2026-11-17)

`--mode naboot-1.2.0.1-rom` is 1.2.0.1 with `pages_to_rom`: the four
configuration pages and their stylesheet (7 039 bytes) leave the bytecode
for C constants, served by a new `pageRender` opcode that fills the fields.
The image grew by 196 bytes (127 148, 172 over the flash). Text weighs the
same in the bytecode and in C, both live in the flash, and the renderer is
new code. What moves is RAM: the VM no longer copies the pages at boot.

Rewriting bytecode logic in C will not win flash either: the Metal bytecode
is denser than ARM code for the same work. **Moving to C is closed as a
flash lever.** What is left is in the list above: gzipped pages, unused
radio tables, a pinned TLS key, one signature scheme.

Gzip sizes of the pages, measured on the 1.2.0.1 sources (`gzip -9n`):

| Part | Plain | Gzipped |
|---|---|---|
| setup page (15 fields) | 2 254 | 922 |
| saved | 664 | 430 |
| update | 752 | 488 |
| error | 592 | 402 |
| stylesheet | 2 777 | 1 017 |

## Gzipped pages: 2 232 bytes back (2026-11-18)

`--mode naboot-1.2.0.1-gz` is 1.2.0.1 with `gzip_pages`: the stylesheet
moves to `/s.css`, and it and the three fixed pages are stored as their gzip
bytes behind a ready header (`Content-Encoding: gzip`), which the server
sends as it is. The setup page keeps its fields and stays plain, the
stylesheet linked instead of inlined. 4 844 bytes of text become 2 354.

The image: 124 720 bytes, 2 256 spare, against 126 952 for 1.2.0.1
(2 232 bytes back, net of the routing code). In the emulator
(`firmware-arm/emulator/tools/config-pages.sh`), a phone asking for every page gets
each one gzipped behind the right Content-Type, and each inflates to the
page 1.2.0.1 serves; 1.2.0.1 itself, as the control, fails every gzip check.
What the emulator does not show: a real browser fetching the page and its
stylesheet on two connections at once. Nabazprout confirmed it on
2026-11-18 (`-gz-tel`, installed by OTA): setup, upgrade and saved pages
styled in a phone's browser, and loading faster than before.

## The setup page gzipped, dead code, one radio chip (2026-11-18)

`gzip_setup`: the setup page is stored empty and gzipped like the others
(2 674 bytes -> 1 087); a script at its end loads `/v.js`, which the
rabbit writes: `v([mac, firmware, ssid, server, ip, mask, gateway, dns,
proxy ip, proxy port, dhcp, proxy, crypt], [scan])`, strings escaping `\`
and `"`. The template, `pagefill` and the `<option>` builders go with it.
Without JavaScript the fields come up empty and can still be typed.

`prune_unreachable`: walks the bootloader from `main` and `loop` and deletes
every function nothing reaches (10 today, three of them orphaned by
gzip_setup); a second pass finds none.

| Image | Bytes | Spare |
|---|---|---|
| `-gz` (gzip_pages) | 124 720 | 2 256 |
| + gzip_setup | 122 024 | 4 952 |
| + prune_unreachable | 121 552 | 5 424 |
| `-gz-tel` with both | 122 564 | 4 412 |
| `-gzrf` (+ rf2528_only) | 120 360 | 6 616 |

In the emulator (`config-pages.sh`) every page comes gzipped and `/v.js`
gives the 13 values and the scanned network; yesterday's `-gz-tel`, as the
control, fails both v.js checks. The page script, run in node against a
sample `v.js` (quotes and backslashes in an SSID, trailing commas), fills
every field, the radio and the scan list.

`rf2528_only` (`--mode naboot-1.2.0.1-gzrf`): the RF5226 and RF5225/RF2527
tables, antenna and channel cases go. Violet's own comment marks the RF2528
case "notre carte". A rabbit with another chip would boot and tune no
channel: no Wi-Fi, no OTA back. The emulator stops above `rt2501_tx()` and
does not run this code; only the test rabbit can.

On Nabazprout (2026-11-18, `-gzrf-tel`, all three steps, sent in session):
the first warm boot after the flash stayed scanning (W1, SAE cache at 8)
until the rabbit was unplugged; then it joined in one SAE exchange and the
warren confirmed the image at 13:42. Wi-Fi works on the RF2528-only driver,
and the setup page shows every field, the radio and the scan list filled
from /v.js in a phone's browser. The stuck first boot is not explained
yet: the next OTA from this branch is to be watched over DCC.

The first fix image cached every page for an hour: a phone that had
already seen "Settings saved" showed it again from its cache, the save
never reached the rabbit and it stayed in setup mode, blue. Only `s.css`
is cached now; `config-pages.sh` saves with `z=1` and expects RUN. With
that image (sent in session at 14:01) the rabbit came back on its own in
72 s, no stuck boot. At 17:49 a save from setup mode left setup mode on
its own (DCC: A0 B0 → A2 B1) and the rabbit was back in the warren.

## Naboot 1.2.0.3 with the review's fixes (2026-11-18)

The 2026-11-18 review's steps (`SECURITY-FINDINGS-2026-11.md`, last
section) in `naboot-1.2.0.3` and `-tel`, built unsigned on the dev VM with
`BUILD_MARK=2611182200 BOOT_FLOOR=202611182200`:

| Image | Bytes | Spare |
|---|---|---|
| `naboot-1.2.0.3`, the base branch (gzip, factory_server) | 121 656 | 5 320 |
| `naboot-1.2.0.3` with the review | 121 472 | 5 504 |
| `naboot-1.2.0.3-tel` with the review | 122 484 | 4 492 |
| `naboot-1.2.0.3-oscrypto` (+ opt_size_crypto, measurement) | 116 832 (-4 640) | 10 144 |
| `naboot-1.2.0.3-chacha` (+ tls_chacha_only, measurement) | 120 536 (-936) | 6 440 |

What the review's steps add and take: C for the bounds and the overflow
alert (`ie_walk_bounds`, `vm_strcmp_bound`, `vm_seed_hidden`,
`tls_in_overflow`, S-3), C back from the probation (`ccmp_no_probation`),
bytecode for `config_presence` (`needhead`, a page 35 bytes longer gzipped)
and bytecode back from `boot_strip_wav` and `boot_strip_login`: the
bootloader compiles to 21,760 bytes against 22,105 (`../tools/mtl_comp.py` on
the staged `boot.0.0.0.13.mtl`, `../tools/mods-matrix.py --keep`).

**Two levers measured, shipped in no release mode.** `opt_size_crypto`
compiles the pure-computation files (`CRYPTO_OBJS` in apply-mods.py: P-256,
SAE, SHA-256, CMAC, AES, the key exchange, TLS, the PKI, SHA-384,
ChaCha20-Poly1305, TweetNaCl, nab_sig, hash.c) at `-Os` and leaves the
driver, the VM and the radio at `-O1`; `-Os` for the whole image never
reached the network on the test rabbit three times, each time in code these
files do not touch, and the crypto's cost at `-Os` is within 4 % (above).
`tls_chacha_only` offers TLS_CHACHA20_POLY1305_SHA256 alone in both
ClientHellos and compiles AES-GCM and its bit-serial GHASH out
(`NAB_TLS_CHACHA_ONLY`): about six times cheaper per record on this
processor, proven against OpenSSL for the PSK session (`make
tls-interop-chacha`). Either is one Nabazprout flash away, `tools/cost`
first; neither goes into a release without it.

The CRLF sweep (C-1) is byte-neutral: the `Nab.bin` of `naboot-1.2.0.3`
built before and after it, same marks, is identical.
