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
| **Size** | 231 330 bytes (`Nab.bin` 115 584) |
| **sha256** | `4f400a581ff897d23bb794bfb2fe7b618a7031267cbea9bb1083f3611c3c1bea` |
| **Build mode** | `lean` (`apply-mods.py`) + `firmware-arm/patches/wpa2-scanned-ciphers.patch` |
| **Built from** | nabgcc `2c05b53f` (wpa2 HEAD) + uplg/nabgcc's `aes128.c` and `eapol_install_msg3_gtk` (MIT, `upstream/UPLG-LICENSE`) |

**What it fixes.** Upstream's `aes128.c` is an empty stub, so on a WPA2/CCMP
network the group key was never decrypted and `eapol_input_msg3()` dropped the
GTK that WPA2 delivers in message 3 — a rabbit deaf to every broadcast and
multicast frame. This build carries a real AES-128, the RFC 3394 unwrap with
its integrity check, and the GTK KDE parser, wired into message 3 and the
group rekey. PR #20 has the full story and the test suite.

**Replaced on 2026-10-23.** The image here now also carries the cold-state
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
| **Build mode** | `minimal` (rescue / rollback: C side only — TweetNaCl + `nab_sig` + the `OPverifySig` opcode, with `linker_keep`, `audio_fifo_data`, `cold_state`, the EAPOL and interpreter fixes, and `--gc-sections`). `boot.0.0.0.13.mtl` is **byte-identical to vanilla wpa2 HEAD** (`2c05b53f`), so the bootloader UI and the `httpflash` upload flow behave exactly like the stock 2006 firmware. No modernized pages, no signature-gated `httpflash`, no mDNS. |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `../keys/signing_pubkey.h`) |
| **sha256** | `337c27d0011c8d4b256d327c34b65c5690761719976a4284c9973b8cf94ce25b` |
| **Built** | 2026-10-23, and it passes `preflight.py` — see below |

**When to use it:** flash this when a build (`lean`/`full`) left the rabbit in a
state where config mode still answers but the modernized UI misbehaves, or when
you simply want back to stock bootloader behavior. It keeps the `verifySig`
opcode in place, so signed OTAs stay possible afterwards. It is **not**
hardware-verified the way `naboot-stable.signed.sim` is — it's the parachute,
built from the same pinned sources. See `../RECOVERY.md`.

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
