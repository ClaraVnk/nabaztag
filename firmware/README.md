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

The Metal toolchain only builds on Linux x86 (`-m32`), so it's done in a Docker
container (here, `nabi-build` on the HAOS host):

```sh
# deps
apt-get install -y git build-essential gcc-multilib g++-multilib flex bison perl python3

# 1) the Metal compiler (RedoXyde/mtl_linux) — note: OpenJabNab's bundled mtl_comp
#    segfaults because its Makefile builds 64-bit; this one is -m32.
git clone https://github.com/RedoXyde/mtl_linux && (cd mtl_linux && make)

# 2) the stock Violet bytecode sources come from the OpenJabNab image
#    (/var/www/OpenJabNab/bootcode). Drop micstream.mtl + mdnsresolve.mtl into
#    its sources/, then patch main.mtl (#include + RS/RT/SV/FW + mdns announcer)
#    and dns.mtl (route ".local" names over multicast mDNS):
cp micstream.mtl mdnsresolve.mtl <bootcode>/sources/
python3 patch_main.py <bootcode>/sources/main.mtl
python3 patch_dns.py  <bootcode>/sources/dns.mtl

# 3) preprocess + compile
cd <bootcode>/sources && ../preproc.pl < main.mtl | ../preproc_remove_extra_protos.pl > ../bootcode.mtl
# NB: use RedoXyde/mtl_linux's compiler (-m32). The bundled <bootcode>/compiler/
# mtl_comp is the 64-bit build and SEGFAULTS. The full hybrid pulls in fwota.mtl,
# which uses the custom `verifySig` opcode (152) — so use the *patched* compiler
# (mtl_compiler_patched); the plain mtl_compiler errors "unknown label verifySig".
mtl_linux/mtl_compiler_patched -s <bootcode>/bootcode.mtl bootcode_hybrid.bin
```

**One command for steps 2–3:** `./build-bytecode.sh <bootcode-dir>` copies all
our modules, applies `patch_main.py` + `patch_dns.py`, and compiles with the
patched compiler — so a rebuild can never silently drop a module (the mDNS
resolver in particular). Hardware-verified: the full hybrid (104 827 B — resolver
+ announcer + RS/RT/SV + OTA) boots, resolves over mDNS, binds XMPP, broadcasts
`naboot.local`, and speaks. The FW/OTA path only *runs* on Naboot firmware.

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
- ⏳ Serve the hybrid from the add-on, add a **UDP/4000 receiver** + an on-server
  wake-word engine (openWakeWord, "hey Nabi") that hands the utterance to the
  existing STT → conversation-agent → TTS loop. Live-test on the device (a bad
  bytecode just means a reboot — recoverable by reverting the served bytecode).
