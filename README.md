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
  <img src="https://img.shields.io/badge/server-OpenJabNab-orange" alt="OpenJabNab">
  <img src="https://img.shields.io/badge/100%25-local-success" alt="100% local, no cloud">
  <img src="https://img.shields.io/badge/status-experimental-yellow" alt="experimental">
</p>

Bring an **original Nabaztag** (the 2005–2006 Wi-Fi rabbit by Violet/Mindscape)
back to life and drive it from **Home Assistant**, fully locally — no cloud.

This repository is a **Home Assistant OS add-on repository**. The add-on runs an
[OpenJabNab](https://github.com/OpenJabNab/OpenJabNab) server that the rabbit
phones home to (its original servers died around 2011), and exposes an HTTP API
that Home Assistant uses to wiggle the ears, set LED colors, play sounds, and
speak.

> ⚠️ **Uncertain-outcome project.** A stock rabbit is a "dumb" Wi-Fi client with
> a hard-coded server. Reviving it depends on redirecting that traffic and on the
> rabbit's aging firmware/Wi-Fi cooperating. The protocol is plain HTTP (no TLS
> pinning), so the odds are good — but if the wall proves too high, the fallback
> is the hardware route (a **tagtagtag/pynab** card inside the rabbit), which is
> out of scope for this add-on.

## How it works

```
Nabaztag (<rabbit-ip>)  ──HTTP /vl/* :80──▶  ┌───────────────────────────┐
       ▲                ◀──XMPP push :5222─  │  Add-on @ <haos-ip>        │
       │                                      │  Apache :80 ─▶ OpenJabNab  │
Home Assistant  ──HTTP /ojn_api/* :80──▶      │            127.0.0.1:8080  │
                                              └───────────────────────────┘
```

On boot the rabbit downloads its bootcode and a "locate" file, then opens an
XMPP connection and waits for **pushed** commands. OpenJabNab's HTTP listener
binds localhost only, so **Apache** serves port **80** and reverse-proxies the
`/vl/*` rabbit protocol and the `/ojn_api/*` control API to OpenJabNab; XMPP
(`:5222`) is served by OpenJabNab directly.

## What you can do from Home Assistant

- 🐰 Wiggle the **ears** to preset positions / choreographies
- 🌈 Set the **LED** color (e.g. flash on the Farfisa intercom)
- 🔊 Play a **sound / MP3** (incl. local HA-generated speech)
- 🗣️ **Speak** (see the TTS note — local HA TTS recommended)
- 📟 (v2) React to **button** presses and **RFID** tags *(read-back support is
  WIP — see the add-on docs)*

## Repository layout

```
.
├── repository.yaml            # declares this as an HA add-on repository
├── nabaztag-server/           # the add-on
│   ├── config.yaml            # options, ports, arch
│   ├── build.yaml             # builds on the prebuilt OpenJabNab image (amd64)
│   ├── Dockerfile
│   ├── run.sh                 # renders persistent config, starts OpenJabNab
│   └── DOCS.md                # full install / pairing / networking / testing guide
├── home-assistant/
│   └── nabaztag.yaml          # ready-to-paste HA package (rest_commands + automations)
├── README.md
└── LICENSE
```

## Quick start

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
   add `https://github.com/ClaraVnk/nabaztag`.
2. Install **Nabaztag Server (OpenJabNab)**, set `server_address` to your HAOS
   host IP, and start it.
3. Allow the rabbit's VLAN to reach the host, point the rabbit at the server,
   and pair it.
4. Drop `home-assistant/nabaztag.yaml` into `/config/packages/`.

Full step-by-step (cross-VLAN firewall, Wi-Fi settings, traffic capture, staged
testing, troubleshooting) is in **[`nabaztag-server/DOCS.md`](nabaztag-server/DOCS.md)**.

## Hardware

- Works with the **Nabaztag** (v1, 2005) and **Nabaztag:tag** (v2, 2006 — mic +
  RFID). This project targets the **stock** rabbit; it does **not** use pynab.
- Host architecture: **amd64**.

## TTS note

OpenJabNab's built-in TTS relied on Acapela's now-dead web service. For reliable,
fully-local speech, generate audio with Home Assistant's local TTS (e.g. Piper)
and play the resulting MP3 on the rabbit via the `playurl` API. See the docs.

## Credits

- [OpenJabNab](https://github.com/OpenJabNab/OpenJabNab) — the PHP/C++ Violet
  protocol reimplementation this add-on runs.
- [antoine-aumjaud/docker-openjabnab](https://github.com/antoine-aumjaud/docker-openjabnab)
  and [fbricon/openjabnab-docker](https://github.com/fbricon/openjabnab-docker) —
  the prebuilt Docker image this add-on layers on.
- The Nabaztag community boot-process write-ups (wizz.cc) that documented the
  `/vl/bc.jsp` → `/vl/locate.jsp` → ping/broad/XMPP sequence.

Images: Home Assistant logo © the Home Assistant project. Nabaztag photo by
docraven (Flickr), via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Nabaztag.jpg),
licensed [CC BY-SA 2.0](https://creativecommons.org/licenses/by-sa/2.0).

## License

[Apache License 2.0](LICENSE) — the same license as Home Assistant.
