# Naboot — flash + recovery guide

The `firmware-arm/` tree now produces two `.sim` flavors. Both embed the
Ed25519 verifySig opcode (so future OTAs can be signature-gated). They
differ only in how invasive they are on the bootloader:

| Build      | `Nab.bin` | Δ vs vanilla wpa2 | What's modified |
|------------|-----------|-------------------|-----------------|
| **minimal** (rescue) | 122 KB | +6.4 KB | C side only: TweetNaCl + nab_sig + OPverifySig opcode. **`boot.0.0.0.13.mtl` is byte-identical to vanilla wpa2 HEAD.** Bootloader UI + `httpflash` flow stay exactly as the version Kevin's sysadmin already flashed safely. |
| **full** (recommended) | 117 KB | +1.7 KB | Everything in minimal, **plus**: `httpflash` rejects unsigned/tampered .sim, 4 config-mode HTML pages modernized (dark theme, semantic HTML, mobile viewport). |
| **phase8-rom** (experimental) | 108 KB | -6.6 KB | Everything in `full` + `--gc-sections` **plus** the Phase 8 stack of 8 source-level slices: (1) 4 portal HTML pages live in C-side flash ROM rendered via a new `OPpageRender` opcode; (2) `pagefill`/`listreplacestr` + 4 stillborn helpers (`wifiConnected`, `listnth`, `itoanil`, `unregudp`) pruned as orphans after rom_pages obsoleted their callers (each verified to have zero external refs before deletion); (3) `mkwav 8000 1 16` precomputed and inlined as a 46-byte literal; (4) `dump`/`dumpscan` reduced to identity (UART unwired so the prints go to a sink nobody reads), then callsites inlined and defs dropped; (5) all 86 `Secho{ln} "literal"` calls replaced with `nil`; (6) user echo helpers `MACecho`/`SEQecho`/`IPecho` collapsed to identity (passthrough preserved for callers using their return); (7) every dynamic-arg `Secho`/`Secholn`/`Iecho`/`Iecholn` opcode keyword deleted — they're confirmed stack passthroughs in vinterp.c. **Smallest .sim of all modes (220 330 B)**, -18 376 B vs minimal/rescue, -6 328 B vs lean. Frees ~10 KB of vmem RAM at runtime. Every slice is idempotent and composable in the mode's step list. Untested on hardware as of 2026-06-01 — flash `full` first, treat `phase8-rom` as the experimental headroom slice. |

Both files live under `bin/<mode>/`:

```
firmware-arm/bin/
├── minimal/
│   ├── firmware0.0.0.13.sim         ← unsigned (just for inspection)
│   └── firmware0.0.0.13.signed.sim  ← upload this one (rescue)
└── full/
    ├── firmware0.0.0.13.sim
    └── firmware0.0.0.13.signed.sim  ← upload this one (normal)
```

## Risk model

* **First-flash risk** is the same as the vanilla wpa2 flash Kevin's sysadmin
  successfully performed a year ago — the bootloader bytecode and config-mode
  UI are unchanged in `minimal`, and our additions in `full` are surgical
  (Metal compiler validates everything; the simulator confirms the source
  compiles cleanly with no unresolved labels).
* **Brick scenario:** a corrupted upload mid-flash could leave the rabbit
  unable to re-enter config mode. The OKI ML67Q4051 ROM bootloader recovers
  via UART pads on the PCB — which means opening the shell. That risk is
  unchanged from what Kevin already accepted with the sysadmin flash.

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
   `bin/full/firmware0.0.0.13.signed.sim`.
6. **Do not power off during the flash** (~30 s). When done, the rabbit
   reboots and rejoins your normal network.

## If something goes wrong with `full`

If the rabbit boots into `full` but the modernized config-mode UI misbehaves
(broken form, can't re-upload), enter config mode again (head button + power)
and upload `bin/minimal/firmware0.0.0.13.signed.sim`. Minimal restores the
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
* `httpflash` (config-mode upload) still works — Kevin can re-flash any
  signed `.sim` the normal way.
* OTAs become possible via the add-on: stage with `POST /api/ota`, then
  `?trigger=1` to push a `FW <url>` command to the rabbit. This last step
  needs the **runtime bytecode** to be rebuilt with `firmware/fwota.mtl` +
  the patched `patch_main.py` — that rebuild happens *after* the first
  manual flash, so the rabbit always has the verifySig opcode available
  before any bytecode tries to use it.

## Recovery key facts

* **Private signing key:** `~/.nabaztag/signing_key.bin` (Kevin's Mac only,
  chmod 600). Never copied anywhere. If lost, future OTAs are dead in the
  water — keep a backup somewhere safe.
* **Public key:** `firmware-arm/keys/signing_pubkey.h`, committed to the
  repo, embedded in the firmware. Public, no secrecy required.
* **Both builds are reproducible** from the pinned upstream commits
  (`NABGCC_COMMIT=2c05b53fe…`, `MTL_LINUX_COMMIT=7e557a153…` in the
  Dockerfile). `./build.sh --mode minimal` and `./build.sh --mode full`
  rebuild from scratch on the HAOS host.
