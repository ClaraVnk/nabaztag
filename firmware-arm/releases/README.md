# Naboot — shipped releases

> **Note (2026-11-08):** every image here was rebuilt. Two defects made signed
> flashing impossible until tonight: the signature covered the obfuscated
> `.sim` payload while the rabbit verifies the plain image, and — the blocking
> one — `crypto_sign_open` was called with one buffer as both input and output,
> so `verifySig` never once returned 1. Both are fixed, and these builds carry
> the fix plus the radio repairs of the same evening. A rabbit running an
> older build cannot install any of this over the air; it needs the recovery
> bytecode the warren now serves, or JTAG.

> **Flashing is done at your own risk** — no warranty, no liability. A failed
> flash can leave the rabbit unable to re-enter config mode, recoverable only
> over JTAG. See [`../docs/RECOVERY.md`](../docs/RECOVERY.md). Flashing is optional: a
> stock rabbit works fine with the Home Assistant add-on.

This folder holds the firmware we ship: the hardware-verified release the
community downloads (1.2.0.0). Every earlier release, and the old `minimal`
rescue build, is in `archive/`. Each is reproducible and signed. Test / dev /
experimental builds stay out of git (see `../.gitignore`).

## `naboot-1.2.0.0.signed.sim` — the release

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `naboot-1.2.0.0` (`firmware-arm/apply-mods.py`): 1.0.0.3, plus HTTPS with the certificate checked (`tls_certs`), the first boot over HTTPS, and the owner's way out (`owner_unlock`) |
| **Firmware version** | `1.2.0.0` (the boot URL says `v=1.2.0.0`) |
| **Size** | 126 696 of 126 976 bytes |
| **Signature** | Ed25519, by the Naboot project key (`../keys/signing_pubkey.h`) |
| **sha256** | `fb26f171ba61510e85aa…` (the `.sim`); the plain image, `naboot-1.2.0.0.bin` on the GitHub release, is `ea1187fed55f…` |

What it brings:

- **The first boot over HTTPS.** Before the adoption key exists, the rabbit
  fetches its bytecode on port 443 and checks the Terrier's chain against one
  root built into the firmware (ISRG Root X2), no fallback to plain HTTP.
  Certificates are refused if they expired before the build date.
- **Updates from GitHub.** A Terrier can send the rabbit to this release's
  `.bin`: the rabbit downloads it over HTTPS itself and checks our signature,
  which comes with the order, before it writes a byte.
- **Sounds and streams from any `https://` URL**, the certificate checked.
- **The owner's way out.** Hold the head button while the rabbit starts
  installing an image, and it accepts one we did not sign: you can always
  leave Naboot for another firmware. Without the button, only our signature.

**In the emulator:** the HTTPS first boot (and the real Terrier's four-certificate
chain, checked by the same C code on the host), the owner's way out with and
without the button, the GitHub update, and the upgrade to this image from the
stock Violet firmware (config page), from a stock rabbit adopted on the
Terrier (over the air), and from 1.0.0.3 (inside the session).

**On a rabbit (2026-11-13):** installed over the air on 1.0.0.3 at 15:07; it
plays `https://` streams and fetches its runtime over TLS.

`naboot-1.0.0.3` is archived below: flash it back if this one gives you trouble.

## `archive/naboot-1.0.0.3.signed.sim` — archived (the previous release)

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `naboot-1.0.0.3` (`firmware-arm/apply-mods.py`): 1.0.0.2, with the WPA3 password exchange (SAE hunting-and-pecking) in constant time — the same work whatever the password, so its timing says nothing about it |
| **Firmware version** | `1.0.0.3` (the boot URL says `v=1.0.0.3`) |
| **Size** | 123 564 of 126 976 bytes |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `789be0850ede4ce4444813c1563d984c4dffc85db9d4f13938ebb5c4708b0efc` |

