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
3. Install **Nabaztag Violet Server**, set `server_address` to **your HAOS host
   IP** (the address the rabbit must reach), then **Start**.

## Point the rabbit at it

Hold the rabbit's head while powering it (LEDs go blue), join its `NabaztagXX`
Wi-Fi, open `192.168.0.1`, set the **Violet Platform** field to
`http://<HAOS_IP>/vl`, then *update and start*. Watch the add-on log: you should
see `serving bootcode`, then the XMPP `stream → success → bind → session` and
finally `bound and idle — ready for commands`.

## Control API

- `GET /api/status` — connected rabbits.
- `GET /api/led?id=2&r=0&g=238&b=0` — set an LED (ids 0=bottom,1=left,2=middle,3=right,4=nose).
- `GET /api/ears?ear=1&angle=20&dir=0` — move an ear.
- `GET /api/ping` / `GET /api/reboot`.
- `GET /api/raw?b64=<base64>` — inject a raw violet packet (for RE/experiments).

If only one rabbit is connected, `mac` is optional; otherwise pass `?mac=...`.

## Status / honesty

- **Boot + XMPP connect are implemented and tested** (against a simulated rabbit).
  The big milestone — the rabbit booting our bytecode and connecting to our own
  server — should work on first try.
- **The binary command payloads (LED/ears/sound) are best-effort.** The packet
  *framing* is correct (`0x7F type len(3) payload 0xFF`, base64 in
  `<packet xmlns='violet:packet'>`), but the exact opcode layout still needs
  validation on the real rabbit — use `/api/raw` + the logs to refine. This is
  Phase 1; the mic and richer commands come next.
