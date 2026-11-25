# The Home Assistant add-on (legacy)


This repository is a **Home Assistant OS add-on repository** (`repository.yaml`
at the root). Its add-on, **Nabaztag Terrier** (`nabaztag-violet/`), is a
small, **dependency-free server that speaks the original Violet protocol**
directly: the rabbit phones home to it, and Home Assistant drives the rabbit —
ears, belly weather icons, nose — through a simple control API. A **stock**
rabbit needs nothing flashed for it. A rabbit on **Naboot** gets the same
adoption key as on Le Terrier (since 0.17.0): you confirm the five belly
colours on the add-on's page, and from then on its bytecode is signed and its
session runs inside TLS 1.3 on port 5223 (see the add-on's
[`DOCS.md`](../nabaztag-violet/DOCS.md), *Adoption key and TLS*).

It still works and it is still here — but the server it embeds is the ancestor
of Le Terrier, and new work goes to the
[HACS integration](../README.md#home-assistant) and to the warren
rather than here.

> `legacy/nabaztag-server/` is a different, older add-on: the first one, built on
> OpenJabNab. It is no longer offered in the store -- its manifest is kept as
> `config.yaml.retired`, which the Supervisor does not read -- and its image
> cannot be patched — if you still have it installed, **uninstall it** (see its
> [`DOCS.md`](../legacy/nabaztag-server/DOCS.md)).

## How it works

```
Nabaztag (<rabbit-ip>)  ──HTTP /vl/bc.jsp,/vl/locate.jsp :80──▶  ┌──────────────────────────┐
       ▲                                                          │  Add-on @ <haos-ip>       │
       │                ◀──── XMPP push (commands) :5222 ───────  │  nabaztag-violet (Python) │
       │                ───── XMPP events (button/RFID) ───────▶  │   HTTP boot + XMPP + API  │
Home Assistant  ──── HTTP control API :8099 (/api/...) ─────────▶ └──────────────────────────┘
```

On boot the rabbit fetches its **bytecode** (`/vl/bc.jsp`) and a **locate** file
(`/vl/locate.jsp`) that points it at our server, then opens an **XMPP** stream.
Our server completes the handshake using the documented **SASL success-bypass**
(no Violet password needed), keeps the rabbit idle, pushes `violet:packet`
commands, and **logs everything** the rabbit sends. No OpenJabNab, no reverse
proxy — one clean process.

## Quick start

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
   add `https://github.com/ClaraVnk/nabaztag`.
2. Install **Nabaztag Terrier**. In **Configuration**, set `server_address`
   to a **hostname** such as `nabaztag.lan` — **not a raw IP**: the bytecode
   resolves the XMPP server by **DNS**, so an IP literal fails. **Start** the
   add-on.
3. **Add a DNS record** so the rabbit resolves that hostname to the HAOS host:
   `nabaztag.lan → <haos-ip>` (UniFi "Local DNS Records", AdGuard/Pi-hole rewrite…).
   Verify from any device: `nslookup nabaztag.lan` → `<haos-ip>`.
4. **Point the rabbit at the server:** hold its head while powering it (LEDs go
   blue), join its `NabaztagXX` Wi-Fi, open `192.168.0.1`, set **Violet Platform**
   to `http://<haos-ip>/vl` (the IP is fine here — boot/locate don't need DNS),
   then *update and start*.
5. Watch the add-on log: `serving bootcode → … → bound and idle — ready for
   commands`. Optionally drop `home-assistant/nabaztag.yaml` into `/config/packages/`.

> Running the server **outside** Home Assistant (a Pi, a VPS)? `python3
> nabaztag-violet/server.py` from a checkout serves the committed
> `nabaztag-violet/bootcode.hybrid` on its own. See `nabaztag-violet/DOCS.md`,
> *Bytecodes — and running the server outside the add-on*.

> **Rabbit on its own VLAN/subnet?** Allow it through the firewall to the HAOS
> host on TCP **80** and **5222**, and give it a **DHCP reservation** (so its IP —
> and the firewall rule — stay valid; a changed IP silently breaks everything). On
> a flat home network there's nothing to do.

Full guide (API, pairing, troubleshooting) is in
**[`nabaztag-violet/DOCS.md`](../nabaztag-violet/DOCS.md)**.

## Host architecture

**amd64**. **arm64/aarch64** (Raspberry Pi 4/5) is provisional — the add-on
declares it, but nobody has built or run it on real arm hardware yet. Reports
welcome in [#7](https://github.com/ClaraVnk/nabaztag/issues/7).

## What you can do from Home Assistant

Commands use the two `violet:packet` channels reverse-engineered from the
bytecode — *programs* (`MessagePacket`) for the rich stuff, *ambient*
(`AmbientPacket`) for the at-a-glance indicators:

- 🗣️ make it **speak** any text — **built-in local TTS** (`/api/say`, espeak-ng;
  no cloud, nothing to install)
- 🔊 **play audio** — stream any MP3 or WAV (e.g. a Piper TTS media URL); the
  rabbit fetches it over HTTP from the add-on
- 🎵 **original Nabaztag jingles** — the iconic Violet sounds (extracted from the
  firmware as MIDI, synthesized on the fly): `acquired`, `ack`, `rfid_ok`, …
- 👂 **move the ears** to precise positions (independent left / right) via a
  choreography
- 💡 **RGB light shows** on the 5 LEDs (bottom / left / middle / right / top)
- 🌦️ **belly icons** — weather (sun/cloudy/smog/rain/snow/storm), stock, e-mail,
  air quality &nbsp; 👃 **nose** blink &nbsp; 💤 **sleep / wake**
- 🎤 **talk to it** — hold the head button, ask a question; it transcribes
  (bundled whisper.cpp), asks a **conversation agent (e.g. Claude)**, and speaks
  the reply — which can itself move the ears/LEDs
- 🐇 **give it standing orders** — *"préviens-moi quand la batterie de
  l'aspirateur est pleine"*, *"rappelle-moi de sortir le linge dans 40 minutes"*.
  Nabi remembers, watches the condition (or the clock) and pipes up on its own —
  then forgets the task. See [Give Nabi standing orders](#give-nabi-standing-orders).
- 📟 (v2) react to **button** presses, **RFID/Ztamp** tags and **ears moved by
  hand** — re-emitted as `nabaztag_event` **Home Assistant events** to trigger
  automations (Nabi as an input device)

## Give Nabi standing orders

Beyond one-off questions, you can hand Nabi a **deferred task** in plain French and
let it watch for you:

> *"Nabi, préviens-moi quand la batterie de l'aspirateur est pleine."*
> *"Rappelle-moi de sortir le linge dans 40 minutes."*
> *"Oublie tout."*

Nabi remembers the order, **Home Assistant watches the condition** (a state
threshold, an entity state, or the clock), and the rabbit **announces it on its
own** in Violet's voice once it's met — then forgets the task (single shot, up to
5 at a time).

It works by exposing three scripts as **tools** to your Claude conversation agent
(no add-on change): the spoken request already flows to that agent, which calls
`nabaztag_remember_task` / `nabaztag_remind` / `nabaztag_forget`. A 1-minute
engine in `home-assistant/nabaztag_tasks.yaml` evaluates the stored tasks and
routes the announcement through `script.nabaztag_notify` (so quiet hours still
apply unless the reminder is marked urgent).

**Setup (once):**

1. Drop `home-assistant/nabaztag_tasks.yaml` into `/config/packages/` (it reuses
   `proactive.yaml`'s `script.nabaztag_notify` — install that too).
2. **Anthropic integration → enable "Control Home Assistant"** (the LLM API).
3. **Settings → Voice assistants → Expose** the three `nabaztag_*` scripts **and
   the entities Nabi should watch** (vacuum, washer, weather…) so Claude knows
   their `entity_id`.
4. Reload YAML. Try it by voice, or test from **Developer Tools → Actions**.

## Voice / TTS — make Nabi sound as natural as you like

The rabbit's original TTS relied on Acapela's now-dead web service. The add-on
ships its own **fully-local TTS** (`espeak-ng`, robotic but offline) **and** can
speak through **any Home Assistant TTS engine** — so the voice is *your* choice,
from robotic-and-local to Siri/Alexa-natural. Set it in the add-on options:

- **`tts_engine`** — `espeak` (bundled, robotic, 100% local) or **`piper` / `ha`**
  (speak through the HA TTS entity below).
- **`tts_entity`** — when the engine is `piper`/`ha`, *which* HA voice to use.
  Point it at any `tts.*` entity in your Home Assistant:

  | `tts_entity` | Voice | Local? | Naturalness |
  |---|---|---|---|
  | `tts.piper` | Piper (neural, on-device) | ✅ local, free | good |
  | `tts.home_assistant_cloud` | HA Cloud / Nabu Casa | ☁️ (privacy-friendly) | very natural |
  | `tts.elevenlabs` | ElevenLabs | ☁️ paid | the most natural |
  | `tts.google_cloud` / `tts.azure` / `tts.openai` | Google / Azure / OpenAI | ☁️ paid | very natural |
  | (Wyoming) Kokoro / XTTS | newer neural, self-hosted | ✅ local | very natural, more setup |

  Whatever you install in HA appears as a `tts.*` entity — just name it here.
- **`voice_pitch`** — pitch-shift up for a cuter timbre. **`0` is the most natural**;
  `~12–18` is the playful "mignonne" range; `>30` goes chipmunk.

> Privacy note: `espeak` and `piper` stay on your network; the cloud engines send
> the spoken text to a third party. Pick the row that matches your trade-off.

You can also bypass TTS entirely: generate audio elsewhere and hand the rabbit a
media URL via `GET /api/play?url=…` (it decodes MP3 and 22 kHz/16-bit mono WAV),
or `POST` the audio bytes directly and the add-on serves them.
