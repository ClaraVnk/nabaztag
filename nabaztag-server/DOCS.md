# Nabaztag Server (OpenJabNab) — Add-on documentation

This add-on runs an [OpenJabNab](https://github.com/OpenJabNab/OpenJabNab) server
inside Home Assistant OS. An **original Nabaztag / Nabaztag:tag** (the 2005–2006
Wi-Fi rabbit by Violet/Mindscape) is a "dumb" Wi-Fi client: on boot it phones
home over plain HTTP to a hard-coded server. The original Violet servers died
around 2011, so we run a replacement locally and point the rabbit at it. Home
Assistant then drives the rabbit (ears, LEDs, sounds, TTS) through OpenJabNab's
HTTP API.

> **Placeholders:** replace `<HAOS_IP>` with your Home Assistant host's IP,
> `<RABBIT_IP>` with the rabbit's IP, `<NABAZTAG_VLAN>` with the rabbit's
> subnet, and `<MAC>` with the rabbit's MAC (lowercase, no colons). Keep these
> real values only in the add-on options and your HA `secrets.yaml` — never in a
> committed file.

> Scope: this is for a **stock** rabbit (no tagtagtag/pynab card inside). If the
> rabbit has a pynab card, that is a different project.

## How it works

```
Nabaztag (<RABBIT_IP>)  ──HTTP /vl/* :80──▶  ┌───────────────────────────┐
       ▲                ◀──XMPP push :5222─  │  Add-on @ <HAOS_IP>        │
       │                                      │  Apache :80  ─┐           │
Home Assistant  ──HTTP /ojn_api/* :80──▶      │   proxy ──▶ OpenJabNab    │
                                              │             127.0.0.1:8080│
                                              └───────────────────────────┘
```

- The rabbit fetches its bootcode (`GET /vl/bc.jsp`) and then `GET /vl/locate.jsp`,
  which returns the ping/broadcast/XMPP server addresses. It then connects to
  XMPP (`:5222`) and waits for **pushed** commands (low latency).
- **OpenJabNab's HTTP listener binds `127.0.0.1` only** (hard-coded
  `QHostAddress::LocalHost` upstream), so **Apache** listens on `0.0.0.0:80` and
  reverse-proxies `/vl/*` and `/ojn_api/*` to OpenJabNab on `127.0.0.1:8080`. It
  also serves the admin UI at `/ojn_admin/`.
- OpenJabNab's **XMPP listener binds `0.0.0.0:5222`**, so the rabbit reaches it
  directly — no proxy needed there.

## Requirements

