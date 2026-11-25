# Repository layout

What lives where.

```
.
├── firmware-arm/            # Naboot — custom ARM7 firmware, 1.2.0.0. Forks
│   ├── apply-mods.py        #    RedoXyde/nabgcc wpa2: WPA3-SAE, the adoption
│   ├── boot-mods/           #    key, HMAC bytecode, TLS 1.3 PSK, Ed25519-gated
│   ├── crypto/              #    OTA, modernized config UI, mDNS, crash report.
│   ├── pages/               #    Build via ./build.sh --remote HOST --runtime
│   │                        #    {docker,podman} --mode naboot-1.2.0.0 (or
│   │                        #    minimal, lean, …).
│   ├── releases/            #    the signed .sim images + their sha256 (README.md)
│   ├── jtag/                #    ⭐ The JTAG station: a Raspberry Pi as the probe.
│   │                        #    station.sh setup|check|flash|listen|dump
│   ├── emulator/            #    A rabbit that is not a rabbit: the Wi-Fi stack
│   │                        #    run on a build machine against a virtual AP,
│   │                        #    so a fix costs a second instead of unplugging
│   │                        #    the rabbit. `cd firmware-arm/emulator && ./run.sh`
│   ├── vendor/              #    RedoXyde's nabgcc + mtl_linux at the pinned
│   │                        #    commits: the build reads this, not GitHub.
│   └── docs/
│       ├── ADOPTION-KEY.md  #    The per-rabbit key: exchange, colours, HMAC, TLS.
│       ├── SECURITY-FINDINGS-2026-11.md # The review: fixed, measured, left open.
│       ├── OTA-ANTIBRICK.md #    What a flash can and cannot survive, measured.
│       ├── VIOLET-*.txt     #    Violet's original code, mapped and audited (FR).
│       ├── BRICK_FORENSICS.md #  Recovery plan + bisection ladder.
│       └── RECOVERY.md
├── bytecode/                # Runtime-side bytecode add-ons (mic stream, mDNS,
│   ├── micstream.mtl        #    OTA, reconnect, the TLS seam) compiled into the
│   ├── fwota.mtl            #    hybrid bootcode served by the add-on / warren.
│   ├── patch_tls.py
│   └── patch_main.py
│                            # (Le Terrier — the public warren at
│                            #  terrier.cyberloutre.fr — lives in a separate
│                            #  private repo; the live site is the showcase.
│                            #  The HACS integration is ClaraVnk/terrier-ha.)
├── mirror/                  # Violet's Mir:ror (USB RFID reader) as a second
│   ├── mirror.py            #    tag reader for a rabbit on Le Terrier: a tag
│   ├── Containerfile        #    on it does what it does on the rabbit's nose.
│   └── README.md            #    Stdlib Python, Alpine image, Quadlet unit.
├── docs/
│   └── HISTORY.md           # the phase-by-phase journal (the former Roadmap)
├── repository.yaml          # declares this as an HA add-on repository
├── nabaztag-violet/         # the Home Assistant add-on — our own Violet-protocol server
│   ├── server.py            #    dependency-free Python (HTTP boot + XMPP + control API)
│   ├── config.yaml          #    options (server_address, log_level), ports, arch
│   ├── Dockerfile           #    installs python3, fetches the bytecode at build
│   ├── build.yaml
│   └── DOCS.md              #    install / pairing / API / status
├── legacy/
│   └── nabaztag-server/     # the first OpenJabNab-based add-on — superseded by
│                            #    nabaztag-violet, no longer offered in the store,
│                            #    kept for reference only
├── home-assistant/          # packages for the add-on's REST control API
│   ├── nabaztag.yaml        # ready-to-paste HA package (rest_commands + scripts)
│   ├── ambient.yaml         # ambient automations (belly/ears/nose) over the REST API
│   ├── entities.yaml        # optional dashboard controls (sleep/nose/ears/belly) over REST
│   ├── proactive.yaml       # Nabi "alive" — proactive companion (quips, briefing, notify)
│   ├── nabaztag_tasks.yaml  # standing orders ("préviens-moi quand…") via Claude tools
│   └── rfid.yaml            # RFID tag → action (remembers last tag; dispatches known ones)
├── README.md
└── LICENSE
```
