# Nabaztag Violet Server — Add-on documentation

A clean, dependency-free server (Python stdlib) that revives an **original
Nabaztag / Nabaztag:tag** by speaking the **Violet protocol** directly — no
OpenJabNab, no openab. It replaces the dead Violet servers.

## What it does

- **HTTP boot (`:80`)** — `/vl/bc.jsp` serves the original bytecode
  (`bootcode.violet`, bundled), `/vl/locate.jsp` points the rabbit at this host.
- **XMPP (`:5222`)** — accepts the rabbit's connection using the documented
  **SASL success-bypass** (no Violet password needed), binds it, keeps it idle,
  and can push `violet:packet` commands. **Every byte the rabbit sends is
  logged** — this doubles as the platform to finish reverse-engineering the
  command/event packets on the real device.
- **Control API (`:8080`)** — small HTTP API for Home Assistant.

## Install

1. Add the repo `https://github.com/ClaraVnk/nabaztag` (Settings → Add-ons →
   Store → ⋮ → Repositories) if not already.
2. **Stop the "Nabaztag Server (OpenJabNab)" add-on** — it uses the same
   `:80`/`:5222` ports.
3. Install **Nabaztag Violet Server**. Set `server_address` to a **hostname**
   (e.g. `nabaztag.lan`) — **not a raw IP**: the bytecode resolves the XMPP server
   by DNS, so it must be a resolvable name. Then **Start**. (The runtime bytecode
   is bundled — a *hybrid* build, so passive listening and volume work out of the
   box; there's nothing to choose.)

## Point the rabbit at the server

1. **DNS record** so the rabbit resolves the `server_address` hostname to this
   host (the bytecode resolves the XMPP server by DNS — an IP literal fails):
   ```
   nabaztag.lan  →  <HAOS_IP>
   ```
   Add it where the rabbit's DNS lives (UniFi "Local DNS Records", AdGuard/Pi-hole
   rewrite…). Verify from any device: `nslookup nabaztag.lan` → `<HAOS_IP>`.
