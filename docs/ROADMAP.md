# Roadmap

What is done and what comes next.

The long, dated story of every phase — what was tried, what broke, what was
measured — lives in **[`docs/HISTORY.md`](HISTORY.md)**, unabridged. This
is the short version.

**Done — Naboot 1.1.0.0 (2026-11; a beta, emulator only; all of it shipped in 1.2.0.0)**

- **The 2026-11 security review** — fifteen entries fixed, each reproduced
  first; one measured before it is enforced (CCMP replays are counted and
  reported over the JTAG telemetry, refused once a rabbit has shown the
  counter stays at zero). The list, with what is left open and why, is in
  `firmware-arm/docs/SECURITY-FINDINGS-2026-11.md`.
- **Flash reclaimed** — 112 556 of 126 976 bytes, 14 420 free (1.0.0.3 left
  3 556): `firmware-arm/docs/VIOLET-CODEMORT.txt` is the study, measured
  symbol by symbol. **WEP is gone**: a rabbit on a WEP network will not join
  it with this image.
- **A safer flash** — the image is checked before anything is erased, a
  sector that does not take is retried from RAM, and the writer never returns
  to code it has just overwritten. A power cut during the two seconds of the
  write is still a brick; nothing else is.
- **Nothing lost if upstream disappears** — RedoXyde's `nabgcc` and
  `mtl_linux` are vendored in `firmware-arm/vendor/`, the image builds
  offline, and the Python Metal compiler builds this bootloader byte for byte
  like the C++ one.

**Done — Naboot 1.0.0.0 (2026-11-08; every item proven on a real rabbit)**

- **WPA3-SAE** — WPA3-only and WPA2/WPA3 transition networks, on 2006 silicon
  (SAE in software, CCMP still in the radio). Hunting-and-pecking only: an
  access point set to H2E-only needs 1.0.0.2 (below).
- **Adoption key** — one X25519 exchange at adoption; the rabbit shows five
  belly colours, the owner confirms they match the warren's page. Until
  confirmed, the colours show for a minute at every boot.
  [`firmware-arm/docs/ADOPTION-KEY.md`](../firmware-arm/docs/ADOPTION-KEY.md).
- **HMAC-signed bytecode** — a confirmed rabbit refuses bytecode its warren
  did not sign.
- **XMPP over TLS 1.3** (PSK, port 5223) for confirmed rabbits; the PSK is
  derived from the adoption key.
- **Binary OTA** — the update is pushed from the warren's page; the rabbit
  verifies it, installs it and restarts by itself. Nobody at the plug.
- **Crash report** — reset reason and counters in the boot URL, so the warren
  sees what used to need a JTAG read.
- **Self-recovery** — the rabbit notices an access point that forgot it and
  rejoins by itself; a server restart was already survived by the runtime
  bytecode (reconnect, no reboot).
- **Live telemetry over JTAG** without halting the rabbit
  (`firmware-arm/jtag/listen.sh`).
- **The JTAG station in one command** — clone the repo on a Raspberry Pi,
  then `firmware-arm/jtag/station.sh` does setup, check, flash, listen and
  dump. Five jumpers, no probe.
- Also in the image: the modernized config UI, mDNS announcer and resolver,
  the Ed25519 signature gate, and a reproducible build — 118 344 of
  126 976 bytes of flash. Around it: Le Terrier live, the HACS integration,
  the whole-rabbit emulator, and a Metal toolchain in Python whose
  `mtl_comp.py --check` type-checks the real runtime bytecode.

**Done — Naboot 1.2.0.0 (2026-11-13; proven in the emulator; HTTPS streams and sounds on a real rabbit)**

- **The rabbit speaks HTTPS itself.** A certificate session beside the
  warren's TLS: X25519, ChaCha20-Poly1305 or AES-128-GCM, chains checked
  up to roots the warren sends (ECDSA P-256/P-384, RSA, RSA-PSS), a link
  proven once not checked again that boot. Radios, podcasts and any typed
  address (`play`, a webradio, an MP3) are opened by the rabbit, nothing
  relayed; an address that only exists in plain HTTP is marked so.
- **Even the first boot is encrypted.** Before adoption, `kx.jsp` and
  `bc.jsp` go to `terrier.cyberloutre.fr` over HTTPS, anchored on ISRG Root
  X2 built into the firmware, with no fallback to port 80 (a fallback is
  what someone blocking 443 would ask for); other warrens as before. Proven
  in the emulator against a real Caddy: the right chain adopts, a rogue one
  is refused 13 times out of 13 with not one request in the clear.
- **Updates from GitHub** (held back). A rabbit on 1.2.0.0 was to fetch its
  image from this repository's releases over HTTPS. Proven in the emulator,
  it failed on the first real rabbit (2026-11-13): updates go through the
  warren's session again, the path every adopted rabbit has used.
