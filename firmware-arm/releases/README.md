# Naboot — shipped releases

This folder holds the firmware builds we ship: the hardware-verified **stable**
release the community downloads, plus the **minimal rescue** build to roll back to
when a flash goes wrong. Both are reproducible and signed. Test / dev /
experimental builds stay out of git (see `../.gitignore`).

## `naboot-stable.signed.sim`

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `lean` (modernized config UI + Ed25519-gated OTA + mDNS announcer **and resolver** + `--gc-sections`, with the surgical `.bss`/audio-FIFO fix) |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `8d6111193bce6a7a16689b893efa0d7b30a119fee4867a79bf1f40a705ed4ba8` |

**Verified live (2026-06-21):** boots, loads its bytecode, resolves its server
over multicast mDNS, completes the XMPP handshake, speaks — and a full
**signed OTA was flashed over the air and recovered cleanly** (the rabbit
re-flashed itself with this exact `.sim` and came back operational — **after
a power cycle**: the post-flash reboot leaves the radio asleep and the belly
blinking orange until the rabbit is unplugged and plugged back in. That is
normal and it is the last step of every flash, OTA included).

## `naboot-lean-wpa2.signed.sim` — lean + the WPA2 group-key fix

| | |
|---|---|
| **Size** | 230 418 bytes |
| **sha256** | `446e2728680adde201d356fc95d8cb755c9c4495084aa909418c6b80d94f7c60` |
| **Build mode** | `lean` + `wpa2_gtk` (see `apply-mods.py`) |
| **Built from** | nabgcc `2c05b53f` (wpa2 HEAD) + uplg/nabgcc's `aes128.c` and `eapol_install_msg3_gtk` (MIT, `upstream/UPLG-LICENSE`) |

**What it fixes.** Upstream's `aes128.c` is an empty stub, so on a WPA2/CCMP
network the group key was never decrypted and `eapol_input_msg3()` dropped the
GTK that WPA2 delivers in message 3 — a rabbit deaf to every broadcast and
multicast frame. This build carries a real AES-128, the RFC 3394 unwrap with
its integrity check, and the GTK KDE parser, wired into message 3 and the
group rekey. PR #20 has the full story and the test suite.

**Hardware-validated on 2026-10-21** on a Nabaztag:tag v2 by OTA: sent
12:09:30, `bc.jsp` 12:11:31, operational 12:11:46. It boots, associates and
reaches the server.

**One honest caveat.** On this firmware the bytecode hands the driver the raw
config byte (`IEEE80211_CRYPT_WPA2 = 0x40`) with **no cipher bits**, so the
`switch(encryption & 0x0F)` that selects the CCMP path hits `default` — the
fix is **installed but dormant** until the driver also learns the cipher
suites from the beacon (uplg's `ieee80211.c` change; next step, not this
release). What this release *does* prove is that the code is safe to run and
that the signed OTA path works end to end.

**Flashing it — read step 3.**

1. Config mode (hold the head button while plugging in → belly blue) →
   `192.168.0.1` → *firmware upgrade* → upload this file. Or send it over the
   air from the Terrier.
2. Do not unplug during the ~30 s install. The rabbit then reboots by itself.
3. **It comes up with the first belly light blinking orange. That is normal.
   Unplug it, wait ten seconds, plug it back in.** A flash ends in a software
   reset, which leaves the 2006 radio asleep; only a power cycle wakes it.
   Three "failed" flashes in a row were exactly this — including
   `naboot-stable`, the image that had run since June.

**Running ServerlessNabaztag?** You do not need this image. ServerlessNabaztag
is bytecode on top of RedoXyde/nabgcc's `wpa2` C firmware, which has the same
stub. Replace its `src/net/aes128.c` with uplg/nabgcc's (`wpa23-gtk-r2`) and
wire `eapol_install_msg3_gtk` as `apply-mods.py` does here, rebuild, and keep
your bytecode.

## `naboot-minimal.signed.sim`

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `minimal` (rescue / rollback: C side only — TweetNaCl + `nab_sig` + the `OPverifySig` opcode, with `linker_keep` + `--gc-sections`). `boot.0.0.0.13.mtl` is **byte-identical to vanilla wpa2 HEAD** (`2c05b53f`), so the bootloader UI and the `httpflash` upload flow behave exactly like the stock 2006 firmware. No modernized pages, no signature-gated `httpflash`, no mDNS. |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `449974dd9b42d3fffb80562d9682a26985bf26bc2cc74594568d3316545f3fe6` |

**When to use it:** flash this when a build (`lean`/`full`) left the rabbit in a
state where config mode still answers but the modernized UI misbehaves, or when
you simply want back to stock bootloader behavior. It keeps the `verifySig`
opcode in place, so signed OTAs stay possible afterwards. It is **not**
hardware-verified the way `naboot-stable.signed.sim` is — it's the parachute,
built from the same pinned sources. See `../RECOVERY.md`.

## Flashing it

First flash is from the rabbit's config-mode upload page (hold the head button
on power-up → blue belly → join `Nabaztag-XXXX` → `http://192.168.0.1` →
*firmware upgrade* → upload this file). The simple step-by-step guide lives on
**Le Terrier** (`/naboot`). After this first flash, updates are **OTA** — signed,
over the air, no opening the rabbit again. See `../RECOVERY.md` for the full
flash + recovery procedure and `../BRICK_FORENSICS.md` if anything goes wrong.

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
