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

- `GET /api/status` — connected rabbits.
- `GET /api/ears?left=0&right=14` — ear positions (0 ≈ horizontal … ~16).
- `GET /api/weather?v=storm` — belly icon (`sun|cloudy|smog|rain|snow|storm`).
- `GET /api/nose?v=1` — nose (0 none / 1 blink / 2 double-blink).
- `GET /api/sleep?on=0` — wake (`0`) or sleep (`1`).
- `GET /api/ambient?svc=8&val=1` — generic AmbientPacket (repeatable `svc`/`val`).
- `GET /api/raw?b64=<base64>` — inject a raw violet packet.

## Status

- **Connection + full boot verified on the real rabbit:** it boots our bytecode,
  does the XMPP handshake, we answer its `violet:iq:sources` query with the init
  packet, it rebinds as `idle` and **breathes** — operational.
- **Commands** are real binary `AmbientPacket`s (verified format): ears, belly
  weather/stock/mail/air-quality icons, nose, plus sleep/wake — sent from
  `net.openjabnab.platform@<domain>/services` to the rabbit's current resource.
- **Visible confirmation pending an awake rabbit:** a sleeping Nabaztag parks
  display *and* motor commands; revalidate ears/belly/nose when it's awake.
- **Not yet (Phase 2):** sound/TTS (MessagePacket) and the microphone → Claude.
