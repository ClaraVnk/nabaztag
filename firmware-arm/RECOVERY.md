# Naboot — flash + recovery guide

The `firmware-arm/` tree builds several `.sim` *modes* from the same pinned
sources (`apply-mods.py --mode …`). All embed the Ed25519 `verifySig` opcode (so
OTAs can be signature-gated); they differ in how invasive they are on the
bootloader. The ones that matter for flashing and recovery:

| Build | `Nab.bin` | Shipped? | What's modified |
|---|---|---|---|
| **lean** (stable) | 107 288 B | **yes** — `releases/naboot-lean-wpa2.signed.sim`, hardware-verified 2026-10-27 (a spoken sentence *and* both MIDI jingles, heard) | Everything in `full` **plus** the mDNS announcer *and resolver*, `--gc-sections`, the WPA2 group key, the cold-state guards, the audio half-duplex fix, the bounded hardware waits — and, since 2026-10-27, `extbss`: every `.bss.*` zeroed at boot (the EAPOL state no longer starts on whatever the SRAM held), 8 KB of scratch moved to external RAM, 10 104 bytes of user stack instead of 1 916, and seven RT2501/USB failures that used to be detected and then dropped. The build the community runs. `releases/naboot-stable.signed.sim` (113 256 B, June) is kept as the older verified fallback. |
| **minimal** (rescue) | 119 800 B | **yes** — `releases/naboot-minimal.signed.sim`, rebuilt 2026-10-23 | C side only: TweetNaCl + `nab_sig` + the `OPverifySig` opcode, plus the `.bss`/audio-FIFO fix and the EAPOL and interpreter fixes. **`boot.0.0.0.13.mtl` is byte-identical to vanilla wpa2 HEAD** — stock bootloader UI, stock `httpflash` (accepts any `.sim`, signed or not), no mDNS. The parachute. |
| **full** | 114 456 B | no — build it yourself | Everything in `minimal` **plus**: `httpflash` rejects unsigned/tampered `.sim`, 4 config-mode HTML pages modernized (dark theme, semantic HTML, mobile viewport). |
| **phase8-rom** (experimental) | 110 084 B | no | `full` + `--gc-sections` + the Phase 8 size work (portal pages moved to C-side flash ROM via an `OPpageRender` opcode, orphan helpers pruned, debug echoes removed). Smallest image of all; untested on hardware as of 2026-06-01. |

Sizes are those of the 2026-10-20 builds, except `minimal`, which was rebuilt
on 2026-10-23: the shipped one was missing the `audio_fifo_data` step and so
carried the 2026-06-20 brick itself. `./build.sh` prints the current ones and
refuses to hand you an image that fails `preflight.py`.
Bisection modes (`signed-stock`, `pages-only`, `mdns-only`, `lean-nomdns`) are
described in `apply-mods.py --help` and [BRICK_FORENSICS.md](./BRICK_FORENSICS.md).

Every build lands under `bin/<mode>/` (ignored by git), but the signed `.sim`
files we actually **ship** live in `releases/`:

```
firmware-arm/releases/
├── naboot-stable.signed.sim   ← upload this one (normal — mode `lean`, hardware-verified)
└── naboot-minimal.signed.sim  ← upload this one (rescue / rollback — mode `minimal`)
```

`full` is **not shipped**: there is no full build under `releases/`. If you
want it, build and sign it yourself with
`./build.sh --mode full --remote <user@dev-vm> --runtime podman`, which leaves
it at `bin/full/firmware0.0.0.13.signed.sim` on your own machine.

## Risk model

* **First-flash risk** is the same as the original vanilla wpa2 flash this
  rabbit already survived — the bootloader bytecode and config-mode UI are
  unchanged in `minimal`, and our additions in `full`/`lean` are surgical
  (Metal compiler validates everything; the simulator confirms the source
  compiles cleanly with no unresolved labels).
* **Brick scenario:** a corrupted upload mid-flash could leave the rabbit
  unable to re-enter config mode. The OKI ML67Q4051 ROM bootloader recovers
  via UART pads on the PCB — which means opening the shell. That risk is
  unchanged from any flash on this device, including the original wpa2 one.

## JTAG recovery via a Raspberry Pi — no probe needed (proven 2026-06-20)

When the rabbit can't re-enter config mode (true brick), reflash over **JTAG**.
You don't need a J-Link/BusBlaster: a **Raspberry Pi** drives the JTAG lines
directly (bit-bang) with **F/F Dupont wires**.

* **Rabbit JTAG header:** top-LEFT corner of the board (rabbit seen from the
  front), under the base (4 tri-wing screws). 8-pin, top→bottom:
  `1=3V3(Vref, leave NC) 2=GND 3=nTRST 4=TDI 5=TMS 6=TCK 7=TDO 8=RESETN`.
  (Source: wk.redox.ws/dev/nab/v2/jtag + journaldulapin.com debriquer-nabaztag.)
