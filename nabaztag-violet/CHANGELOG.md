# Changelog

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
