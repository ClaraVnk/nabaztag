# What it does

Everything the project does, in detail. The short version is the [README](../README.md).

- 🐰 **Naboot 1.2.1.4 — config mode without unplugging**: three short
  presses of the head open it, on its server or offline, and it closes by
  itself after ten minutes with nobody on the setup page. On the Terrier,
  the rabbit hears its head and its tags while asleep (a double click wakes
  it), and a Restart button reloads its software without unplugging.
  On top of 1.2.1.2: the WPA2 handshake replies declare their true length
  (a Livebox 4 drops the rest), and an access point offering WPA and WPA2
  together is taken for WPA2. And 1.2.1.0's: a reset sends the rabbit back
  to the Terrier, a head press lets you change the server, a captive setup
  page, a purple blink when there is no server, and a diagnostic line.
- 🔒 **Since 1.2.0.1 — HTTPS on the rabbit itself**: it boots for the very
  first time over HTTPS (ISRG Root X2 built in), its key exchange included
  (1.2.0.1 gives it the 18 s a real ARM7 needs, and the boot URL says how it
  went), and its owner can always flash something else, unsigned: start it
  head pressed, press again during the send. The runtime that also opens
  radios and sounds over HTTPS by itself is held back until its own update
  path is proven on hardware (2026-11-13): updates go through the warren.
- 🔧 **Under it, everything Naboot already did** (every item proven on a
  real rabbit): **WPA3-SAE** in constant time (and H2E, in beta), a per-rabbit
  **adoption key** (X25519, confirmed by five belly colours), **HMAC-signed
  bytecode**, **TLS 1.3** for XMPP and for the boot itself, **binary OTA**
  pushed from the warren's page with the rabbit restarting by itself, a crash
  report in the boot URL, and a modern config UI.
- 🛡️ **And the fixes of a line-by-line security review** of Violet's 2006
  code, RedoXyde's port and ours, each finding reproduced before it was fixed
  ([`SECURITY-FINDINGS-2026-11.md`](../firmware-arm/docs/SECURITY-FINDINGS-2026-11.md)):
  two out-of-bounds bugs twenty years old in the beacon parser and the USB
  descriptors, the adoption key no longer readable by a bytecode, an OTA that
  only flashes an image whose signature was checked, a flash writer that no
  longer returns into half-written code
  ([`OTA-ANTIBRICK.md`](../firmware-arm/docs/OTA-ANTIBRICK.md)). 15 KB of flash
  were taken back from dead code (WEP, the factory test mode, the software
  codecs, pairwise TKIP) to make room for HTTPS. First tried as the
  1.1.0.0 beta, now in the release.
- 🌍 **[Le Terrier](https://terrier.cyberloutre.fr/)** — a public "warren" so
  anyone can adopt a rabbit without running their own server. Violet's
  services with the 2006 voices (clock, weather, alarm, Ztamps, rabbits in
  love), and what OpenJabNab offered and more: reminders -- a treatment
  reminder waits for a press on the head and warns a relative otherwise --,
  the morning agenda from a private iCal address, birthdays, the name day,
  a bad joke a day, a bedtime story, weather warnings and pollen on the
  belly, and the next departures from any French network or Swiss stop
  (open data, live where published). Answers from the owner's own AI, a
  Home Assistant integration, and a token API for every setting. And the
  **Nabagram**: owners share photos of their rabbits, open to members whose
  rabbit is adopted, each photo reviewed before it shows.
- 🧩 **[The HACS integration](https://github.com/ClaraVnk/terrier-ha)** — the
  rabbit as proper Home Assistant **devices and entities** (a light, a switch,
  selects, sensors, buttons) rather than REST calls. It works against Le
  Terrier or against a warren you run yourself, and it is where this project's
  Home Assistant work happens now.
- 📡 **Survives a hostile network** — a tiny firmware mDNS resolver lets the rabbit
  find its server over multicast even when the gateway refuses its unicast DNS.
- 🔁 **Comes back by itself** — restart the server and the rabbit reconnects in
  seconds, base LED violet; lose the access point and it notices and rejoins on
  its own. The old "unplug it" advice is mostly history.
- 🛠️ **A JTAG station in one command** — a Raspberry Pi and five jumpers, no
  probe to buy. `git clone` this repo on the Pi, then
  `firmware-arm/jtag/station.sh setup|check|flash|listen|dump`: un-brick a
  rabbit, write an image and read it back, dump what it *really* runs, or
  watch its live status line without halting it. See
  [`firmware-arm/jtag/README.md`](../firmware-arm/jtag/README.md).
- 🗣️ **Talk to Claude through the rabbit** — hold the head button, ask, and it
  answers in its own voice while moving its ears + LEDs. Hands-free **"hey Nabi"
  wake word is in beta** — opt-in and off by default (see the privacy note below).
- 🐰 **Or nothing flashed at all** — with the [Home Assistant
  add-on](ADDON.md), a stock rabbit with **zero
  hardware mods** just downloads its bytecode from the add-on on boot.
- 🔒 **100&nbsp;% local with the add-on** — bundled whisper.cpp STT + Piper /
  espeak-ng TTS. No cloud, no account, no telemetry.
- 🧩 **Driven from Home Assistant through the add-on** — speak text, play audio,
  original Violet jingles, ear choreographies, RGB light shows, weather / mail /
  air belly icons; button / RFID / ear-move events come back as HA events for
  your automations.

> ✅ **It works end-to-end — verified live on a real Nabaztag:tag&nbsp;v2.** The
> custom **Naboot** firmware (1.2.0.0) adds WPA3, a per-rabbit adoption key,
> signed bytecode, TLS and HTTPS, and over-the-air updates; **Le Terrier** is the public
> warren. A stock rabbit is a "dumb" Wi-Fi client that downloads its bytecode on
> every boot (nothing flashed by default) and talks plain HTTP + XMPP. The rabbit
> boots, breathes, speaks, plays jingles, moves its ears, lights up, listens
> hands-free for "hey Nabi", asks **Claude** and answers. The
> **[Roadmap](ROADMAP.md)** below says what is done and what comes next; the
> phase-by-phase story is in [`docs/HISTORY.md`](HISTORY.md).
