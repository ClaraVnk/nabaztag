# Naboot — runbook

Every tool in this repository, from a shell. Each section gives the commands
in order, then points at the document that explains them in depth.

Placeholders: `<dev-vm>` is a Linux machine with podman (never the Home
Assistant host: a build there starves it and drops the rabbit), `<pi>` the
JTAG station, `<mac>` a rabbit's twelve hex digits. Container bases are
Alpine or Rocky; every Containerfile lives in the repository.

## 0. What is where

| Path | What |
|---|---|
| `firmware-arm/` | the ARM firmware (Naboot): `build.sh`, `apply-mods.py` (every change, as steps and modes), `crypto/`, `preflight.py` |
| `firmware-arm/releases/` | the shipped release; every earlier image in `archive/` |
| `firmware-arm/signing/` | Ed25519 signing of a `.sim` |
| `firmware-arm/tests/` | host tests of the crypto and protocol code (`make`) |
| `firmware-arm/tools/` | the Metal toolchain: compiler, assembler, disassembler (`run-tests.sh`) |
| `firmware-arm/emulator/` | the rabbit without a rabbit: Wi-Fi scenarios, a whole rabbit (`rabbit-vm`), proofs against a real Terrier |
| `firmware-arm/jtag/` | the JTAG station on a Raspberry Pi |
| `bytecode/` | the runtime bytecode: `build-bytecode.sh` and one `patch_*.py` per change |
| `nabaztag-violet/` | the Home Assistant add-on |

## 1. Building a firmware

```sh
cd firmware-arm
./build.sh --mode naboot-1.0.0.3 --remote <dev-vm> --runtime podman
# -> bin/naboot-1.0.0.3/firmware0.0.0.13.sim (unsigned), Nab.bin, Nab.elf
```

- `--mode` picks the list of steps (`python3 apply-mods.py --help` lists the
  modes; `naboot-X.Y.Z.W` are the releases).
- The build runs on the remote, in `firmware-arm/Dockerfile`. Only the build
  context goes there, never the key.
- `preflight.py` checks the image as the build ends: it fits in flash
  (126,976 bytes), the stack, the external RAM, the reset vector. A failed
  check means no image.

**Signing** (only an image you will ship):

```sh
./build.sh --mode naboot-1.0.0.3 --remote <dev-vm> --runtime podman --sign
# -> bin/naboot-1.0.0.3/firmware0.0.0.13.signed.sim, verified by verify_sim.py
python3 signing/verify_sim.py bin/naboot-1.0.0.3/firmware0.0.0.13.signed.sim
```

The key is `~/.nabaztag/signing_key.bin` on the machine that signs; never
print it, never copy it to a remote. Its backups: a password manager
(age/YubiKey) and the OpenBao KV. A new machine gets the file back from
there; `firmware-arm/signing/gen_key.py` would make a *different* key, which no rabbit
accepts.

## 2. Releasing

1. Build with `--sign` (above).
2. Prove it:
   - in the emulator (section 4);
   - on the test rabbit, by OTA from the Terrier as one rabbit's own image
     (the Terrier's own RUNBOOK, "Sending a firmware");
   - from the previous release, as every other rabbit will take it.
3. Copy it as firmware-arm/releases/naboot-X.Y.Z.W.signed.sim, and **move
   the previous release to `firmware-arm/releases/archive/`** in the same change. Write
   its section in `firmware-arm/releases/README.md` (size, sha256, what changed, what was
   proven), and point `firmware-arm/jtag/README.md`'s flash example and `firmware-arm/docs/RECOVERY.md`
   at it.
4. The Terrier's shared image: upload it on the admin page (Firmware image).
   The Terrier's `/naboot` link to GitHub (`GITHUB_SIM` in the Terrier's ui.py)
   names the release file: change it in the same week.

## 3. Host tests

```sh
make -C firmware-arm/tests            # crypto + protocol, under ASan/UBSan
make -C firmware-arm/tests tls-interop   # the TLS client against OpenSSL (python3 cryptography)
make -C firmware-arm/tests arm        # the same code built for ARM (gcc-arm-none-eabi)
firmware-arm/tools/run-tests.sh       # the Metal toolchain against the reference compiler
```

A missing `libasan` (some distributions) means: run them in a Rocky container,

```sh
podman run --rm -v $PWD/firmware-arm:/src:Z -w /src/tests quay.io/rockylinux/rockylinux:10 \
  sh -c "dnf -q -y install gcc make libasan libubsan openssl python3-pip && pip3 -q install cryptography && make"
```

## 4. The emulator

Details: [`firmware-arm/emulator/README.md`](../firmware-arm/emulator/README.md).

```sh
cd firmware-arm/emulator
./run.sh                                  # every Wi-Fi scenario; exit status = failures
./run.sh scenarios/wpa3-sae.txt           # one, with its transcript
./run.sh --steps wpa2_gtk,mdns            # with the firmware steps you choose
```

**A whole rabbit** (bootloader, bytecode, Wi-Fi, a gateway) against your own
Terrier:

```sh
python3 mkconf.py --ssid Terrier --security open --server <warren>:80/vl \
    --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o open.eeprom
./run.sh --vm scenarios/vm/boot-open.txt --eeprom open.eeprom --warren <warren>
```

