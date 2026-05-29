<p align="center">
  <img src="images/nabaztag.jpg" alt="Nabaztag" height="140">
  &nbsp;&nbsp;&nbsp;
  <img src="images/home-assistant.png" alt="Home Assistant" height="44">
</p>

# Nabaztag for Home Assistant OS

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache 2.0"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5?logo=home-assistant&logoColor=white" alt="Home Assistant add-on">
  <img src="https://img.shields.io/badge/arch-amd64-lightgrey" alt="amd64">
  <img src="https://img.shields.io/badge/protocol-Violet-orange" alt="Violet protocol">
  <img src="https://img.shields.io/badge/python-stdlib-3776AB?logo=python&logoColor=white" alt="Python stdlib">
  <img src="https://img.shields.io/badge/100%25-local-success" alt="100% local, no cloud">
  <img src="https://img.shields.io/badge/status-talking%20%2B%20moving%20(live)-success" alt="Talking and moving — verified live">
</p>

Bring an **original Nabaztag** (the 2005–2006 Wi-Fi rabbit by Violet/Mindscape)
back to life and drive it from **Home Assistant**, fully locally — no cloud.

This repository is a **Home Assistant OS add-on repository**. Its primary add-on
is a small, **dependency-free server that speaks the original Violet protocol**
directly: the rabbit phones home to it (the real Violet servers died around
2011), and Home Assistant drives the rabbit — ears, belly weather icons, nose —
through a simple control API.

> ⚠️ **Uncertain-outcome project.** A stock rabbit is a "dumb" Wi-Fi client that
> downloads its bytecode from the server on every boot (nothing is flashed) and
> talks plain HTTP + XMPP (no TLS pinning). Booting it against our own server and
> getting it to connect is **Phase 1 (done)**. Capturing its **microphone** for
> voice is **Phase 2** and genuinely harder (see Roadmap). If a wall proves too
> high, the hardware fallback is a **tagtagtag/pynab** card — out of scope here.

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

## What you can do from Home Assistant

Commands use the two `violet:packet` channels reverse-engineered from the
bytecode — *programs* (`MessagePacket`) for the rich stuff, *ambient*
(`AmbientPacket`) for the at-a-glance indicators:

- 🗣️ make it **speak** any text — **built-in local TTS** (`/api/say`, espeak-ng;
  no cloud, nothing to install)
- 🔊 **play audio** — stream any MP3 or WAV (e.g. a Piper TTS media URL); the
  rabbit fetches it over HTTP from the add-on
- 👂 **move the ears** to precise positions (independent left / right) via a
  choreography
- 💡 **RGB light shows** on the 5 LEDs (bottom / left / middle / right / top)
- 🌦️ **belly icons** — weather (sun/cloudy/smog/rain/snow/storm), stock, e-mail,
  air quality &nbsp; 👃 **nose** blink &nbsp; 💤 **sleep / wake**
- 📟 (v2) react to **button** presses and **RFID** tags *(surfaced in the logs)*
- 🎤 **microphone → Claude** → *Phase 2*

## Repository layout

```
.
├── repository.yaml          # declares this as an HA add-on repository
├── nabaztag-violet/         # ⭐ primary add-on — our own Violet-protocol server
│   ├── server.py            #    dependency-free Python (HTTP boot + XMPP + control API)
│   ├── config.yaml          #    options (server_address, log_level), ports, arch
│   ├── Dockerfile           #    installs python3, fetches the bytecode at build
│   ├── build.yaml
│   └── DOCS.md              #    install / pairing / API / status
├── nabaztag-server/         # OpenJabNab add-on (superseded — kept for reference)
│   └── …                    #    its HTTP listener is non-functional in the upstream image
├── home-assistant/
│   └── nabaztag.yaml        # ready-to-paste HA package (rest_commands + automations)
├── README.md
└── LICENSE
```

## Quick start

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
   add `https://github.com/ClaraVnk/nabaztag`.
2. Install **Nabaztag Violet Server**. In **Configuration**, set `server_address`
   to a **hostname** such as `nabaztag.lan` — **not a raw IP**: the bytecode
   resolves the XMPP server by **DNS**, so an IP literal fails. (`bootcode` option:
   `ojn` is the default and works.) **Start** the add-on.
3. **Add a DNS record** so the rabbit resolves that hostname to the HAOS host:
   `nabaztag.lan → <haos-ip>` (UniFi "Local DNS Records", AdGuard/Pi-hole rewrite…).
   Verify from any device: `nslookup nabaztag.lan` → `<haos-ip>`.