- Home Assistant OS, **amd64** host.
- An original Nabaztag (v1) or Nabaztag:tag (v2). v2 adds mic + RFID.
- The rabbit must be able to reach the HAOS host — see **Cross-VLAN** below.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/ClaraVnk/nabaztag`.
2. Install **Nabaztag Server (OpenJabNab)**.
3. Set options (below), then **Start**. Check the log for
   `starting OpenJabNab — HTTP 127.0.0.1:8080 (proxied on :80 by Apache), XMPP :5222`.

## Options

| Option | Default | Description |
|---|---|---|
| `server_address` | `192.0.2.10` *(placeholder)* | The address OpenJabNab tells the rabbit to use. **Set this to your HAOS host IP** (`<HAOS_IP>`), reachable from the rabbit. |
| `log_level` | `Info` | `Debug` while pairing, then lower it. |
| `auth_bypass` | `true` | Local, no-token API (fine on a trusted LAN). Set `false` to require a token. |

> Keep the **host ports equal to the container ports** (80, 5222). The server
> advertises itself on the standard ports, so remapping the host side would
> break the rabbit. If host `:80` is already taken on your HAOS host (e.g. an
> NGINX add-on), free it first.

## Cross-VLAN reachability (do this first)

If the rabbit is on a different subnet than HAOS, the UniFi VLANs are isolated by
default. **Allow the rabbit's VLAN to reach the HAOS host** on:

- `TCP 80` (rabbit protocol + API)
- `TCP 5222` (XMPP push)
- `TCP 8123` (only if you use HA-served MP3 for TTS — see TTS note)

This is a firewall change — **confirm before applying**. In UniFi: Settings →
Firewall & Security → create a rule allowing `<NABAZTAG_VLAN> → <HAOS_IP>` on the
ports above.

## Point the rabbit at the server

**Method A — configure directly in the rabbit (chosen approach).** When the
rabbit cannot reach a Violet server, hold its head button to enter config mode;
it exposes a small config page (and/or accepts the server via the original
Violet config). Set the **server / "Violet platform"** address to `<HAOS_IP>`.
The rabbit speaks plain HTTP on port 80.

- If the firmware only accepts a **hostname** (not a bare IP), use Method B.

**Method B — DNS override (fallback).** Leave the rabbit on its default and, in
UniFi, override `r.nabaztag.com` → `<HAOS_IP>`. One rule covers boot + ping +
broadcast + XMPP because the server re-advertises `server_address`.

## Wi-Fi settings for the rabbit

The v2 is 802.11b/g. Create a dedicated 2.4 GHz IoT SSID:

- **WPA2 only** (no WPA3/mixed), **PMF disabled**
- **2.4 GHz**, **fixed channel** (1/6/11), **20 MHz** width
- **Band steering OFF**, **802.11r OFF**, min data rate auto
- Simple SSID name (no spaces/emoji)
- If the firmware turns out to be **WEP-only**, create a temporary WEP SSID just
  for pairing.

## Find the real hostname + MAC + serial (network capture)

Useful to confirm what the rabbit actually calls, and to get the `<MAC>` you need
for the API / HA.

- **iPhone MITM/firewall:** share the iPhone hotspot, connect the rabbit, and
  watch the first DNS lookup plus `GET /vl/bc.jsp?...m=<MAC>...` and
  `GET /vl/locate.jsp?...sn=<serial>...`.
- **tcpdump on HAOS:** `tcpdump -i any -n host <RABBIT_IP> and tcp port 80`.

Record the **MAC** from `m=` (lowercase, no colons) — that is your `<MAC>` in
`/ojn_api/bunny/<MAC>/...` and in the HA secrets.

## Staged testing

1. **Server up:** add-on log shows it started; admin UI at
   `http://<HAOS_IP>/ojn_admin/` responds. Inside the container,
   `curl http://127.0.0.1:8080/ojn_api/plugin/stats/getbunniesname` should answer
   (a bare `/ojn_api/` with no command may return empty — that's fine).
2. **Rabbit connects:** power the rabbit; the add-on log shows `GET /vl/bc.jsp`
   then `/vl/locate.jsp`, and the rabbit registers. Verify with
   `curl "http://<HAOS_IP>/ojn_api/plugin/stats/getbunniesname"`. If Apache logs
   a 404 for a path other than `/vl/*` or `/ojn_api/*`, add a matching
   `ProxyPass` rule (report it and I'll update `run.sh`).
3. **Manual command:**
   - Ears: `curl "http://<HAOS_IP>/ojn_api/bunny/<MAC>/ears/setFriend?id=1"`
   - LED:  `curl "http://<HAOS_IP>/ojn_api/bunny/<MAC>/colorbreathing/setColor?name=blue"`
   - Sound:`curl "http://<HAOS_IP>/ojn_api/bunny/<MAC>/webradio/playurl?url=<mp3-url>"`
4. **From HA:** trigger the `rest_command`s in the Home Assistant package.

## TTS note (important)

OpenJabNab's built-in TTS used **Acapela's web service, which is dead**, so
`tts/say` may produce no audio. The robust, 100%-local alternative:

1. Generate speech with **HA's local TTS** (e.g. Piper) into an MP3 served by HA.
2. Play it on the rabbit with `webradio/playurl?url=http://<HAOS_IP>:8123/local/...mp3`.

Ears, LEDs, sounds and MP3 playback are unaffected by the Acapela issue.

## Persistence

Everything mutable lives in `/data` (rendered `openjabnab.ini`, `openjabnab.log`,
and `state/`), so config and pairing survive restarts and add-on updates. The
exact state sub-directories are best confirmed from the first-run logs.

## Troubleshooting

- **Rabbit never appears in the log:** check the cross-VLAN firewall rule; confirm
  the rabbit points at `<HAOS_IP>`; confirm host `:80` is free; capture its
  traffic to see what it actually calls.
- **Connection refused on :80:** OpenJabNab binds localhost only — make sure
  Apache is up (`ss -tlnp` should show `:80` on `0.0.0.0`, OpenJabNab on
  `127.0.0.1:8080`) and the proxy modules are enabled.
- **Host :80 already used:** another add-on owns it → free it, or use the DNS
  method.
- **TTS silent:** expected — use the HA-TTS-to-MP3 path above.
- **Pairing lost after update:** verify the `state/` relocation in `run.sh`
  matches OpenJabNab's actual data dirs (adjust the dir list if needed).
