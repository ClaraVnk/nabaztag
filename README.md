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
  <img src="https://img.shields.io/badge/status-talks·moves·listens→Claude%20(live)-success" alt="Talks, moves, listens, talks to Claude — verified live">
</p>

Bring an **original Nabaztag** (the 2005–2006 Wi-Fi rabbit by Violet/Mindscape)
back to life and drive it from **Home Assistant**, fully locally — no cloud.

This repository is a **Home Assistant OS add-on repository**. Its primary add-on
is a small, **dependency-free server that speaks the original Violet protocol**
directly: the rabbit phones home to it (the real Violet servers died around
2011), and Home Assistant drives the rabbit — ears, belly weather icons, nose —
through a simple control API.

> ✅ **It works end-to-end.** A stock rabbit is a "dumb" Wi-Fi client that
> downloads its bytecode from the server on every boot (nothing is flashed by
> default) and talks plain HTTP + XMPP (no TLS pinning). Phases 1, 2, 3 and 6
> are all working live: the rabbit boots, breathes, speaks, plays jingles,
> moves its ears, lights up — and listens hands-free for "hey Nabi", asks
> Claude, and answers. The custom firmware (Naboot, Phase 4) adds Ed25519-gated
> OTA and a modernized config UI; Le Terrier (Phase 6) is the public warren
> live at <https://terrier.cyberloutre.fr/>. See Roadmap for the current
> status of each phase.

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
- 📟 (v2) react to **button** presses, **RFID/Ztamp** tags and **ears moved by
  hand** — re-emitted as `nabaztag_event` **Home Assistant events** to trigger
  automations (Nabi as an input device)

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
├── terrier/                 # Le Terrier — public warren (Phase 6, live at
│   ├── server.py            #    terrier.cyberloutre.fr). Same Violet protocol as the
│   ├── ui.py                #    add-on but multi-tenant, with an owner Web UI,
│   ├── xmpp.py / protocol.py #    SQLite for per-MAC state, Caddy+Podman deploy.
│   ├── deploy/              #    Caddy / Quadlet / OVH firewall snippets.
│   └── SPEC.md
├── firmware-arm/            # Naboot — custom ARM7 firmware (Phase 4/5). Forks
│   ├── apply-mods.py        #    RedoXyde/nabgcc wpa2 + Ed25519-gated httpflash +
│   ├── boot-mods/mdns.mtl   #    modernized config UI + boot-side mDNS announcer.
│   ├── crypto/              #    Build via ./build.sh --remote HOST --runtime
│   ├── pages/               #    {docker,podman} --mode {minimal|full|max|lean|…}.
│   ├── BRICK_FORENSICS.md   #    Recovery plan + bisection ladder.
│   └── RECOVERY.md
├── firmware/                # Runtime-side bytecode add-ons (mic stream, mDNS,
│   ├── micstream.mtl        #    OTA helper) compiled into the hybrid bootcode
│   ├── mdns.mtl             #    served by the add-on.
│   └── patch_main.py
├── home-assistant/
│   ├── nabaztag.yaml        # ready-to-paste HA package (rest_commands + scripts)
│   ├── ambient.yaml         # ambient automations (belly/ears/nose) over the REST API
│   ├── entities.yaml        # optional dashboard controls (sleep/nose/ears/belly) over REST
│   └── rfid.yaml            # RFID tag → action (remembers last tag; dispatches known ones)
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
- **Phase 2 — voice → Claude — working (verified live), no firmware hacking:**
  the stock bytecode already does **push-to-talk** — hold the head button, speak,
  and it records the mic and POSTs the audio to `/vl/record.jsp`. The add-on
  decodes it, runs **bundled whisper.cpp** for speech-to-text, sends the text to a
  **Home Assistant conversation agent** (e.g. the Anthropic/Claude integration),
  and speaks the reply back. The agent can also **drive the rabbit** by embedding
  `[ears …]` / `[led …]` / `[nose …]` tags in its reply. TTS is bundled **espeak-ng**
  or, for a much nicer voice, **Piper** via the HA Piper add-on. Enable it with the
  `voice_pipeline` / `conversation_agent` / `tts_engine` options.
- **Phase 3 — wake word — working (verified live):** push-to-talk needs no
  firmware change; a hands-free wake word ("hey Nabi") does. The stock firmware
  only records the mic on a physical button press — the server can't start a
  recording. So passive listening required a **custom mic-streaming bytecode**.
  Solution: a *hybrid* bytecode that adds a server-triggered **UDP microphone
  stream** (`firmware/micstream.mtl`, à la `openab`) to the stock Violet
  bytecode, compiled with RedoXyde's `mtl_linux` in a Linux x86 container.
  Server-side `wake_loop` swaps the audio buffer every few seconds, runs bundled
  whisper.cpp, and if "Nabi" is heard, the rest of the utterance is sent to the
  conversation agent and the reply spoken back. Confirmed live: "Nabi, raconte
  une blague" → Claude reply spoken full-voice in the Piper voice with action
  tags driving ears + LEDs simultaneously.
