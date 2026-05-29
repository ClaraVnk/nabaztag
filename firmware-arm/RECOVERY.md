# Naboot — flash + recovery guide

The `firmware-arm/` tree now produces two `.sim` flavors. Both embed the
Ed25519 verifySig opcode (so future OTAs can be signature-gated). They
differ only in how invasive they are on the bootloader:

| Build      | `Nab.bin` | Δ vs vanilla wpa2 | What's modified |
|------------|-----------|-------------------|-----------------|
| **minimal** (rescue) | 122 KB | +6.4 KB | C side only: TweetNaCl + nab_sig + OPverifySig opcode. **`boot.0.0.0.13.mtl` is byte-identical to vanilla wpa2 HEAD.** Bootloader UI + `httpflash` flow stay exactly as the version Kevin's sysadmin already flashed safely. |
| **full** (recommended) | 117 KB | +1.7 KB | Everything in minimal, **plus**: `httpflash` rejects unsigned/tampered .sim, 4 config-mode HTML pages modernized (dark theme, semantic HTML, mobile viewport). |

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