4. **Point the rabbit at the server:** hold its head while powering it (LEDs go
   blue), join its `NabaztagXX` Wi-Fi, open `192.168.0.1`, set **Violet Platform**
   to `http://<haos-ip>/vl` (the IP is fine here — boot/locate don't need DNS),
   then *update and start*.
5. Watch the add-on log: `serving bootcode → … → bound and idle — ready for
   commands`. Optionally drop `home-assistant/nabaztag.yaml` into `/config/packages/`.

> **Rabbit on its own VLAN/subnet?** Allow it through the firewall to the HAOS
> host on TCP **80** and **5222**, and give it a **DHCP reservation** (so its IP —
> and the firewall rule — stay valid; a changed IP silently breaks everything). On
> a flat home network there's nothing to do.

Full guide (API, pairing, troubleshooting) is in
**[`nabaztag-violet/DOCS.md`](nabaztag-violet/DOCS.md)**.

## Roadmap

- **Phase 1 — working (verified live):** the rabbit boots our bytecode, completes
  the full XMPP handshake (incl. answering its `violet:iq:sources` query with an
  init packet), becomes operational (`idle`) and **breathes**. It receives binary
  **AmbientPacket** commands (ears, belly weather/stock/mail/air-quality icons,
  nose) and sleep/wake. Connection + command pipeline confirmed on the real device.
- **Phase 1.5 — working (verified live on the device):** the rich command channel
  runs end-to-end. The rabbit **speaks** (built-in espeak-ng TTS), **plays audio**
  (MP3 and WAV), **moves its ears** to precise positions and runs **RGB light
  shows** — pushed as `MessagePacket` programs whose audio/choreography resources
  the rabbit fetches back over HTTP from the add-on. Getting here needed two
  protocol fixes: the server must answer the rabbit's **presence** (so it reaches
  the `ssFree` state where it executes pushed commands) and must not let `<unbind>`
  be mistaken for a bind. A ready-to-use Home Assistant package (incl. a
  *speak Claude's reply on the rabbit* script) ships in `home-assistant/`.
- **Phase 2 — microphone & firmware:** the v2 mic needs firmware that streams it.
  The whole toolchain is open (RedoXyde's `mtl_linux` Metal compiler + simulator,
  the RE'd original firmware `nominal.mtl`, the `nabAsm`/`nabDasm` tools and
  `nabgcc`), so we can build/modify the bytecode and add a clean mic stream to our
  server — then wire it to a local STT (Whisper) and an LLM conversation agent.

## Hardware

- Works with the **Nabaztag** (v1, 2005) and **Nabaztag:tag** (v2, 2006 — mic +
  RFID). Targets the **stock** rabbit; no hardware mod, nothing flashed.
- Host architecture: **amd64**.

## TTS note

The rabbit's original TTS relied on Acapela's now-dead web service. The add-on
ships its own **fully-local TTS** (`espeak-ng`): `GET /api/say?text=…` makes the
rabbit speak — no cloud, nothing to install. For a nicer voice, generate audio
with Home Assistant's local TTS (e.g. Piper) and hand the media URL to
`GET /api/play?url=…` (the rabbit decodes MP3 and 22 kHz/16-bit mono WAV), or
`POST` the audio bytes directly and the add-on serves them.

## Credits

- The Violet documentation at [nabaztag.com/doc](https://nabaztag.com/doc) —
  official API, the **Metal** language grammar, and the Télécom SudParis report
  that reverse-engineered the v2 boot + XMPP protocol (the SASL success-bypass).
- [OpenJabNab](https://github.com/OpenJabNab/OpenJabNab) — reference PHP/C++ Violet
  reimplementation (and the bundled `bootcode.violet`).
- [RedoXyde](https://github.com/RedoXyde) (`mtl_linux`, `nabgcc`) and
  [Pixel166](https://github.com/Pixel166) (`nabAsm`/`nabDasm`) — the open Metal
  firmware toolchain that makes Phase 2 possible.

Images: Home Assistant logo © the Home Assistant project. Nabaztag photo by
Catalarem, via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Nabaztag1.jpg),
licensed [CC BY-SA 2.5](https://creativecommons.org/licenses/by-sa/2.5).

## License

[Apache License 2.0](LICENSE) — the same license as Home Assistant.
