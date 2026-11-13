# Nabi firmware — custom mic-streaming bytecode (Phase 3/4)

The stock Nabaztag firmware only records the microphone on a **physical button
press**; the server cannot start a recording. A hands-free wake word ("hey Nabi")
therefore needs a custom bytecode that streams the mic continuously. This folder
holds the **reverse-engineering add-on** to the stock Violet bytecode that does
exactly that — without adopting a whole different firmware/server stack.

## What it adds

`micstream.mtl` adds server-controllable continuous capture: it streams 8 kHz
audio to the server as `snd`-prefixed UDP datagrams (the same wire shape openab
uses), started/stopped by two new `MessagePacket` program commands wired into
`runEvalOneCommand`:

- `RS <ip> [port]` — start streaming the mic to `<ip>:<port>` (default 4000)
- `RT` — stop streaming

Cross-subnet works because the Violet `arpreq` already resolves the **gateway**
MAC for non-local IPs (`arp.mtl`: `if subnet ip then ip else netgateway`); we
resolve it once at start and reuse it for every datagram.

## How it's built

Two things are needed, and nothing else: this repository and OpenJabNab's copy
of Violet's runtime sources (GPL, not vendored here).

```sh
git clone https://github.com/OpenJabNab/OpenJabNab
git -C OpenJabNab checkout 640257f3cef63fb428d80b8c171f3b15d17ab0ed
./bytecode/build-bytecode.sh OpenJabNab/bootcode
```

`build-bytecode.sh` copies our modules into `sources/`, applies every
`patch_*.py` in order (each one idempotent, each one failing loudly if its
anchor moved), preprocesses with OpenJabNab's `preproc.pl` and compiles with
this repository's Python Metal compiler, `firmware-arm/tools/mtl_comp.py` --
byte-identical to RedoXyde's C++ `mtl_compiler` and aware of the opcodes
Naboot adds, so no 32-bit toolchain and no container are needed: perl and
python3. From that commit the result is reproducible: 114 082 bytes for the
tree of 2026-11-11.

What comes out expects a Naboot firmware (the TLS, OTA and adoption-key paths
call opcodes a stock rabbit does not have; they stay dormant there) and a
warren that speaks the in-session records. `nabaztag-violet/bootcode.hybrid`
in this repository is an older build, without TLS, kept for the Home
Assistant add-on and the emulator's default run.

## Why the rabbit no longer reboots when the server restarts

Stock `main.mtl` reboots the rabbit when the XMPP socket closes — the comment
says so, and says the authors considered reconnecting instead and chose the
reboot as "the most effective". On a server that never went away, it was.

Measured on real hardware (2026-10-20), on a warren that redeploys: the drop
is at 17:12:12, the rabbit re-fetches its 103 KB of bytecode at **17:12:25**,
and then stops — it never even asks for `locate.jsp`. It stays orange until
someone unplugs it. A soft `reboot` does not bring the 2006 rt2501 USB radio
back; a power cycle does.

`patch_reconnect.py` routes the closed-socket case to `XmppSessionIsDisconnected`
instead: stop what we are doing, reset to idle, base LED violet, wait 5 s plus a
random 0-30 s (so a whole warren does not stampede back at once), then
`XmppGateReconnect`. The network stack is never torn down, so there is nothing
to bring back. That path is not new code — Violet already uses it for BOSH, for
a server-closed stream and for stanza errors; this only routes the TCP case
there too.

It ships in the **bytecode**, which the rabbit downloads at every boot, so it
needs no flashing and carries no brick risk: build, serve, power-cycle once.

The compiled `*.bin` in this directory is **not committed**. The copy the
add-on actually ships *is*: `nabaztag-violet/bootcode.hybrid`, checksum-verified
by `nabaztag-violet/Dockerfile`. After a rebuild, copy the new output there and
update that checksum in the same commit — the Docker build and the CI
consistency job both fail otherwise. (The stock Violet/OpenJabNab bytecodes
stay out of the repo; see `nabaztag-violet/DOCS.md`, *Bytecodes*.)

## Status / next

- ✅ Toolchain builds; stock Violet bytecode rebuilds from source (~103 KB).
- ✅ Hybrid compiles (`startmicstream`/`cbrecstream`/`stopmicstream` verified in
  the output).
- ✅ mDNS **resolver** (`mdnsresolve.mtl` + `patch_dns.py`): `dnsreq` routes
  `.local` xmpp domains to the 224.0.0.251:5353 multicast group instead of the
  gateway DNS, so a rabbit whose gateway drops its unicast DNS still resolves its
  server (paired with locate returning a `.local` name + an mDNS responder on the
  HA host). Compiles clean → 103 779 B (+208 B vs the unpatched hybrid). The
  announcer (`mdns.mtl`, publishes the rabbit) is the opposite direction.
- ✅ TLS 1.3 PSK on the XMPP socket (`patch_tls.py`) for a rabbit whose owner
  confirmed its adoption key, and the RFID report inside that session instead
  of plain `rfid.jsp` when the warren asks for it (`patch_rfid_xmpp.py`). See `../firmware-arm/docs/ADOPTION-KEY.md`.
- ✅ What the rabbit plays comes down that session too (`patch_res_in_session.py`):
  an announce record, the raw bytes, then `ST res:N`, instead of an
  `ST http://…/res/<token>` anyone on the path can fetch, when the warren flips
  `xmpp-res:0` under the HMAC.
- ✅ A radio or a podcast comes down that session as well, and plays as it
  arrives (`patch_live_in_session.py`): `ST live:N`, then the rabbit asks the
  warren for the bytes its player has room for, 49 152 at most in hand, and
  the sound starts after 16 384. Instead of `ST http://…/pod/<token>` over
  plain HTTP, when the warren flips `xmpp-live:0` under the HMAC. Proven in
  the emulator (`../firmware-arm/emulator/scenarios/vm/xmpp-live.txt`), not
  yet on a rabbit.
- ⏳ Serve the hybrid from the add-on, add a **UDP/4000 receiver** + an on-server
  wake-word engine (openWakeWord, "hey Nabi") that hands the utterance to the
  existing STT → conversation-agent → TTS loop. Live-test on the device (a bad
  bytecode just means a reboot — recoverable by reverting the served bytecode).