**Measured before it was flashed** (`../tools/cost/ct.sh`, qemu-arm, the
firmware's own sources and flags): the password element costs 161 060 360
instructions whether the first point is found at round 1 or at round 12
(167 150 792 against 167 065 465 before), and decompressing a valid or an
invalid x differs by one instruction, outside the crypto. Instructions, not
cycles: the ARM7TDMI multiplier still finishes early on small operands.

**In the emulator:** every Wi-Fi scenario (35 of 35), and the whole rabbit
booting three times against a Terrier image — pending key, confirmed key with
the warren vanishing under the TLS session (back in 49 s later), then
`bc.jsp` over TLS.

**Installed over the air (2026-11-10 16:56):** this exact file, sent by the
warren to a rabbit running 1.0.0.2. It restarted by itself, announced
`v=1.0.0.3` with `r=3.0.3.3.3.0`, fetched its bytecode inside TLS 1.3 and
opened its XMPP session over TLS 11 s later.

**H2E is still beta**, and everything said under 1.0.0.2 below about WPA3 →
WPA2 holds.

`naboot-1.0.0.2` below stays as the previous release: flash it back if this
one gives you trouble.

## `archive/naboot-1.1.0.0-beta.signed.sim` — archived (a beta, never the release)

| | |
|---|---|
| **Status** | Archived 2026-11-13. A beta that only Nabazprout ran; never offered to everyone. Its fixes are all in 1.2.0.0. |
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `naboot-1.1.0.0` (`firmware-arm/apply-mods.py`): 1.0.0.3 with the fixes of the 2026-11 security review, the safer flash writer and 15 KB of dead code removed. **No WEP.** |
| **Firmware version** | `1.1.0.0` |
| **Build mark** | `z=2611121004` |
| **Size** | 112 572 of 126 976 bytes (14 404 free) |
| **Signature** | Ed25519, by the Naboot project key (`../keys/signing_pubkey.h`) |
| **sha256** | `1f1132a063abd87cb8daff25c99a20ba7cee25c4b295a5d3d684051152aafe5c` |

## `archive/naboot-1.0.0.2.signed.sim` — archived (the previous release)

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `naboot-1.0.0.2` (`firmware-arm/apply-mods.py`): everything in 1.0.0.0, plus an entropy pool behind every SAE, adoption and TLS secret; no plaintext data on a protected link; the four-way handshake hardened (no new pairwise key from a forged message 1, bounded MIC length, no group-key reinstall, no pairwise TKIP); a WPA3 network stays WPA3 (no downgrade to WPA2); SAE hash-to-element (H2E, **beta**); and `bc.jsp` over TLS 1.3 for a rabbit whose adoption key is confirmed |
| **Firmware version** | `1.0.0.2` (the boot URL says `v=1.0.0.2`) |
| **Size** | 123 420 of 126 976 bytes |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `c03b6e7806fad5f18f20f278644a731c2ae48ce466b224fa6c1a213cac2ad73b` |

**Installed over the air (2026-11-09 15:05):** this exact file, sent by the
warren to a rabbit running 1.0.0.1. It restarted by itself and, on that very
first boot, fetched its bytecode inside a TLS 1.3 session on port 5223
(`r=3.0.2.2.2.0`), accepted it, and opened its XMPP session over TLS 18 s
later. Earlier the same day the 1.0.0.1 part of it (with H2E) associated over
WPA3 on a UniFi access point that offers H2E: the access point answered with
status 126, hash-to-element.

**H2E is beta:** it has joined an access point that offers H2E next to
hunting-and-pecking, never yet one set to H2E-only. If yours is, please say
whether the rabbit joins.

**Behaviour to know:** once a rabbit has joined a network over WPA3, it refuses
that network over WPA2. If WPA3 is turned off there, it stays orange until its
Wi-Fi settings are saved again in config mode (see the main README,
*Troubleshooting*).

**Which way in:** as for 1.0.0.0 below — the warren's update button from
1.0.0.0, `naboot-stable` or `naboot-minimal` of 2026-11-08, the config page
from a stock rabbit; an older Naboot needs a replug first, for the recovery
bytecode.

`naboot-1.0.0.0` below is the release before it.

## `archive/naboot-1.0.0.0.signed.sim` — archived

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `naboot-1.0.0.0` (`firmware-arm/apply-mods.py`): WPA3-SAE (WPA3-only and WPA2/WPA3 transition; H2E-only access points are not supported), the adoption key and its five belly colours, HMAC-signed bytecode, XMPP over TLS 1.3 PSK, binary OTA, crash report in the boot URL, self-recovery when the access point forgets the rabbit, live JTAG telemetry |
| **Firmware version** | `1.0.0.0` (the boot URL says `v=1.0.0.0`) |
| **Size** | 118 344 of 126 976 bytes |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `df654a2868257c32b34982cbf411381d6af55ee56d7fbfb0e9e07445a0c55125` |

**Installed over the air (2026-11-08 23:47):** this exact file, sent by the
warren as bytes to a rabbit running the `lean-wpa3-tls` build. It verified,
wrote, restarted by itself — no unplugging — announced `v=1.0.0.0` with
`r=3.0.1.1.1.0` (one signature check, passed; one flash write, no error),
and opened its XMPP session over TLS 1.3 eleven seconds later.

**From the config page.** Measured in the whole-rabbit emulator
(`../emulator/scenarios/vm/config-upload.txt`): uploaded through the config
page of `naboot-stable` and of the stock bootloader (0.0.0.10 and 0.0.0.13
bytecode), it reaches `flashFirmware` with the heap at most at 66 %. The
config page holds the image about three times, where the old hex OTA path
held it six times and could not take an image this size.

**Which way in.**
- A stock rabbit, or one on `naboot-stable`/`naboot-minimal` of 2026-11-08:
  the config page (blue mode), or the warren's update button.
- A rabbit on a Naboot build from before 2026-11-08: its signature check
  never passes, on the config page too. Unplug and replug it — the warren
  then serves it the recovery bytecode — and use the update button.

1.1.0.0 (a beta), 1.0.0.2, 1.0.0.0, `naboot-stable` (0.0.0.13) and `naboot-lean-wpa2` (the same
file as stable under another name) are **archived** (2026-11-13) in
[`archive/`](archive/): no longer offered, kept so a rabbit can still be
rolled back to them. Every new rabbit goes to 1.0.0.3.

**The rule:** this folder holds the release, nothing else. When a release
ships, the one before it moves to `archive/` in the same change.

`naboot-minimal` is archived too (2026-11-13): it never ran on a rabbit, it
predates the fixes of 2026-11-08 (a rabbit on it cannot take an update from
the Terrier), and since 1.0.0.0 the setup page takes any signed image, so a
rabbit that misbehaves is flashed again with the release itself. A rabbit
whose bootloader no longer answers is a JTAG case, minimal or not.

## `archive/naboot-stable.signed.sim` — archived

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `lean` (modernized config UI + Ed25519-gated OTA + mDNS announcer **and resolver** + `--gc-sections`, with the surgical `.bss`/audio-FIFO fix) |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `84db2654df7efd17e4620183f0a761e19dfe96416a4b819f34c225601d63a3fb` |

**Installed over the air (2026-11-08 14:09):** this exact file was sent by
the warren to a rabbit running the previous lean build, downloaded in a
second, verified, written, and the rabbit came back 54 s later announcing
`&z=2611081305&s=1` — the mark of *this* image, not of the one that had been
in flash. The warren logged `boot URL changed across the flash … confirmed
running`. First OTA to install in five months.

**Which images it can be installed from.** Only from a build dated
2026-11-08 or later. Every earlier `lean`/`full` resets inside `verifySig`:
the Ed25519 check of a 107 KB image is ~248 million instructions, one
opcode, and the 2.09 s watchdog is fed only between opcodes. Earlier
images also refuse signed uploads from the config page (the
`crypto_sign_open` alias bug). A rabbit on one of those leaves it through
the recovery bytecode the warren serves it, or over JTAG — see
`../docs/RECOVERY.md`. Three firmware fixes make this build installable *from*:
`verify_wdt` (the signature check feeds the watchdog), the `OPverifySig`
operands re-read after the allocation that can move them, and
`sign_open_alias`.

**Correction to what this section used to claim.** It said a signed OTA had
been flashed over the air and recovered cleanly, on 2026-06-21. That was not
true, and could not have been: `verifySig` has never returned 1 on any
rabbit, because the firmware's wrapper passed one buffer as both the input and
the output of `crypto_sign_open` (`b8f6dd1`). Every signed image was refused,
in silence, by every rabbit with a signature gate — which is why five months
of flashes reported success and wrote nothing. The June image is gone from
this folder for that reason: a rabbit flashed with it could never be updated
over the air again. `minimal` is the parachute.

If the first belly light is still orange a minute after it reboots, unplug it, wait ten seconds, plug it back in. Since the 2026-11-08 builds this is the exception: an over-the-air install was watched end to end that day and the rabbit came back on its own, 54 s after the download, with nobody at the plug. Earlier builds leave the 2006 radio asleep after the post-flash reset and need the mains every time.

## `archive/naboot-lean-wpa2.signed.sim` — archived (lean + the WPA2 group-key fix)

> Byte-identical to `naboot-stable.signed.sim` as of 2026-11-08: the WPA2
> group-key fix is in `lean`, so stable *is* this build. Two names, one
> file, kept so existing links still resolve.

| | |
|---|---|
| **Size** | 215 970 bytes (`Nab.bin` 107 904) |
| **sha256** | `84db2654df7efd17e4620183f0a761e19dfe96416a4b819f34c225601d63a3fb` |
| **Build mode** | `lean` (`apply-mods.py`) + `firmware-arm/patches/wpa2-scanned-ciphers.patch` |
| **Built from** | nabgcc `2c05b53f` (wpa2 HEAD) + uplg/nabgcc's `aes128.c` and `eapol_install_msg3_gtk` (MIT, `upstream/UPLG-LICENSE`) |

**What it fixes.** Upstream's `aes128.c` is an empty stub, so on a WPA2/CCMP
network the group key was never decrypted and `eapol_input_msg3()` dropped the
GTK that WPA2 delivers in message 3 — a rabbit deaf to every broadcast and
multicast frame. This build carries a real AES-128, the RFC 3394 unwrap with
its integrity check, and the GTK KDE parser, wired into message 3 and the
group rekey. PR #20 has the full story and the test suite.

**Replaced on 2026-10-27.** This is the build that stops the internal RAM
being laid out by accident, and it closes pacifox's #25 and #27.

Every `.bss.*` is zeroed at boot now. Until this image, `-fdata-sections` gave
each variable its own section, the linker script collected only `*(.bss)`, and
85 sections sat past `__bss_end__` where the startup loop never reached them:
`pmk`, `ptk`, `gtk`, `anonce`, `snonce`, `replay_counter`, the scan cipher
cache. They booted holding whatever the SRAM held at power-on, so whether an
association succeeded was partly a dice roll and every replug re-rolled it.
Collecting them needs somewhere to put 8 KB of pure scratch, so `audioFifoPlay`
and vlog's `buffer_temp` move to a NOLOAD `.extbss` in external RAM, below the
USB pool at `0xD0008000`. Ported from RedoXyde/nabgcc `6303737` (Léonard,
uplg) — his linker script, his section attributes, his save and restore of the
location counter around the ExtRAM section.

The user stack goes from 1 916 to **10 104 bytes** of headroom, which was
finding 1; the newlib heap no longer starts on `counter_timer`, which was
finding 2. Seven radio failures go with it (#25 findings 3, 4 and 5): the 8051
start request is checked, a failed enumeration returns NULL instead of being
reported as success, reception is re-armed after a failed allocation, a USB
error no longer arrives looking like a short frame, the ComRAM leak on every
failed transmit is closed, its busy-loop is bounded, and a transient receive
error no longer kills the radio until the next power cycle.

**Hardware-validated on 2026-10-27** by OTA on a Nabaztag:tag v2: sent
09:14:24, downloaded 245 ms later, and after the power cycle `bc.jsp`
09:21:42, `locate.jsp` 09:21:57, operational 09:22:02 — then a spoken
sentence fetched at 09:22:22 **and heard**, and both MIDI jingles after it,
also heard.

Those last two clauses are the whole reason this image took two attempts. The
first flash, on 2026-10-26, was called mute on the strength of one 119-byte
MIDI jingle that nobody heard — a sound of well under a second, on a build
that had moved the audio FIFO to external RAM, which made the explanation feel
obvious. It was not evidence. Rolled back, then re-flashed and tested properly
against a control: the same sentence through the same path on the previous
image first, so that silence and "I heard it" could be told apart. Both the
MP3 path and the MIDI decoder — separate decoders in the VS1003, and the MIDI
one is what every owner hears at connect — were checked by ear before this
image was offered to anyone else.

The image it replaces, `2f35637bce320d23…`, is kept on the warren as the
rollback.

**Previously replaced on 2026-10-25.** It added the three waits that used to feed the
watchdog while they hung: the two VS1003 DREQ waits and the RT2501 bring-up
loops (#25, #27). A wedged codec or a radio that never reports ready used to
stop the rabbit with nothing left to rescue it, because the waits kept
clearing the watchdog that exists for exactly that.

**Hardware-validated on 2026-10-25** by OTA on a Nabaztag:tag v2: sent
21:42:45, downloaded 384 ms later, and after the power cycle `bc.jsp`
21:45:06, `locate.jsp` 21:45:20, operational — then a sound fetched and
played at 21:49.

Worth being exact about what that validates, since the fix itself cannot be
staged: both failures it addresses need a wedged codec or a radio that will
not come up. So the flash shows that nothing broke — which, given the DREQ
waits sit on the audio path, is the thing most at risk. The fix is only
visible the day one of those happens and the rabbit comes back instead of
staying dark. It is one you hope never to see work.

The image it replaces, `4f400a581ff897d2…`, is kept on the warren as the
rollback.

**Previously replaced on 2026-10-23.** That image carries the cold-state
guards (#30), the EAPOL group-key acknowledgement (#37), the group cipher and
the pairwise rekey (#44), the interpreter's store bounds-check (#47) and the
audio half-duplex fix (#52). It passes all fifteen `preflight.py` checks.

**Hardware-validated on 2026-10-23** on a Nabaztag:tag v2 by OTA: sent
23:10:38, downloaded 384 ms later, and after the power cycle `bc.jsp`
23:12:48, `locate.jsp` 23:13:04, operational 23:13:08 — then a sound pushed
from the warren, fetched, and **heard**.

That last clause is there because the build before it was not. An earlier
attempt included an amplifier change that plays a sound perfectly on the wire
and produces silence in the room; it took a flash to find out, and it is why
"operational" is no longer the bar for calling one of these validated. See
`audio_ampli` in `apply-mods.py`.

The 2026-10-21 image it replaces was `c8148bf9c5560e59…`, validated the same
way minus the sound: sent 13:22:38, operational 13:25:21.

**And the fix actually runs.** An earlier build carried only the group-key
work, which was dormant: the bytecode hands the driver the raw config byte
(`IEEE80211_CRYPT_WPA2 = 0x40`) with **no cipher bits**, so the
`switch(encryption & 0x0F)` selecting the CCMP path hit `default` — and worse,
message 2 installed the pairwise key as **TKIP** on a CCMP network, which
works only because access points tolerate it. `firmware-arm/patches/wpa2-scanned-ciphers.patch`
(uplg's `ieee80211.c` change, verbatim) remembers the cipher suites each AP
advertises in its beacon and fills them in at association time. This image
therefore associates with a correct RSN IE, installs the pairwise key as AES,
and exercises the RFC 3394 group-key unwrap.

**Flashing it — read step 3.**

1. Config mode (hold the head button while plugging in → belly blue) →
   `192.168.0.1` → *firmware upgrade* → upload this file. Or send it over the
   air from the Terrier.
2. Do not unplug during the ~30 s install. The rabbit then reboots by itself.
3. **It comes up with the first belly light blinking orange. That is normal.
   If it is still orange a minute later, unplug it, wait ten seconds, plug it
   back in.** Builds before 2026-11-08 need that every time (their post-flash
   reset leaves the 2006 radio asleep); from the 2026-11-08 builds on, the
   rabbit comes back by itself — measured on an OTA, 54 s download to boot URL.
   Three "failed" flashes in a row were exactly this — including
   `naboot-stable`, the image that had run since June.

**Running ServerlessNabaztag?** You do not need this image. ServerlessNabaztag
is bytecode on top of RedoXyde/nabgcc's `wpa2` C firmware, which has the same
stub. Replace its `src/net/aes128.c` with uplg/nabgcc's (`wpa23-gtk-r2`) and
wire `eapol_install_msg3_gtk` as `apply-mods.py` does here, rebuild, and keep
your bytecode.

## `archive/naboot-minimal.signed.sim` — archived

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `minimal` (rescue / rollback: C side only — TweetNaCl + `nab_sig` + the `OPverifySig` opcode, with `linker_keep`, `audio_fifo_data`, `cold_state`, the EAPOL and interpreter fixes, and `--gc-sections`). `boot.0.0.0.13.mtl` is **byte-identical to vanilla wpa2 HEAD** (`2c05b53f`), so the bootloader UI and the `httpflash` upload flow behave exactly like the stock 2006 firmware. No modernized pages, no signature-gated `httpflash`, no mDNS. |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `7d0f9be60c40ae0f6a09a532e3d50f982b8b85ee09ad9397beec3756a7c6e101` |
| **Built** | 2026-11-08, and it passes `preflight.py` — see below |

**When to use it:** flash this when a build (`lean`/`full`) left the rabbit in a
state where config mode still answers but the modernized UI misbehaves, or when
you simply want back to stock bootloader behavior. It keeps the `verifySig`
opcode in place, so signed OTAs stay possible afterwards. It is **not**
hardware-verified the way `naboot-stable.signed.sim` is — it's the parachute,
built from the same pinned sources. See `../docs/RECOVERY.md`.

**Replaced on 2026-10-23, and why it matters.** The file shipped here until
that date was built on 2026-10-20 from a step list that read

```
vbc,vinterp,stdlib,strip_tweetnacl,makefile,linker_keep,gc_sections
```

`linker_keep` without `audio_fifo_data`, which is the combination that leaves
`play_w` and `play_r` uninitialised in a `.bss.*` section the zero-loop does
not reach. One of them read as garbage is an out-of-bounds load into the audio
FIFO, a Data Abort, and a dead-loop in the vector — the 2026-06-20 brick,
whose trigger was the connection jingle. So the parachute carried the fault it
exists to rescue from. pacifox found it by running `preflight.py` before
flashing anything (#45); nobody flashed one.

The file here now passes every `preflight.py` check. Being a parachute, it is
still a build nobody has put on hardware — but between two unflashed images,
this is the one that is not known to be faulty.

## Flashing it

First flash is from the rabbit's config-mode upload page (hold the head button
on power-up → blue belly → join `Nabaztag-XXXX` → `http://192.168.0.1` →
*firmware upgrade* → upload this file). The simple step-by-step guide lives on
**Le Terrier** (`/naboot`). After this first flash, updates are **OTA** — signed,
over the air, no opening the rabbit again. See `../docs/RECOVERY.md` for the full
flash + recovery procedure and `../docs/BRICK_FORENSICS.md` if anything goes wrong.

## Reproducing it

```sh
# on the dev VM (NEVER build on the HA host) — signing happens locally on the Mac
./build.sh --mode lean --remote <user@dev-vm> --runtime podman
```

```sh
# the minimal rescue build, same way
./build.sh --mode minimal --remote <user@dev-vm> --runtime podman
# → bin/minimal/firmware0.0.0.13.signed.sim, copied here as naboot-minimal.signed.sim
```

The build is deterministic: a fresh rebuild is byte-identical to these files
(same sha256). Only these two builds — the verified `lean` stable and the
`minimal` rescue — are committed; the other modes (`full`, `max`, `mdns-only`,
`phase8-rom`) build the same way but stay under the ignored `bin/`.
