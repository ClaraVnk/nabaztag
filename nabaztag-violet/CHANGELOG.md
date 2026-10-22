# Changelog

## 0.15.7

- **Your rabbit comes back on its own when the server restarts.** Until now a
  restart of this add-on — an update, a Home Assistant reboot, a crash — left
  the rabbit orange until someone unplugged it. Stock Violet bytecode reboots
  the rabbit when the server's socket closes, and it does: measured on
  hardware, it reboots and re-downloads its bytecode 13 seconds later, then
  dies before it even asks for `locate.jsp`. A soft reboot does not bring the
  2006 rt2501 radio back; a power cycle does. The bundled bytecode now takes
  the gentler branch Violet wrote and declined: reset to idle, base LED violet,
  wait 5 s plus a random 0-30 s, reconnect — the network stack is never torn
  down. Proven on hardware: drop at 19:41:12, operational again at 19:41:26,
  no reboot in between.
- **The mDNS resolver is back.** The bundled bytecode had been built without
  it (459 functions instead of 468), so a rabbit whose gateway drops its DNS
  queries could not resolve a `.local` server name. It can again.
- **One power cycle to pick this up**, since the rabbit only downloads its
  bytecode at boot.

  *Correction, 2026-10-22:* this said "it should be the last one". It is not,
  and the code never supported the claim. Three `reboot`s remain on the
  `locate.jsp` and code-9 paths (#23, finding 4), and a software reset leaves
  seven "already initialised" flags holding their previous values, four of
  which produce the same amber-until-unplugged on their own (#25). The flags
  are fixed in #30; the `reboot`s are not.

## 0.15.6

- **arm64/aarch64 declared (#7).** *Tested on real hardware 2026-10-22:*
  the image builds on a Pi, whisper.cpp compiles from source, and the server
  imports. The bundled `small` model takes ~51 s for 2 s of audio there, so a
  wake word on arm wants `tiny` (~6 s) or `base` (~12 s). The add-on no longer refuses
  to install on a Raspberry Pi. It builds whisper.cpp from source on the same
  musl base, so it *should* work — but it has not been built or run on real
  arm hardware, so treat it as untested until someone confirms.

- **Fixed: a fresh install had no bytecode at all** (#2). The server reads
  `/data/bootcode.hybrid`, but nothing ever put a file there — it was dropped in
  by hand on the dev machine, so every clean install logged `bootcode file not
  found` and answered `/vl/bc.jsp` with a 500. The rabbit then booted and sat
  there silently. The hybrid bytecode is now **bundled in the image** (checksum
  verified at build), and the server picks the first bytecode that exists:
  `BOOTCODE_FILE` → `/data/bootcode.hybrid` → the bundled copy → a local
  `firmware/bootcode_hybrid.bin` (not committed). A hand-built bytecode in `/data` still wins,
  and `python3 server.py` now works straight from a checkout.

## 0.15.5

- **Personality: no more autonomous nose blink.** The `personality` "sign of life"
  is now only the random LED colour dance or an ear wiggle — the occasional nose
  blink was removed by preference. (Dance ~75% / ear wiggle ~25%.)

## 0.15.4

- **Pick any voice — up to Siri/Alexa-natural.** New **`tts_entity`** option: when
  `tts_engine` is `piper`/`ha`, Nabi speaks through *any* Home Assistant TTS entity
  you name — `tts.piper` (local neural), `tts.home_assistant_cloud` (Nabu Casa),
  `tts.elevenlabs`, `tts.google_cloud`, `tts.azure`, `tts.openai`, or a self-hosted
  Wyoming voice. `tts_engine` now accepts `ha` as a clearer synonym of `piper`
  ("use the HA TTS entity"). README + DOCS gained a voice/TTS guide (local ↔ cloud
  trade-off table); the docs note `voice_pitch: 0` is the most natural setting.

## 0.15.3

- **Personality is now a real LED colour show.** When `personality` is
  `discret | normal | vif`, Nabi's autonomous "sign of life" is mostly a **random
  LED colour dance** — the 5 LEDs (belly included) light up one after another in a
  random order and random vivid colours, hold a beat, then fade out — so between
  shows the rabbit stays calm instead of sitting on a single colour. Ear wiggles
  and nose blinks still happen, less often. Previously personality only nudged the
  side LEDs and deliberately avoided the belly.

## 0.15.2

- **Docs refresh** — `DOCS.md` brought in line with the current options:
  `personality` is `off | discret | normal | vif` (was wrongly `subtle/auto/lively`);
  added `stt_model` (`tiny | base | small`, default `small`); removed the gone
  `voice_prompt` / `tts_entity` / `bootcode` references; the wake word is the
  built-in `nabi`. Passive listening ("hey Nabi") is now clearly marked **beta**.
- Clarified that **`personality: off` is not "asleep"** — a connected rabbit still
  breathes; only `/api/sleep` (and the companion's night mode) makes it dormant.
- Documented the Home Assistant packages **`proactive.yaml`** (Claude-driven
  companion: anti-repetition memory, optional silence, morning agenda+weather
  briefing, `nabaztag_notify`, night-mode that sleeps the rabbit) and
  **`rfid.yaml`**, plus a point-and-click **blueprint** to wire any house event
  to Nabi's voice.

No code changes — documentation only.