- **The owner can always leave.** An unsigned image (Violet's firmware,
  someone else's) is written only with the head held at power-on and again
  at the write, read on the pin: nobody remote can do it, the owner always
  can, without JTAG.
- 126 704 of 126 976 bytes of flash; `naboot-1.2.0.0-tel`, the same with the
  live JTAG telemetry back for a rabbit that misbehaves, boots in plain HTTP
  to fit (126 872).

**Done — Naboot 1.0.0.3 (2026-11-10; proven in the emulator and on a real rabbit)**

- **Everything the rabbit exchanges travels encrypted.** What a TLS rabbit
  used to fetch or send in the clear now rides its XMPP-over-TLS session,
  with no firmware change, each proven in the emulator and then on a real
  rabbit:
  - what it says, AI answers included (`bytecode/patch_res_in_session.py`):
    a 122 KB answer, played whole, no `/res/` request in the clear;
  - its jingles and choreographies (`bytecode/patch_rsc_in_session.py`);
  - `locate.jsp`, asked on the TLS port like `bc.jsp`
    (`bytecode/patch_locate_tls.py`), falling back to plain HTTP once when
    that port does not answer;
  - the microphone behind "talk to it" (`bytecode/patch_mic_in_session.py`),
    instead of UDP: 23 552 bytes captured from the session. The emulator's
    microphone now records a known pattern
    (`firmware-arm/emulator/stubs/audio.c`) to prove it;
  - firmware updates (`bytecode/patch_ota_in_session.py`): the signed
    1.0.0.3 image came down the session, passed the signature gate and was
    written (`verify_ok=1 flash=1 flash_errors=0` in the next boot's
    report), with no `/ota/` request.

  Only radio and podcasts still streamed in the clear, until 1.2.0.0.
- **The Mir:ror is a tag reader again.** Violet's USB RFID reader (2008),
  read on a real one and plugged into a Raspberry Pi
  ([`mirror/`](../mirror/README.md)): a tag on it does what it does on the
  rabbit's nose — the bound action runs, Home Assistant hears of it — and
  turning it upside down and back are two more tags to bind. On a real
  rabbit: a Nano:ztag on the Mir:ror, the rabbit said its sentence.
- **A rabbit that speaks in its sleep goes back to sleep.** A tag shown
  during quiet hours, or a reminder, made a sleeping rabbit play its sound
  and then stay half awake -- ears up, nose lit, deaf to every later sleep
  or wake command until unplugged (Violet never spoke to a sleeping
  rabbit). `bytecode/patch_sleep_after_program.py` puts it back to sleep;
  the bug and the fix are both reproduced in the emulator.
- **Blanks no longer deafen a rabbit.** Violet's XML parser kept a blank
  ahead of a stanza as the rest of its buffer and dropped the stanza, then
  every one after it: one whitespace keepalive and the rabbit acted on
  nothing until unplugged. `bytecode/patch_xml_leading_blank.py` skips them.
- **A rabbit that comes back on its own.** No more BOSH fallback after three
  failed connections, and a write no longer counts as hearing from the
  server (`bytecode/patch_liveness.py`): with the warren stopped for four
  minutes the rabbit was back over TLS two seconds after it restarted.
- **A JTAG listener that does not stall.** `listen.sh` used to stop after a
  few minutes on `unknown target request: ff`: openocd's DCC decoder chokes
  on the `0xff` words the JTAG bus produces during a reset. The Pi now reads
  the DCC channel itself (`firmware-arm/jtag/dcc-listen.tcl`, scan chain 2),
  keeps the telemetry words and counts the rest; it ran 40 minutes with no
  stall. Stopping `listen.sh` now stops openocd too.
- **WPA3 in constant time.** The SAE password element costs the same
  number of instructions whatever the password: 161 060 360 whether the
  first point is at round 1 or round 12, measured under qemu-arm
  (`firmware-arm/tools/cost/ct.sh`). Installed over the air on a real
  rabbit, which came back over TLS.

**Done — Naboot 1.0.0.2 (2026-11-09; proven in the emulator and on a real rabbit)**

- **SAE hash-to-element** (H2E) whenever the access point offers it — **beta**:
  it has joined one real access point that offers H2E alongside
  hunting-and-pecking, but never yet one set to H2E-only. If yours is
  H2E-only, please report whether the rabbit joins. H2E is the
  only derivation an H2E-only access point accepts, and the one without the
  Dragonblood timing leak; hunting-and-pecking stays for access points
  without it. The password token is derived once per network and kept, so
  a reassociation costs one scalar multiplication.
- **An entropy pool** behind every SAE and adoption secret, and a WPA3
  network that stays WPA3 (no silent downgrade to WPA2).
- **The fixes of a security audit** — no plaintext data frame once the link
  is protected, no new pairwise key from a forged message 1, a bounded MIC
  length, no group-key reinstall on a retransmit, no pairwise TKIP.
- **The boot over TLS** — a rabbit whose adoption key is confirmed asks for
  its bytecode inside a TLS 1.3 session with the warren, instead of in the
  clear; one attempt per boot, then plain, since the bytecode carries its
  HMAC anyway.

**Next**

- **Per-rabbit wake word** compiled into the bytecode — it is recorded on the
  warren today, but not yet minted into each rabbit's own bytecode.
- **Close the plain XMPP port** once every rabbit has migrated to TLS.

**Later**

- **Play a sound while it is still arriving in the session** — since
  2026-11-10 a TLS rabbit's sounds come down its encrypted XMPP session
  instead of plain HTTP, but playback starts once the whole file is in: about
  9 s for a 120 KB answer on a real rabbit, where HTTP started after 80 KB.
  Feeding the player as the records arrive would close the gap.
- **Reclaim flash** — 272 bytes are spare in 1.2.0.0; the measured savings
  still to take are in the history (Phase 8).