2. **Rabbit config** — hold its head while powering it (LEDs go blue), join its
   `NabaztagXX` Wi-Fi, open `192.168.0.1`, set **Violet Platform** to
   `http://<HAOS_IP>/vl` (the IP is fine here — boot/locate don't use DNS), then
   *update and start*.
3. Power-cycle and watch the log: `serving bootcode → … → answered
   violet:iq:sources → bound and idle`. The rabbit then **breathes** = operational.

> **Rabbit on its own VLAN/subnet?** Allow it through the firewall to this host on
> TCP **80** and **5222**, and give it a **DHCP reservation** — a changed IP
> silently breaks the (IP-scoped) firewall rule (this cost us hours). On a flat
> home network there's nothing to do.

## Control API (Home Assistant)

Reached on host port **8099** (container `:8080`). If a single rabbit is connected
`mac` is optional; otherwise pass `?mac=<lowercase-no-colons>`.

The API is **open by default** (fine on a trusted LAN behind Home Assistant). Set
the `api_token` option to require a shared secret on every `/api/` request — pass
it as the `X-API-Token` header or a `?token=` query param (anything else → `401`).
This is the minimal guard to enable before exposing the server beyond the LAN. If
you turn it on, add the token to the `rest_command`s, e.g. a `headers:` block with
`X-API-Token: !secret nab_token` (or append `&token=…` to the URLs).

The rabbit has **two command channels** (reverse-engineered from the bytecode):

- **Programs** (`MessagePacket`, type `0x0A`) — the rich channel: play audio,
  move ears to precise angles, run RGB light shows. Resources (audio, light
  shows) are fetched by the rabbit over HTTP **from this add-on** (`/res/…`).
- **Ambient** (`AmbientPacket`, type `0x04`) — belly icons, the bottom LED, the
  nose, sleep/wake. *Ambient "ears" only wiggle the ears back home + beep* (the
  original "a friend moved their ears" effect) — it does **not** set an angle;
  use `/api/ears` (a program) for real positioning.

Endpoints:

- `GET /api/status` — `online` (any rabbit connected), connected rabbits with
  their XMPP resource + `uptime`, and `last_seen` (recently-dropped rabbits with
  `offline_for`) — so a silent drop (e.g. a DHCP IP change) is visible at a glance.
- `GET /api/say?text=Aujourd'hui… pluie !` — **local TTS** (espeak-ng → WAV,
  bundled, no cloud): the rabbit speaks the text. `&voice=fr&speed=160&pitch=50`,
  `&wait=1` to block. This is the simplest way to make the rabbit talk. A
  **`[pause]`** (or `[pause 800]` ms) marker in the text becomes a **real silence**
  — used mainly for the iconic weather cadence (`Aujourd'hui [pause] pluie !`).
  Note: splitting around a pause means each part is synthesized separately, so the
  prosody is a bit staccato — kept for the weather signature, not general replies.
- `GET /api/play?url=<mp3/wav>` — **stream + play audio** from a URL (e.g. a
  Home-Assistant/Piper TTS media URL). `&wait=1` blocks until it finishes.
  Also accepts **`POST`** with the audio bytes as the body (served from `/res/`).
  The rabbit decodes **MP3 and WAV (PCM, 22 kHz/16-bit mono)** — both verified live.
- `GET /api/jingle?name=acquired` — play an **original Nabaztag jingle** (extracted
  from the firmware, rendered on the fly by a tiny built-in MIDI synth). `GET
  /api/jingle` (no name) lists them: `acquired, ack, abort, ministop, ears,
  rfid_ok, start_record, end_record, start_interactive, end_interactive, previous,
  next`. `&wait=1` blocks. Set the **`connect_jingle`** option to a jingle name to
  have Nabi play it automatically when it comes online (empty = off).
- `GET /api/volume?level=70` — **set the speaker volume** (`0`–`100`, 100 = loudest).
  Needs the hybrid bytecode (the `SV`/`sndVol` command). Persists across reboots
  (re-applied on reconnect). Exposed in HA as `nabaztag_volume` + a slider.
- `GET /api/ears?left=8&right=2` — **move the ears** to positions (~0..16) via a
  choreography; `&dir=0|1` sets rotation direction.
- `GET /api/led?led=top&r=0&g=238&b=0` — **full RGB** on one LED
  (`bottom|left|middle|right|top`) via a choreography.
- `GET /api/choreography?spec=100,0,motor,0,180,0,0,5,led,4,0,238,0` — raw light
  show: `tempo,(time,order,p3,p4,p5,p6)…` (`motor`→ear,angle,_,dir / `led`→led,r,g,b).
- `GET /api/program?text=ST%20http://…|MW` — raw program (`|` = newline).
- `GET /api/weather?v=storm` — belly icon (`sun|cloudy|smog|rain|snow|storm`).
- `GET /api/nose?v=1` — nose (0 none / 1 blink / 2 double-blink).
- `GET /api/bottomled?v=3` — bottom belly LED (palette index, no fetch).
- `GET /api/earwiggle?left=1&right=1` — the ambient "ears wiggle home" effect.
- `GET /api/sleep?on=0` — wake (`0`) or sleep (`1`).
- `GET /api/state` — ask the rabbit to report its XMPP/run state (logged).
- `GET /api/lastrecording?format=pcm` — download the last **push-to-talk**
  recording (hold the rabbit's head button and speak). `pcm` = 16-bit PCM WAV
  (decoded, for STT); `adpcm` = the raw IMA-ADPCM upload. The rabbit POSTs it to
  `/vl/record.jsp` automatically. This is the Phase-2 voice input.
- `GET /api/ambient?svc=8&val=1` — generic AmbientPacket (repeatable `svc`/`val`).
- `GET /api/raw?b64=<base64>` — inject a raw violet packet.

## Voice (push-to-talk → conversation agent)

Hold the rabbit's **head button** and speak; on release it records the mic and
POSTs the audio here. With the voice pipeline on, the add-on transcribes it
(bundled **whisper.cpp**), sends the text to a Home Assistant **conversation
agent** (e.g. Claude), and speaks the reply. The agent can also drive the rabbit
by putting `[ears L R]`, `[led ZONE R G B]` or `[nose N]` tags in its reply.

Enable it in the add-on **Configuration**:

- `voice_pipeline`: `true`
- `conversation_agent`: which "brain" answers — **your choice of agent entity**:
  - `conversation.claude_conversation` → **Claude** (chatty; can move the rabbit via
    the action tags), or
  - `conversation.home_assistant` → **Home Assistant's own Assist** (local intents —
    controls your home, tells the time, etc.), or
  - *empty* → just echo the transcription back.
- `stt_language`: `fr`
- `stt_model`: `tiny | base | small` (default **`small`**) — the bundled
  whisper.cpp model. Bigger = more accurate but slower; even `base` does decent
  French on the rabbit's 8 kHz mic.
- `tts_engine`: `espeak` (bundled, robotic) or `piper` (nicer — needs the HA
  **Piper** add-on; the add-on fetches its audio through the Supervisor proxy).
  The Piper entity defaults to **`tts.piper`**; for a softer French voice use
  **`fr_FR-siwis-medium`** in the Piper add-on (the clearest French female).
- `voice_pitch`: shift the voice **up** by this many percent for a cuter / younger,
  more playful timbre (`0` = off; ~`12`–`18` is a nice "mignonne" range; `>30` =
  chipmunk). Stock Piper voices are neutral, so this is how you make Nabi sound
  more espiègle without a custom voice. Needs ffmpeg (bundled).

Keep replies short — the charm is brevity. The agent's instruction (the Nabi
persona + the action-tag explanation) is built in; you shape the *content* by
choosing the conversation agent and its own system prompt.

