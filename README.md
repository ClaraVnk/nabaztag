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
  <img src="https://img.shields.io/badge/status-Phase%201-yellow" alt="Phase 1">
</p>

Bring an **original Nabaztag** (the 2005–2006 Wi-Fi rabbit by Violet/Mindscape)
back to life and drive it from **Home Assistant**, fully locally — no cloud.

This repository is a **Home Assistant OS add-on repository**. Its primary add-on
is a small, **dependency-free server that speaks the original Violet protocol**
directly: the rabbit phones home to it (the real Violet servers died around
2011), and Home Assistant drives the rabbit — ears, LEDs, sounds — through a
simple control API.

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

- 🐰 Move the **ears** &nbsp; 🌈 set **LED** colors &nbsp; 🔊 play a **sound / MP3**
- 🗣️ **Speak** via HA's local TTS → MP3 (see TTS note)
- 📟 (v2) react to **button** presses and **RFID** tags *(surfaced in the logs)*
- 🎤 talk to it via the **microphone** → *Phase 2, see Roadmap*

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
2. Install **Nabaztag Violet Server**, open its **Configuration** tab and set
   `server_address` to your **HAOS host IP**, then **Start** it.
3. Allow the rabbit's VLAN to reach the host on TCP **80** and **5222**
   (inter-VLAN firewall rule — required for **both** methods below).
4. **Point the rabbit at the server — pick the method you prefer:**

   - **Method A · DNS redirect** *(recommended — nothing to type on the rabbit,
     survives a factory reset):* in UniFi, add a DNS host record
     `r.nabaztag.com → <haos-ip>` scoped to the rabbit's VLAN, and leave the
     rabbit on its factory server. The rabbit must use the UniFi/DHCP DNS.
   - **Method B · configure the rabbit directly** *(no DNS change):* hold its head
     while powering it (LEDs go blue), join its `NabaztagXX` Wi-Fi, open
     `192.168.0.1`, set **Violet Platform** to `http://<haos-ip>/vl`, then
     *update and start*.

5. Power-cycle the rabbit and watch the add-on log for
   `bound and idle — ready for commands`. Optionally drop
   `home-assistant/nabaztag.yaml` into `/config/packages/`.

Full guide (API, pairing, troubleshooting) is in
**[`nabaztag-violet/DOCS.md`](nabaztag-violet/DOCS.md)**.

## Roadmap

- **Phase 1 — done:** our own server; the rabbit boots our bytecode and connects;
  HA control API; full traffic logging. Command payloads (LED/ears/sound) are
  best-effort (framing correct) and get refined against the real rabbit.
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

The rabbit's original TTS relied on Acapela's now-dead web service. For reliable,
fully-local speech, generate audio with Home Assistant's local TTS (e.g. Piper)
and play the resulting MP3 on the rabbit.

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
docraven (Flickr), via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Nabaztag.jpg),
licensed [CC BY-SA 2.0](https://creativecommons.org/licenses/by-sa/2.0).

## License

[Apache License 2.0](LICENSE) — the same license as Home Assistant.
