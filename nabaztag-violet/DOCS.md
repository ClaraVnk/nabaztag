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
   by DNS, so it must be a resolvable name. (`bootcode` option: `ojn` is the
   default and works.) Then **Start**.

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

The rabbit has **two command channels** (reverse-engineered from the bytecode):

- **Programs** (`MessagePacket`, type `0x0A`) — the rich channel: play audio,
  move ears to precise angles, run RGB light shows. Resources (audio, light
  shows) are fetched by the rabbit over HTTP **from this add-on** (`/res/…`).
- **Ambient** (`AmbientPacket`, type `0x04`) — belly icons, the bottom LED, the
  nose, sleep/wake. *Ambient "ears" only wiggle the ears back home + beep* (the
  original "a friend moved their ears" effect) — it does **not** set an angle;
  use `/api/ears` (a program) for real positioning.

Endpoints:

- `GET /api/status` — connected rabbits (and their XMPP resource).
- `GET /api/say?text=Aujourd'hui… pluie !` — **local TTS** (espeak-ng → WAV,
  bundled, no cloud): the rabbit speaks the text. `&voice=fr&speed=160&pitch=50`,
  `&wait=1` to block. This is the simplest way to make the rabbit talk.
- `GET /api/play?url=<mp3/wav>` — **stream + play audio** from a URL (e.g. a
  Home-Assistant/Piper TTS media URL). `&wait=1` blocks until it finishes.
  Also accepts **`POST`** with the audio bytes as the body (served from `/res/`).
  The rabbit decodes **MP3 and WAV (PCM, 22 kHz/16-bit mono)** — both verified live.
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
- `conversation_agent`: your agent entity, e.g. `conversation.claude_conversation`
  (leave empty to just echo the transcription)
- `stt_language`: `fr`
- `tts_engine`: `espeak` (bundled, robotic) or `piper` (nicer — needs the HA
  **Piper** add-on; the add-on fetches its audio through the Supervisor proxy)
- `tts_entity`: the Piper TTS entity, e.g. `tts.piper`
- `voice_prompt`: the instruction prepended for the agent (it explains the action
  tags and asks for short replies)

Keep replies short — the charm is brevity. The bundled STT model is `base`
(decent French on an 8 kHz mic); swap to a bigger whisper model for more accuracy.

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
