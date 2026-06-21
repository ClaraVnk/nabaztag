# Naboot — stable release

This folder holds the **one** firmware build we vouch for: hardware-verified,
reproducible, signed. It's what the community downloads and our emergency backup.
Test / dev / experimental builds stay out of git (see `../.gitignore`).

## `naboot-stable.signed.sim`

| | |
|---|---|
| **Hardware** | Nabaztag:tag v2 (2006 — mic + RFID) |
| **Build mode** | `lean` (modernized config UI + Ed25519-gated OTA + mDNS announcer **and resolver** + `--gc-sections`, with the surgical `.bss`/audio-FIFO fix) |
| **Firmware version** | `0.0.0.13` |
| **Signature** | Ed25519, by the Naboot project key (pubkey embedded in the firmware: `keys/signing_pubkey.h`) |
| **sha256** | `8d6111193bce6a7a16689b893efa0d7b30a119fee4867a79bf1f40a705ed4ba8` |

**Verified live (2026-06-21):** boots, loads its bytecode, resolves its server
over multicast mDNS, completes the XMPP handshake, speaks — and a full
**signed OTA was flashed over the air and recovered cleanly** (the rabbit
re-flashed itself with this exact `.sim` and came back operational).

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

The build is deterministic: a fresh rebuild is byte-identical to this file
(same sha256). Other modes (`minimal` rescue, `full`, `max`, `phase8-rom`) build
the same way — only this verified `lean` build is committed.
