# Changelog

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