- **Phase 4 — firmware upgrades — done (Naboot, not yet hardware-verified on
  the new build):** with the toolchain stood up, built **Naboot**, a fork of
  `RedoXyde/nabgcc` (wpa2 branch) compiled as a `.sim` flashable via the
  config-mode upload page (no JTAG / no opening). Adds an **Ed25519-gated OTA**
  path (`verifySig` opcode 152 + `flashFirmware` gated on signature against
  `firmware-arm/keys/signing_pubkey.h`), strips the upstream debug `printf`
  glue, and wraps the linker script's vector / startup / bytecode sections in
  `KEEP()` so `--gc-sections` is safe to enable. Build is reproducible from
  the pinned upstream commits (`nabgcc` `2c05b53f`, `mtl_linux` `7e557a15`).
  Status: the rabbit is currently bricked from a Naboot-full flash that broke
  config-mode AP setup — recovery via JTAG (DollaTek J-Link V8 ordered) +
  bisection-mode flashes is documented in `firmware-arm/BRICK_FORENSICS.md`,
  with six pre-built variants ready: `minimal` (rescue, vanilla bootloader +
  verifySig only), `signed-stock` / `pages-only` / `mdns-only` (isolation
  bisects), `full` (sig-gated OTA + modernized UI), `max` (`full` + boot-side
  mDNS), `lean` (`max` + `--gc-sections`).
- **Phase 5 — setup UX — done (not yet hardware-verified):** rewrote the four
  config-mode HTML pages (`page_a / page_done / page_u / page_error`) in
  modern semantic HTML with a dark theme, mobile viewport, and the same form
  field names + template markers the firmware backend expects, so the existing
  `cbhttp` / `httpindex` / `httpflash` paths in `boot.0.0.0.13.mtl` keep
  working unchanged. Saved ~5 KB of ROM in the process. Source HTML lives
  under `firmware-arm/pages/`; injection into the boot bytecode is done by
  `firmware-arm/apply-mods.py:modernize_pages`.
- **Phase 6 — Le Terrier — DONE (live at https://terrier.cyberloutre.fr/):**
  the public warren every Naboot rabbit dials back to. Dependency-free Python
  (stdlib + Flask for the owner UI), deployed on Loutre's VPS via a rootless
  Podman quadlet behind Caddy 2 (auto-Let's-Encrypt). Reachable from any
  network: `/vl/locate.jsp` → ping/broad/xmpp_domain, `/vl/bc.jsp` →
  signed `.hybrid` baseline bytecode (103 542 B), XMPP `:5222` published
  direct (the rabbit hardware has no TLS, so Caddy stays on the HTTP/HTTPS
  layer). The owner UI is FR/EN, has Open Graph + Twitter card metadata (so
  the link previews cleanly in iMessage), and walks an owner through signup
  → claim → pair → drive in three steps. Multi-tenant per-MAC state in
  SQLite; per-owner / per-rabbit accounts; ProxyFix so HTTPS canonical URLs
  resolve cleanly behind Caddy. Not yet in the platform: per-rabbit bytecode
  minting (every paired rabbit gets the same baseline today — owner-driven
  customisation lands later) and an "orphan claim-me" bytecode that blinks
  the pair code on the belly LEDs. **Naboot is the firmware on the rabbit;
  Le Terrier is the home they all dial back to.**
- **Phase 7 — modernize the Metal toolchain in Python (two steps, pending):**
    1. **Phase 7a — Python MTL compiler:** rewrite the existing C++ `mtl_compiler`
       in Python. Same input (`.mtl` source), same output (the rabbit's
       bytecode), zero behavioral change for the device. Win: no more aging
       C++ toolchain (g++-multilib, build dance), one fewer barrier for
       contributors, easier to add new opcodes / sanity checks / linting.
       Grammar is small (see `DT_metal_03_01_13_grammaire.pdf`).
    2. **Phase 7b — "view as Python" layer:** on top of 7a, add a bidirectional
       MTL ↔ Python source mapper so people can read, write and review bytecode
       logic in familiar Python syntax. The rabbit still executes MTL bytecode;
       the Python is purely a contributor-facing surface. Doable mechanically,
       not research.
- **Phase 8 — shrink the firmware by moving boot-bytecode logic to C
  (deferred):** the rabbit's bootloader is ~3000 lines of Metal bytecode
  (`mtl/boot/boot.0.0.0.13.mtl`) running on top of a tiny stack VM. Native ARM
  is roughly 3× denser than Metal bytecode, so rewriting boot-side hot paths
  (HTTP server, locate parsing, audio bootstrap) in C — and stripping the
  corresponding VM opcodes — would free 10–20 KB of ROM (the flash budget is
  124 KB and `max` already uses ~92%). **Scope clarification:** Phase 8 only
  touches the **boot bytecode**, which is already inside the `.sim` and
  already requires a flash to change. It does **NOT** touch the runtime
  bytecode (`firmware/*.mtl`), which is downloaded fresh from the server on
  every boot and stays OTA-instant. So the flash-cost trade-off is null —
  boot-side updates are flash-only either way. The real cost is the
  rewrite effort (~3000 lines + cross-validating the new C-side does
  byte-equivalent work). Deferred until we actually hit the 124 KB flash
  wall and need the headroom.

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