### Passive listening ("hey Nabi") & privacy — BETA

> **Beta.** Hands-free "hey Nabi" works end-to-end but wake-word accuracy and
> barge-in handling are still being tuned. **Push-to-talk is the stable path.**

A hands-free wake word is **opt-in and OFF by default** — a deliberate privacy
choice (push-to-talk needs no always-on mic). When it is **off, the rabbit does
not stream its microphone at all**; nothing is captured or sent. Controls:

- `auto_listen`: `false` (default) → no passive listening. Set `true` to start it
  automatically once the rabbit is idle.
- The wake word is **`nabi`** (built in).
- `wake_chime`: a short jingle played as a "got it" cue after the wake word (once
  the mic stops, while the agent thinks) — default `start_record`; `none` to disable.
- Toggle it **at runtime** with `GET /api/mic?on=1` / `?on=0`. Turning it **off
  sends `RT`**, so the rabbit *stops capturing* — it is not a server-side mute.
  The `home-assistant/` package exposes this as the `nabaztag_listen` rest_command
  and a **"Nabi passive listening" toggle** for a dashboard.

The bundled bytecode is already a *hybrid* build, so passive listening works out
of the box; push-to-talk is always available regardless of this setting.

## Home Assistant integration (REST)

The integration is **pure REST** — no MQTT broker. Home Assistant drives Nabi
through this add-on's HTTP control API via `rest_command`s. Two ready-to-paste
packages ship in [`home-assistant/`](https://github.com/ClaraVnk/nabaztag/tree/main/home-assistant):

- **`nabaztag.yaml`** — the `rest_command`s (say / play / ears / led / weather /
  nose / sleep / jingle / volume / listen) plus helper scripts.
- **`proactive.yaml`** — the **Claude-driven proactive companion**: Nabi pipes up
  on its own in Violet's tone (morning greeting + agenda/weather briefing, welcome
  home, spontaneous remarks where Claude may stay silent, "the sky turned", a
  silent belly that mirrors the weather), plus a reusable **`nabaztag_notify`**
  script (route any house event through Nabi's voice). Gated by a master switch +
  a **night-mode** sensor that silences speech *and* sleeps the rabbit 22:00→08:00.
- **`rfid.yaml`** — remembers the last scanned tag (to discover ids) and dispatches
  known RFID/Ztamp tags to actions.
- **`ambient.yaml`** — simpler example ambient automations (belly = colour of the
  day, morning/night, arrival, appliance-done, intercom).
- **`entities.yaml`** — optional **dashboard controls** (a sleep toggle, a nose
  selector, an ears slider, belly R/G/B sliders) built from input helpers +
  automations on those `rest_command`s — replaces the old MQTT entities, no broker.
- **`blueprints/nabaztag_event_reaction.yaml`** — a **point-and-click blueprint**:
  pick any entity + the state that should fire + a message, and Nabi reacts in its
  voice (via `nabaztag_notify`) — no YAML. The easy way to wire house events
  (doorbell, laundry done, a door opening…) to Nabi. Drop it in
  `/config/blueprints/automation/` then **Settings → Automations → Create →
  *Use a blueprint***.

Set `nab_api` (`<HAOS_IP>:8099`) and `nab_mac` in `secrets.yaml`, drop the files
in `/config/packages/`, and reload YAML.

## Inputs → Home Assistant events (button / RFID / ears)

Nabi is also an **input device**: it reports its head **button**, **RFID/Ztamp**
tags, and **ear-by-hand** movements, and the add-on re-emits them as a Home
Assistant event named **`nabaztag_event`** (fired via the Supervisor proxy — no
config needed). Trigger automations on it:

- **Button** — `type: button`, `clic` (1–4) and `action`:
  `click` (single), `double_click`. *(Long / double-long presses drive
  push-to-talk recording, so they are not reported as button events.)*
- **RFID/Ztamp** — `type: rfid`, `tag` (the tag id) — tag an object to trigger a
  scene. Reported over HTTP (`/vl/rfid.jsp`), works on the stock bytecode. The
  ready-made **`home-assistant/rfid.yaml`** package remembers the last tag (to
  discover ids) and dispatches known tags to actions. Test it without a physical
  tag by replaying the request: `curl "http://<HAOS_IP>/vl/rfid.jsp?sn=<mac>&t=deadbeef01"`.
- **Ears** — `type: ears`, `left` / `right` (~0..16) when you turn the ears by hand.

```yaml
automation:
  - alias: "Nabi button → toggle a light"
    trigger:
      - platform: event
        event_type: nabaztag_event
        event_data: { type: button, action: click }
    action:
      - service: light.toggle
        target: { entity_id: light.living_room }

  - alias: "Nabi RFID → run a scene"
    trigger:
      - platform: event
        event_type: nabaztag_event
        event_data: { type: rfid }
    action:
      - service: rest_command.nabaztag_say
        data:
          api: !secret nab_api
          mac: !secret nab_mac
          text: "Tiens, un objet magique… {{ trigger.event.data.tag }} !"
```

## Personality — let it live, or make it yours

Nabi can have **a life of its own** or be **entirely driven by you** — your choice
via the `personality` option:

- **`personality: discret | normal | vif`** — the add-on animates Nabi on its
  own: a gentle random behaviour (an ample ear wiggle, a soft side-LED pulse, or
  a nose blink) at random intervals, **daytime only**, and never on top of a
  voice reply. It deliberately leaves the **belly LED** alone so it won't fight a
  colour-of-the-day. The level sets the cadence — `discret` ≈ a few times/hour,
  `normal` ≈ every ~10–25 min, `vif` ≈ every few minutes (advanced env overrides
  `personality_min_s` / `personality_max_s` exist but aren't exposed in the UI).
- **`personality: off`** (default) — Nabi just breathes, and **you** decide what
  it does, when, via Home Assistant automations (see `proactive.yaml` for the full
  Claude-driven companion, or `ambient.yaml` for simpler "signs of life").

> **Note — `off` does not mean asleep.** It only removes the *extra* autonomous
> moves; a connected rabbit still **breathes** (the stock idle animation) until
> something explicitly sends it to sleep (`/api/sleep?on=1`). "Night mode" in the
> companion package silences proactive speech *and* sleeps the rabbit through the
> quiet window — the breathing is not the `personality` option.

Pick one — don't run `vif` *and* HA idle automations at once, or they'll both
move the ears. Test a behaviour instantly any time with `GET /api/personality`.

## Status

- **Phase 1 — working, verified live:** the rabbit boots our server, **breathes**,
  and is fully driven — ears (choreography), the 5 RGB LEDs, nose, audio, and TTS
  (it **speaks**). All packet formats were reverse-engineered and verified against
  the bytecode. The key to pushed commands: the server answers the rabbit's
  **presence** so it reaches `ssFree` (and treats `<unbind>` separately from bind).
- **Phase 2 — working, verified live, no custom firmware:** button **push-to-talk**
  → bundled **whisper.cpp** STT → **conversation agent (Claude)** → spoken reply
  (espeak or **Piper** voice), with the agent able to move the ears/LEDs/nose.
- *asleep = LEDs off + ears down*; *breathing = awake/idle* — command it while it
  breathes (or send `/api/sleep?on=0` first).