**Proofs against a real Terrier container** (podman on `<dev-vm>`, nothing
leaves it):

```sh
# the emulator image, with the firmware steps of the build you test
podman build -f firmware-arm/emulator/Containerfile --build-arg MOD_STEPS=<steps> -t emu .
# adoption + TLS session, then whatever you add
firmware-arm/emulator/tools/xmpp-tls-proof.sh \
    --terrier ghcr.io/claravnk/terrier:latest --bytecode <runtime.hybrid> \
    --emulator emu --elf <bin/<mode>/Nab.elf> --out /tmp/proof \
    [--speak-at 110 --speak "Bonjour"] [--ota <signed.sim>] [--scenario <file>] [--third]
```

- `--elf` is the bootloader you test: its `Nab.bin` must sit next to it.
- `--ota` sends that image inside the session and stops at `flashFirmware`:
  there is no flash to write here, and the line says so.
- `--split --web-restart-at 120 --speak-at 150` runs the warren as its two
  containers (core and web, terrier/corelink.py) and restarts the web alone
  while the rabbit is connected: the verdict shows the sound asked through
  the restarted web reaching the rabbit (proven 2026-11-13).
- `BASE=<port>` moves every port, so several proofs can run side by side.
- A proof left running holds its ports: `podman ps`, then `podman rm -f
  terrier-proof-<base>`.

**A rabbit of a given release, and a rabbit nobody ever flashed.**

```sh
# the emulator steps of a release (network, crypto, VM: what decides how it behaves)
podman build -f firmware-arm/emulator/Containerfile \
    --build-arg MOD_STEPS=$(python3 firmware-arm/emulator/tools/emu-steps.py naboot-1.0.0.3) -t emu-1003 .
# a release taking an update in its TLS session (scenarios/vm/xmpp-ota.txt, 300 s)
firmware-arm/emulator/tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest \
    --bytecode <runtime.hybrid> --emulator emu-1003 --elf <bin/naboot-1.0.0.3/Nab.elf> \
    --out /tmp/ota --speak-at 110 --speak "Bonjour" --ota <naboot-X.signed.sim> \
    --scenario scenarios/vm/xmpp-ota.txt

# Violet's own flash guard (a stock bootloader never calls verifySig)
podman build -f firmware-arm/emulator/Containerfile -t nabaztag-emulator .
podman build -f firmware-arm/emulator/Containerfile.stockflash \
    --build-arg BASE=localhost/nabaztag-emulator -t emu-stockflash firmware-arm/emulator
# a stock rabbit (Violet's boot 0.0.0.10) through its setup page, as a phone uploads
python3 firmware-arm/tools/mtl_comp.py firmware-arm/vendor/nabgcc/mtl/boot/boot.0.0.0.10.mtl -o /tmp/boot-0010.bc
NO_BUILD=1 TAG=emu-stockflash firmware-arm/emulator/tools/config-upload.sh /tmp/boot-0010.bc <naboot-X.signed.sim>
# the same rabbit adopted on a Terrier and updated over the air (otafix, binary path)
firmware-arm/emulator/tools/stock-ota-proof.sh --terrier ghcr.io/claravnk/terrier:latest \
    --runtime <baseline-tls.hybrid> --otafix <baseline-otafix.hybrid> --sim <naboot-X.signed.sim> \
    --emulator emu-stockflash --out /tmp/stock-ota
```

The verdict is the VM's last lines: `asked to flash itself with a N-byte
image`, `flash writes 1`, `resets asked 0`, and the heap at its highest.

## 5. The runtime bytecode

Details: [`bytecode/README.md`](../bytecode/README.md).

```sh
cp -r firmware-arm/vendor/openjabnab/bootcode /tmp/tree     # stock Violet sources
bash bytecode/build-bytecode.sh /tmp/tree firmware-arm/tools/mtl_comp.py /tmp/runtime.hybrid
```

The script applies every patch in order. It checks that each policy literal
(`xmpp-tls:0`, `xmpp-ota:0`, ...) is in the result exactly once, because the
Terrier flips them per rabbit. Prove it in the emulator (section 4,
`--bytecode`). Then serve it to one rabbit first: the Terrier's
RUNBOOK, "The runtime bytecode".

## 6. The JTAG station

Details: [`firmware-arm/jtag/README.md`](../firmware-arm/jtag/README.md) and
[`firmware-arm/jtag/RUNBOOK.md`](../firmware-arm/jtag/RUNBOOK.md); wiring in
`firmware-arm/jtag/wiring.svg`.

```sh
./firmware-arm/jtag/station.sh setup          # once, on the Pi
./firmware-arm/jtag/station.sh check          # JTAG answers, the flash reads back
./firmware-arm/jtag/station.sh listen         # the live status line, no halt
./firmware-arm/jtag/station.sh dump out.bin   # what the rabbit really runs
./firmware-arm/jtag/station.sh flash firmware-arm/releases/naboot-1.0.0.3.signed.sim
```

Never halt a rabbit during its association (it can lose its Wi-Fi), never
arm the watchdog over JTAG, and read symbols only from the ELF of the build
that is in the rabbit.

## 7. A rabbit that does not come back

[`firmware-arm/docs/RECOVERY.md`](../firmware-arm/docs/RECOVERY.md): the
setup page and the release first, the JTAG station last.