* **Wiring (Pi 40-pin header → JTAG, straight-through):** TDI→pin19(GPIO10),
  TMS→pin24(GPIO8), TCK→pin23(GPIO11), TDO→pin21(GPIO9), GND→pin20.
  Do NOT wire the Pi 3V3 — the **rabbit must be on its own mains power** for
  JTAG to respond (the Pi only talks; it doesn't power the SoC).
* **OpenOCD:** `adapter driver linuxgpiod` + the stock
  `interface/raspberrypi-gpio-connector.cfg`, `transport select jtag`,
  `adapter speed 100`. Target is a generic `arm7tdmi` (IDCODE `0x3f0f0f0f`,
  flash mapped at `0x08000000`, 128 KB). Don't bother with SRST/`reset halt`
  on arm7tdmi (it errors / hangs) — **power-cycle to reset**.
* **Flash writes:** OpenOCD has no OKI flash driver, and the documented patched
  OpenOCD 0.8.0 can't drive the Pi GPIO (no `linuxgpiod`; modern kernels
  dropped sysfs-GPIO). So the OKI ML67Q4051 flash sequence is reimplemented in
  **TCL** on stock OpenOCD (FLACON `0xB7000100`, unlock `0x15554`/`0x0AAA8`,
  sector-erase `0x30`, program 4 bytes/cmd `0xA0`, poll `FLACON&0x0E==0x02`).
  ~19 ms/word over bit-bang → ~10 min for a full image. Read/dump is fast.
* **Procedure:** dump first (`dump_image bricked.bin 0x08000000 0x20000`) to
  back up, then erase the firmware sectors and program `bin/<mode>/Nab.bin`,
  then `dump_image` again and `cmp` against the source to verify byte-exact.

> ⚠️ Before re-flashing, make sure your build carries the **`.bss` zeroing fix**
> (commit `97e05d1`, `patch_linker_keep` → `*(.bss .bss.*)`). Without it every
> mode boots into a Data Abort on the first uninitialized global — see
> [BRICK_FORENSICS.md](./BRICK_FORENSICS.md).

## Flash procedure (recommended path)

1. **Power off** the rabbit.
2. **Hold the head button** while plugging the power back in. The four belly
   lights should go **blue** = config mode.
3. From your phone/laptop, join the Wi-Fi network advertised by the rabbit
   (`Nabaztag-XXXX`).
4. Browse to the rabbit's AP IP (usually `192.168.0.1`). You'll land on the
   *modernized* setup page (`page_a`), unless this is the first flash — in
   which case you still see the 2006 UI.
5. Click **firmware upgrade** → `page_u` → upload
   `releases/naboot-stable.signed.sim`. (`full` is not shipped — see above; the
   shipped stable build is mode `lean`.)
6. **Do not power off during the flash** (~30 s). When done, the rabbit
   reboots on its own.
7. **Then unplug it, wait ten seconds, and plug it back in.** After the
   reboot the first belly light blinks **orange** and stays there — that is
   normal, not a failed flash. A flash ends in a *software* reset, and on this
   hardware a software reset leaves the 2006 radio asleep; only a power cycle
   wakes it. (Three "failed" flashes in a row on 2026-10-21 were exactly this,
   including a known-good image. One unplug and it was back in 75 s.)

## If something goes wrong with `full`

If the rabbit boots into `full` but the modernized config-mode UI misbehaves
(broken form, can't re-upload), enter config mode again (head button + power)
and upload `releases/naboot-minimal.signed.sim`. Minimal restores the
2006-style stock UI exactly as it shipped, with the verifySig opcode kept
in place so OTAs still work.

If the flash itself failed and the rabbit can't be put in config mode at all
— that's the brick scenario above. Recovery requires opening the shell to
reach the SoC's ROM bootloader. Same situation as any flash on this device.

## If you suspect a specific patch — bisect

When `full` broke and you want to know WHICH patch did it (page rewrite
vs bootloader sig gate), build the two bisection variants:

* `./build.sh --mode signed-stock` — minimal + sig-gated httpflash,
  pages stay vanilla (untouched boot UI). Tests `patch_bootloader` in
  isolation.
* `./build.sh --mode pages-only` — minimal + modernized HTML pages,
  no sig gate (`httpflash` keeps the upstream "flash anything" body).
  Tests `modernize_pages` in isolation.

Flash each via the config-mode upload page. See
[BRICK_FORENSICS.md](./BRICK_FORENSICS.md) for the full decision tree
and the most recent incident analysis.

## After the first successful flash

The rabbit now runs **Naboot**. From this point on:

* The bootloader refuses to flash any `.sim` lacking a valid Ed25519
  signature against `firmware-arm/keys/signing_pubkey.h`.
* `httpflash` (config-mode upload) still works — you can re-flash any
  signed `.sim` the normal way.
* OTAs become possible via the add-on: stage with `POST /api/ota`, then
  `?trigger=1` to push a `FW <url>` command to the rabbit. This last step
  needs the **runtime bytecode** to be rebuilt with `firmware/fwota.mtl` +
  the patched `patch_main.py` — that rebuild happens *after* the first
  manual flash, so the rabbit always has the verifySig opcode available
  before any bytecode tries to use it.

## Recovery key facts

* **Private signing key:** `~/.nabaztag/signing_key.bin` on the maintainer's
  machine only (chmod 600). Never copied anywhere. If lost, future OTAs are dead in the
  water — keep a backup somewhere safe.
* **Public key:** `firmware-arm/keys/signing_pubkey.h`, committed to the
  repo, embedded in the firmware. Public, no secrecy required.
* **Both builds are reproducible** from the pinned upstream commits
  (`NABGCC_COMMIT=2c05b53fe…`, `MTL_LINUX_COMMIT=7e557a153…` in the
  Dockerfile). `./build.sh --mode <mode> --remote <user@dev-vm> --runtime podman`
  rebuilds from scratch on the build VM — **never on the Home Assistant host**
  (building there takes the rabbit's server down; see `releases/README.md`).
