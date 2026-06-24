# Changelog

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
