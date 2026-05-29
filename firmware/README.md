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
#    (/var/www/OpenJabNab/bootcode). Drop micstream.mtl into its sources/,
#    then patch main.mtl to #include it + add the RS/RT commands:
cp micstream.mtl <bootcode>/sources/
python3 patch_main.py <bootcode>/sources/main.mtl

# 3) preprocess + compile
cd <bootcode>/sources && ../preproc.pl < main.mtl | ../preproc_remove_extra_protos.pl > ../bootcode.mtl
mtl_linux/mtl_compiler -s <bootcode>/bootcode.mtl bootcode_hybrid.bin
```

The compiled `*.bin` is **not committed** (it's derived from reverse-engineered
proprietary firmware — like the stock bootcodes, it's built/served locally, not
redistributed).

## Status / next

- ✅ Toolchain builds; stock Violet bytecode rebuilds from source (~103 KB).
- ✅ Hybrid compiles (`startmicstream`/`cbrecstream`/`stopmicstream` verified in
  the output).
- ⏳ Serve the hybrid from the add-on, add a **UDP/4000 receiver** + an on-server
  wake-word engine (openWakeWord, "hey Nabi") that hands the utterance to the
  existing STT → conversation-agent → TTS loop. Live-test on the device (a bad
  bytecode just means a reboot — recoverable by reverting the served bytecode).
