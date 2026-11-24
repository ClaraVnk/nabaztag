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
./build.sh --mode naboot-1.2.0.0 --remote <dev-vm> --runtime podman
# -> bin/naboot-1.2.0.0/firmware0.0.0.13.sim (unsigned), Nab.bin, Nab.elf
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
./build.sh --mode naboot-1.2.0.0 --remote <dev-vm> --runtime podman --sign
# -> bin/naboot-1.2.0.0/firmware0.0.0.13.signed.sim, verified by verify_sim.py
python3 signing/verify_sim.py bin/naboot-1.2.0.0/firmware0.0.0.13.signed.sim
```

The key is `~/.nabaztag/signing_key.bin` on the machine that signs; never
print it, never copy it to a remote. Its backups: a password manager
(age/YubiKey) and the OpenBao KV. A new machine gets the file back from
there; `firmware-arm/signing/gen_key.py` would make a *different* key, which no rabbit
accepts.

Where the flash goes, and what a change of compiler flags costs
(`firmware-arm/docs/FLASH-MAP.md`): build the mode unsigned, then

```sh
python3 firmware-arm/tools/flash-map.py firmware-arm/bin/<mode>/Nab.elf firmware-arm/bin/<mode>/Nab.bin
# crypto time at another level, in tools/cost's image (see run.sh's header):
OPT=-Os NOSIZES=1 bash run.sh x25519 ed_short p256 aes_block gcm_1k
```

How two builds touch memory, file by file (calls to memcpy/memset, stores
by width), from the images build.sh left on the dev VM; run it there:

```sh
firmware-arm/tools/os-diff.sh localhost/nab-firmware-build:naboot-1.2.0.1 \
    localhost/nab-firmware-build:naboot-1.2.0.2-safe src/net src/usb
```

What the configuration pages serve, as a phone's browser gets them
(`gzip_pages`: stylesheet and fixed pages gzipped), checked answer by
answer; run it on the dev VM with the build's `Nab.elf` and `Nab.bin` side
by side:

```sh
firmware-arm/emulator/tools/config-pages.sh firmware-arm/bin/naboot-1.2.0.1-gz/Nab.elf
```

## 2. Releasing

1. Build with `--sign` (above).
2. Prove it:
   - in the emulator (section 4), the XMPP session bound included
     (`xmpp-tls-proof.sh`, as many answers as challenges: 1.2.1.0 shipped
     to the test rabbit without it and never answered one, 2026-11-18);
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
make -C firmware-arm/tests tls-interop-chacha   # the same, ChaCha20-Poly1305 only (tls_chacha_only)
make -C firmware-arm/tests CFLAGS="-Os -g -std=c99 -Wall -Wextra -Werror -pedantic" TNFLAGS="-Os -g -std=c99 -w"   # the same at -Os, as opt_size_crypto compiles the crypto (2026-11-19)
BEFORE=1 make -C firmware-arm/tests vm_strcmp   # OPstrcmp as shipped before 1.2.1.0: an ASan READ (review S-4)
FIRSTCUT=1 make -C firmware-arm/tests vm_strcmp # S-4's first cut: bounded, and "md5-sess\0" no longer equals "md5-sess" (the 2026-11-18 bind loop)
make -C firmware-arm/tests arm        # the same code built for ARM (gcc-arm-none-eabi)
firmware-arm/tools/run-tests.sh       # the Metal toolchain against the reference compiler
python3 -m unittest discover -s firmware-arm/tests/apply_mods   # apply-mods' own steps and its mode table
```

apply-mods.py without a compiler: every mode applied to a staged copy of
the vendored tree, a hash of each tree (and one with CRLF read as LF), a
FAIL naming the anchor that broke. The before of a before/after is any
other copy of the script (`--apply`). How the modes differ, as steps:

```sh
firmware-arm/tools/mods-matrix.py --all --out after.txt
git show main:firmware-arm/apply-mods.py > /tmp/apply-main.py
firmware-arm/tools/mods-matrix.py --all --apply /tmp/apply-main.py --out before.txt
firmware-arm/tools/mode-diff.py naboot-1.2.0.2 naboot-1.2.1.0
```

What `-Os` would save on the pure crypto files alone (`opt_size_crypto`),
object by object, inside a build image left on the dev VM:

```sh
ssh <dev-vm> 'flock ~/heavy.lock podman run --rm -i localhost/nab-firmware-build:naboot-1.2.1.0 sh -s' < firmware-arm/tools/os-crypto-size.sh
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

**An ear turned by hand** (the "amoureux" pairing, 2026-11-14).
`at <ms> ear <0|1> <holes>` in a scenario turns a still ear by hand;
`firmware-arm/emulator/scenarios/vm/xmpp-ears.txt` turns each ear five holes and the verdict shows
`ears moved` in the Terrier's log, or nothing.

```sh
firmware-arm/emulator/tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest \
    --bytecode <runtime.hybrid> --emulator emu --elf <bin/naboot-1.2.0.1/Nab.elf> \
    --out /tmp/ears --scenario scenarios/vm/xmpp-ears.txt
# the measurement runtime: the ears' state every 30 s inside the session
EARS_TELEMETRY=1 bytecode/build-bytecode.sh <bootcode-tree> \
    firmware-arm/tools/mtl_comp.py /tmp/ears-telemetry.hybrid
```

Served to one rabbit by copying it to the Terrier's `/data/bytecode/` and
choosing `ears-telemetry` in that rabbit's Developer card (admin); the
Terrier logs each `<earsdbg xmlns="violet:nabaztag:telemetry">`. Never the
runtime everyone gets.

**A rabbit of a given release, and a rabbit nobody ever flashed.**

```sh
# the emulator steps of a release (network, crypto, VM: what decides how it behaves)
podman build -f firmware-arm/emulator/Containerfile \
    --build-arg MOD_STEPS=$(python3 firmware-arm/emulator/tools/emu-steps.py naboot-1.2.0.0) -t emu-1200 .
# a release taking an update in its TLS session (scenarios/vm/xmpp-ota.txt, 300 s)
firmware-arm/emulator/tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest \
    --bytecode <runtime.hybrid> --emulator emu-1200 --elf <bin/naboot-1.2.0.0/Nab.elf> \
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

**The Home Assistant add-on** (`nabaztag-violet/`, run from the checkout;
port 5223 must be free):

```sh
# adoption, a sentence inside TLS, then bc.jsp over TLS (bootcode.tls)
firmware-arm/emulator/tools/addon-tls-proof.sh --emulator nabaztag-emulator \
    --elf <bin/naboot-1.2.0.1/Nab.elf> --out /tmp/addon-tls
# Naboot 1.2.x: bootcode.https, and an https:// stream the rabbit opens itself.
# Its own image: the https opcodes are the tls_certs step, which neither the
# default MOD_STEPS nor emu-steps.py include. Without it httpsStart is no
# opcode at all, the rabbit writes the root it was given where its
# ClientHello should be and the host answers "wrong version number"
# (2026-11-16, a morning lost to it).
podman build -f firmware-arm/emulator/Containerfile -t emu-https \
    --build-arg MOD_STEPS=$(grep -m1 '^STEPS=' firmware-arm/emulator/run.sh | cut -d= -f2),tls_certs .
PY=<python with cryptography> firmware-arm/emulator/tools/addon-https-proof.sh \
    --emulator emu-https --elf <bin/naboot-1.2.0.1/Nab.elf> --out /tmp/addon-https
```

The same image for `tools/https-proof.sh` (Le Terrier's side).

`--real icecast.radiofrance.fr/fip-midfi.mp3` plays a real station instead of
the test host: a relay passes the rabbit's bytes to the station untouched and
writes `relay.log` (bytes each way, who closed). Proven 2026-11-16: FIP played
for the whole scenario, about 2 MB in two minutes.

Read the add-on's `opens ... itself over HTTPS`, then the rabbit's `https:`
lines and `audio: the rabbit started`.

Where a reset sends the rabbit (`factory_server`, Naboot 1.2.1.0): each
image's bootloader on an empty config page, and the server it writes there,
which is what the config page's reset writes too. 1.2.0.1 first, as the
control:

```sh
cd firmware-arm/emulator
NO_BUILD=1 tools/default-server-proof.sh ../bin/naboot-1.2.0.1/Nab.elf ../bin/naboot-1.2.1.0/Nab.elf
# ../bin/naboot-1.2.0.1/Nab.elf   r.nabaztag.com/vl
# ../bin/naboot-1.2.1.0/Nab.elf   terrier.cyberloutre.fr/vl      (2026-11-17)
```

The setup page of a rabbit that kept Violet's server offers the Terrier
instead (1.2.1.0 = the gzipped pages + factory_server: v.js says it). Keep
the config page the 1.2.0.1 run wrote, then:

```sh
EEPROM=violet-conf.eeprom EXPECT_SERVER=terrier.cyberloutre.fr/vl NO_BUILD=1 \
  tools/config-pages.sh ../bin/naboot-1.2.1.0/Nab.elf
# ok  v.js: offers the server 'terrier.cyberloutre.fr/vl'          (2026-11-18)
# naboot-1.2.0.1-gz-tel, the control: FAIL, it offers r.nabaztag.com/vl
```

**The 2026-11-18 review** (`firmware-arm/docs/SECURITY-FINDINGS-2026-11.md`,
its last section), each with its control, on the dev VM:

```sh
cd firmware-arm/emulator
tools/ie-walk-proof.sh ../bin/naboot-1.2.1.0/Nab.elf   # S-1: the element walks under ASan, with and without ie_walk_bounds
tools/ccmp-enforce-proof.sh               # S-2: scenarios/enforce/* with and without ccmp_no_probation
NO_BUILD=1 tools/config-presence.sh ../bin/naboot-1.2.1.0/Nab.elf   # S-6: who may change the server
# the control: the base image's Nab.elf (before config_presence) takes the stranger's server
# 2026-11-19: a press of the head arms a minute (config-server-head: the save 30 s
# after the press is taken; config-server-head-late: 70 s after, refused)
NO_BUILD=1 tools/noserver-proof.sh ../bin/naboot-1.2.1.0/Nab.elf   # boot_noserver: the belly with the Wi-Fi up and no warren
STEPS=$(python3 tools/emu-steps.py naboot-1.2.1.0) tools/diag-proof.sh ../bin/naboot-1.2.1.0/Nab.elf   # wifi_diag + boot_diag: the line in flash at 1024, &dg= on the next boot, cleared after
# the control: the image before the step shows amber and green only (its refused run FAILs)
```

**The two flash levers** (`naboot-1.2.1.0` = `-tel` with
`opt_size_crypto` and `tls_chacha_only`, 2026-11-19; the numbers in
`firmware-arm/docs/FLASH-MAP.md` and `SECURITY-FINDINGS-2026-11.md`, O-1 and O-2).
The emulator takes both steps (`emu-steps.py` passes them, its Makefile
reads them off the patched tree: AES-GCM compiled out, the crypto objects
at -Os), so the lean emulator binds its session on the one suite the lean
image offers. The control is `-tel`, every proof run on both:

```sh
cd firmware-arm/emulator
podman build -f Containerfile --build-arg MOD_STEPS=$(python3 tools/emu-steps.py naboot-1.2.1.0) -t emu-1210-ctl ../..
podman build -f Containerfile --build-arg MOD_STEPS=$(python3 tools/emu-steps.py naboot-1.2.1.0) -t emu-1210-lean ../..
# the XMPP session bound, a sound inside it (res:), on each
tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest --bytecode <baseline-https.hybrid> \
    --emulator emu-1210-lean --elf ../bin/naboot-1.2.1.0/Nab.elf --out /tmp/lean-bind \
    --speak-at 110 --speak "Bonjour"
# the HTTPS boot through Caddy (ChaCha20-Poly1305 alone offered, Go's TLS 1.3 takes it):
# one bootloader for both, compiled by hand with the proof's host, since the levers change no bytecode
tools/boot-https-proof.sh chain /tmp/https
podman build -f Containerfile --build-arg BOOT_ANCHOR_FILE=proof_boot_anchor.der \
    --build-arg MOD_STEPS=$(python3 tools/emu-steps.py naboot-1.2.1.0),tls_certs,boot_https -t emu-1210-lean-https ../..
NAB_BOOT_HTTPS_HOST=warren.test ../tools/mods-matrix.py naboot-1.2.1.0 --keep /tmp/stage
python3 ../tools/mtl_comp.py /tmp/stage/naboot-1.2.1.0/mtl/boot/boot.0.0.0.13.mtl -o /tmp/boot-proof.bc
tools/boot-https-proof.sh run /tmp/https --terrier ghcr.io/claravnk/terrier:latest --bytecode <baseline-https.hybrid> \
    --emulator emu-1210-lean-https --elf /tmp/boot-proof.bc
# config pages, the reset's server, presence, no-warren: NO_BUILD=1 TAG=emu-1210-lean, as above
# constant time at -Os (tools/cost/ct-levers.sh, qemu-arm): X25519 on three scalars, Ed25519
# verification on three messages, the three tag compares on three tags; equal counts per group
podman run --rm -v $PWD/../..:/m:Z -w /m/firmware-arm/tools/cost localhost/nab-qemu bash ct-levers.sh
# the vectors under qemu-arm at -Os (bench.c checks each result): run.sh's header for the inputs
OPT=-Os NOSIZES=1 bash run.sh x25519 ed_short ed_long p256 aes_block_cmac cp_1k ecdsa_p256 rsa2048_pss
```

**The XMPP session, bound, on every release** (2026-11-18: 1.2.1.0's first
cut of S-4 took the Terrier's DIGEST-MD5 challenge and never answered it,
a TLS bind loop every 30 s on the test rabbit, while the proof above had
bound). The emulator with the release's VM steps, the production runtime,
the production Terrier image; the realm of the proof's Terrier has the
production's shape (19 characters, a padded challenge, `--domain`) because
the shorter `warren.test` hid it. The control first:

```sh
cd firmware-arm/emulator
podman build -f Containerfile --build-arg MOD_STEPS=$(python3 tools/emu-steps.py naboot-1.2.1.0) -t emu-1210 ../..
tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest --bytecode <baseline-https.hybrid> \
    --emulator <the previous release's image> --elf ../bin/<previous release>/Nab.elf --out /tmp/bind-control
tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest --bytecode <baseline-https.hybrid> \
    --emulator emu-1210 --elf ../bin/naboot-1.2.1.0/Nab.elf --out /tmp/bind
#   sasl (boot 2, realm terrier.warren.test): 1 challenge(s) of 188 bytes, 1 answer(s) from the rabbit, 1 success
# the regression: "2 challenge(s) of 188 bytes, 0 answer(s) from the rabbit, 0 success" (a reconnect at 134 s)
```

`emu-steps.py` lists the release's network, crypto and VM steps (`run.sh`'s
list, which the review's VM steps joined that day). A verdict with fewer
answers than challenges is a release that does not bind.

### A long press that listens, and the Wi-Fi line

`--long-press echo` maps the owner's long press to "Écoute-toi"; with the
long-press scenario the head is held at 120 s and the verdict shows what the
microphone recorded after a real press, chime included (2026-11-19: 0 bytes
on two real rabbits, where `--api listen` took 12 KB). With a runtime built
from this repo (bytecode/patch_wifi_in_session.py) and a Terrier that flips
`xmpp-wifi`, the Terrier's log also has `wifi` once per connection.

```sh
cd firmware-arm/emulator
tools/xmpp-tls-proof.sh --terrier <image> --bytecode <runtime.hybrid> \
    --emulator emu-1210 --elf ../bin/naboot-1.2.1.0/Nab.elf --out /tmp/lp \
    --scenario scenarios/vm/xmpp-longpress.txt --long-press echo
#   control (Terrier before the fix): microphone: stopped after 0 blocks
#   fix: mic: <mac> captured N bytes of audio
```

### Config mode closes by itself (Naboot 1.2.1.4)

`apply-mods.py config_timeout`: the bootloader notes the time on entering
config mode and on every request to the setup page, and reboots after 600 s
with neither (an unplug, in effect). Mode `naboot-1.2.1.4` = 1.2.1.3 +
config_timeout. From `firmware-arm/emulator` on the dev VM, with an emulator
image carrying the image's opcodes:

```sh
podman build -f firmware-arm/emulator/Containerfile -t emu-1214 \
  --build-arg MOD_STEPS=$(python3 firmware-arm/emulator/tools/emu-steps.py naboot-1.2.1.3) .
TAG=emu-1214 NO_BUILD=1 tools/config-idle.sh <naboot-1.2.1.4 Nab.elf>          # reset at ~626 s, a station at 700 s
PHONE=1 TAG=emu-1214 NO_BUILD=1 tools/config-idle.sh <naboot-1.2.1.4 Nab.elf>  # GET / at 500 s: reset at ~1101 s
TAG=emu-1214 NO_BUILD=1 tools/config-idle.sh <naboot-1.2.1.3 Nab.elf>          # control: still master at 700 s, FAIL
```
(Nab.bin must sit beside Nab.elf: bootbc.py lifts the bootloader from it.)

### Config mode from three short presses of the head (Naboot 1.2.1.3)

Offline (the bootloader: a rabbit that cannot reach its Wi-Fi any more),
`firmware-arm/boot-mods/triplepress.mtl`; online (the runtime on its server),
`bytecode/patch_triple_press.py` and the opcodes of `apply-mods.py
config_boot`, used only when the warren flips `cfg-boot:0` to 1. Build mode
`naboot-1.2.1.3` (1.2.1.2 + triple_press + config_boot); the
emulator image with `config_boot` added to the release's emulator steps.
From `firmware-arm/emulator` on the dev VM:

```sh
# offline: control = the 1.2.1.2 Nab.elf, still a station at 40 s
NO_BUILD=1 TAG=emu-1212 RUNTIME=podman tools/config-triple-press.sh <Nab.elf>
#   fix: "ok   the rabbit is its own access point", GET / 200
podman build -f firmware-arm/emulator/Containerfile -t emu-1213 \
  --build-arg MOD_STEPS=$(python3 firmware-arm/emulator/tools/emu-steps.py naboot-1.2.1.3),config_boot .
# online: the runtime as built (cfg-boot:0) is the control, and a copy with
# the literal flipped to cfg-boot:1 (what the warren serves a 1.2.1.3) the fix
tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest --bytecode <runtime.hybrid> \
    --emulator emu-1213 --elf <naboot-1.2.1.3 Nab.elf> --out /tmp/tp \
    --scenario scenarios/vm/xmpp-triple-press.txt
# 2026-11-22: control "controlwait ev=2" (a double click), a station at 170 s;
#   fix "the rabbit restarts after its reset (reason 6...)", master at 170 s, GET / 200

### A sleeping rabbit, its head and its tags

Violet's sleepRun read neither the button nor the reader, so a sleeping rabbit
could only be woken from the server (2026-11-22, on a real rabbit: a double
click, a long press and a tag, nothing on the wire).
bytecode/patch_head_in_sleep.py reports them, in the dark. A runtime built
with and one built without it (the control: skip that line of
build-bytecode.sh), from `firmware-arm/emulator` on the dev VM:

```sh
tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest --bytecode <runtime.hybrid> \
    --emulator emu-1212 --elf <naboot-1.2.1.2 Nab.elf> --out /tmp/hs \
    --scenario scenarios/vm/xmpp-sleep-head.txt --at 100 sleep '{"asleep":true}'
#   control: nothing after the `asleep` bind
#   fix: button clic=2 (double_click), button clic=3 (long_click), one
#        `rfid over the session` for a tag resting 4 s, still bound `asleep`
```

### The Nabaztag v1 (2005)

`firmware-arm/emulator/v1` runs Violet's own v1 firmware (vendored in
`firmware-arm/vendor/newbaztag`) on an emulated PIC18 with its Prism 2 card,
serial flash and ear sensors; see its README. From `firmware-arm/emulator`,
on the dev VM:

```sh
tools/v1-proof.sh                          # joins, DHCP, DNS, the p3.jsp poll
tools/v1-proof.sh --terrier <terrier checkout>
# the Terrier's own handler (its tools/v1_warren.py): program taken, ears
# 5/10 then 2/3 and stopped, a hand -> sd=8603, a 3 s press -> sd=0004,
# two v1 in "amoureux" following each other. V1_PORT overrides 28080.
V1_FW=1.4b tools/v1-proof.sh --terrier <terrier checkout>
# the same on Clara's own firmware (Lazare, firmware-arm/v1-icsp/dumps,
# a PIC18LF6525 on an AT45DB081B: --hex ... --flash-chip 081).
# 2026-11-24: "PROOF terrier: program taken, ears stop, hand and long
# press reported, amoureux follow", the ears one after the other
# (terrier#476).
```

The 1.4b firmware keeps the PIC's brown-out reset and its watchdog on
(CONFIG2L 0A, CONFIG2H 0F; 2.0b has both off). On Lazare (2026-11-24) the
left ear alone moves, the right one alone restarts the rabbit: with the
brown-out reset turned off (`no-brownout.py`, written over ICSP and
verified) it no longer restarted, and the right ear still did not turn,
in silence -- its motor or its bridge draws current without turning, a
fault of that rabbit, not of the firmware. The original was written back
(verified against the dump): the reset cuts a stalled motor. The
Terrier's program starts the motors one at a time all the same (the
smaller peak). The emulator models the watchdog, not the supply; on
the 161 flash model the 1.4b firmware lost the program it had just written
and never polled again, hence `--flash-chip 081`.

Violet's own v1 programs (`firmware-arm/vendor/violet-platform`, MIT) put
together by Violet's own assembler, and served as Violet's server did, from
the repository root on the dev VM:

```sh
podman build -t violet-vasm -f firmware-arm/emulator/tools/violet-vasm/Containerfile .
podman run --rm violet-vasm Nabaztag_wait.vasm > wait.frame   # the 05 frame, 33747 bytes
podman build -t nabaztag-v1 -f firmware-arm/emulator/v1/Containerfile firmware-arm
python3 firmware-arm/emulator/tools/violet-vasm/violet_warren.py --port 28081 \
    --frame wait.frame --ears 5 10 --after 3 &
podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 28081 --seconds 120
# 2026-11-22, firmware 2.0b: both ears home at 19 s ("motor stopped, slot 16"),
# then after the order L5 R10 turn and stop at 52-53 s ("slot 4", "slot 9").
# Violet served this same program to firmware 12, 14 and 20; ours reboots a
# real 1.4b at every ear order.
```

The Terrier's copy of that program, `Nabaztag_wait_terrier.vasm`
(`firmware-arm/emulator/tools/violet-vasm/patches/one-motor-at-a-time.patch`, applied when the
image builds: in `@moveears` the right ear waits while `/dir0` says the left
one turns), against Violet's as the control, on both firmwares:

```sh
firmware-arm/emulator/tools/violet-vasm/one-motor-proof.sh   # repo root, dev VM, ~90 s
# 2026-11-24: PASS. On an order (L5 R10, then L2 R6) Violet's frame starts
# both motors in the same millisecond (2.0b: left 48635 ms, right 48636;
# 1.4b: 48293, 48294); the Terrier's starts the right one the millisecond
# the left one stops (2.0b: left 48635-51980, right 51980-56682; 1.4b: left
# 48555-51767, right 51768-57170), on both orders. st=01 after the program,
# a hand on the right ear reported (sd=9007 on 2.0b, 8E0F on 1.4b, both
# frames alike; the value moves with the second the hand comes, 8204/9004/
# 9010 on Violet's own, so the script checks the report, not its value), a
# click with a message waiting sends sd=0003, the 1.4b watchdog never fires.
# Still both motors at once, in both programs: the acquisition turn
# (@motorturn) at boot and after a hand.
# 2026-11-24, with p2-acquisition-one-motor.patch: PASS again, the homing
# turn at boot and the acquisition turn after the hand now start the right
# motor the millisecond the left one stops too (Violet's: together), on
# both firmwares; the Terrier's frame is 33860 bytes.
```

The review of Violet's program (`firmware-arm/emulator/tools/violet-vasm/REVIEW.md`, 2026-11-24:
findings by value, what was measured, what the Terrier should know) and its
three other patches, each a hang of Violet's program the Terrier can run into,
against Violet's frame as the control, on both firmwares:

The same program without Antoine Schmitt's clip (TaiChiPop, the 26 KB of
animations it plays only when "moves by itself" is on, off by default on the
Terrier): `still.awk`, built as `Nabaztag_wait_terrier_still.vasm`.
`firmware-arm/emulator/tools/violet-vasm/still-proof.sh` (2026-11-24, PASS
on 2.0b and 1.4b): 3,589 bytes instead of 33,860; from the program's
download to the next poll 12.4 s instead of 18.1 s (the poll interval is
11.8 s of it); ears L5 R10, a click (sd=0003) and a hand (sd=8xxx) as with
the full frame.

```sh
firmware-arm/emulator/tools/violet-vasm/review-proof.sh   # repo root, dev VM, ~15 min
# 2026-11-24: PASS. "stall" (--stall 1: ear 1's motor runs, its wheel does
# not turn, as Lazare's right ear; a hand of 9 holes on ear 0 at 70 s):
# Violet's frame starts ear 1 in the acquisition turn and never stops it,
# no hand report; p3-acquisition-timeout.patch stops it 40 s later and
# reports the hand with that ear at 0 (2.0b: the turn from 76.8 s, ear 0
# done at 85.3 s, ear 1 stopped at 124.2 s, sd=8700 on poll 11; the
# firmware's second is about 1.18 s on the emulator's clock, so 40 show
# as 47).
# "click" (a message waiting, a click at 60 s, an order L5 R10 from 96 s):
# both send sd=0003; Violet's frame never moves its ears again (stuck in
# @sendwaitloop waiting for a message program that never comes);
# p4-click-timeout.patch goes back to @startwait 40 s after the click,
# homes the ears again and takes the order (5 and 10 holes). "stall0" (ear
# 0 stalled from boot): Violet's homing deadline lives in r10, which the
# right ear's target overwrites (@reset5); the patched homing (target in
# r13) stops the stalled left motor 40 s after it started. "all14" (eight pairs (14, 1)): Violet's
# frame polls twice in 90 s and never again (the program spins in @info,
# the firmware stops polling, the 1.4b watchdog does not fire);
# p5-info-hops.patch keeps it polling. The 1.4b watchdog never fired in
# any run. The emulator's --stall EAR was added for this proof.
# Other runs of the review, Violet's frame on 2.0b (violet_warren.py
# --source and --down added for them): source 14 = 255 and nothing moves
# in 720 s, the first spontaneous animation at 718 s in a 1400 s run (the
# countdown is 11,776 turns of the wait loop, 59 ms each: 11.5 minutes
# after the ears last moved); the server away for
# polls 6-12 (72-144 s): the firmware re-joins the Wi-Fi at 105 s and 162 s,
# keeps the program, polls on, and takes the next order.
```

A real v1's firmware, read over ICSP with a PICkit (read only):
`firmware-arm/v1-icsp/README.md`, `firmware-arm/v1-icsp/read.sh <out.hex>
[PIC18LF6525|PIC18LF6627]` (MPLAB X 6.20 on the Mac: 6.30 dropped the
PICkit 3; 2026-11-24, Lazare's 1.4b in `firmware-arm/v1-icsp/dumps`);
`no-brownout.py <in.hex> <out.hex>` clears CONFIG2L's BOREN alone, written
with `ipecmd.sh -P18LF6525 -TPPK3 -M -Y -F<out.hex>` and back with the
dump the same way (check the hex is there first: a missing file writes
nothing); its
serial log on a Raspberry Pi:
`firmware-arm/v1-serial/README.md`.

**The Wi-Fi card's station firmware** (does WPA2 have a chance on the v1:
1.7.0 or later): `firmware-arm/v1-icsp/wifi-probe.py` adds 88 bytes to the
1.4b dump that read the card's station identity (RID FD20) just before the
firmware enables it, and keep it in the PIC's unused data EEPROM (Lazare
writes no serial text, so not the serial port). Proven on the emulator
first, witness included:

```sh
firmware-arm/emulator/tools/wifi-probe-proof.sh    # PASS: 05 20 1F 01 01 07 kept, polls as the dump
# 2026-11-24: witness 10 polls, no EEPROM write; probe 10 polls, EEPROM
# 05 20 1F 01 01 07 00 00 (emulated card 1.7.1); no watchdog in either
```

Then on the rabbit, the PICkit on J2 as for the dump, its own adapter on:

```sh
cd firmware-arm/v1-icsp
./wifi-probe.py dumps/violet-v1-1.4b.hex /tmp/probe.hex && ls -l /tmp/probe.hex
ipecmd.sh -P18LF6525 -TPPK3 -M -Y -F/tmp/probe.hex     # write, verify
# release the PICkit (unplug its USB), let the rabbit boot ~30 s, then:
./read.sh /tmp/after.hex PIC18LF6525
./wifi-probe.py --read /tmp/after.hex                # station firmware X.Y.Z
ipecmd.sh -P18LF6525 -TPPK3 -E                       # erase all: -M leaves the EEPROM bytes
ipecmd.sh -P18LF6525 -TPPK3 -M -Y -Fdumps/violet-v1-1.4b.hex   # the original back, verified
```

`-M` alone skipped the EEPROM's FF bytes and its verify failed on the
probe's (2026-11-24): erase first, then the original reads back identical,
EEPROM included.

**Result on Lazare (2026-11-24): station firmware 1.4.2** (EEPROM `05 20 1F
02 01 04`, component 0x1F). Host-side WPA2 needs 1.7.0 or later: not with
this card's firmware, whatever the PIC. The one way left would load a newer
station firmware into the card's RAM at every boot (Intersil's, not free,
about 64 KB from the serial flash): not pursued. Lazare runs its original
firmware again (read back, 0 bytes differ).

**The card itself (FD0B, same day): component 0x800C, hardware 1.0.0**
(EEPROM `05 0B 0C 00 01 00`). 0x800C is among the NIC ids Intersil's
Prism 2.5 station images list (`sf010409.hex`; primary `pk010004.hex`), so
the card could take station 1.7.4 with its matching primary in its own
flash, on a PC with a PCMCIA slot (hostap's `prism2_srec -f`, which checks
compatibility; never the primary alone). The v1's card is a removable
PCMCIA card (BenQ 802.11b). Not done.

The matching Intersil files (primary 1.1.1, station 1.7.4 for flash and for
RAM), not kept here (Intersil's, no licence to redistribute), are fetched
from two mirrors and checked against their hashes:
`firmware-arm/v1-icsp/prism-fw.sh [DIR]` (default `~/.nabaztag/prism-fw`).
Both list NIC 0x800C variant 0 in their own compatibility records. First
try on a PC with a 16-bit PCMCIA slot: the RAM image (`prism2_srec -r`),
gone at power off; the flash (`-f`, primary and station together) only
after.

What could be improved in the v1's firmware, 1.4b and 2.0b, with the
evidence: `firmware-arm/v1-icsp/IMPROVEMENTS.md` (2026-11-24). Its tools:

```sh
cd firmware-arm/v1-icsp
./pic18dis.py dis dumps/violet-v1-1.4b.hex 2170 21D0   # a listing (byte addresses)
./pic18dis.py funcs dumps/violet-v1-1.4b.hex           # call targets, sizes, callers
./pic18dis.py strings dumps/violet-v1-1.4b.hex
./pic18dis.py dead dumps/violet-v1-1.4b.hex            # code no path from the vectors reaches
./pic18dis.py diff dumps/violet-v1-1.4b.hex ../vendor/newbaztag/firmware/original/nabaztag.hex
# 132 functions identical, 89 changed by a few instructions, the rest listed
cd ../emulator
tools/v1-study.sh both /tmp/v1-study          # on the dev VM: the network broken under
                                              # the firmware (server gone, AP gone, no DNS)
                                              # and the OKI registers for a sound at
                                              # 8/16/32 kHz; V1_PORT (28090) and +1
tools/v1-study.sh both /tmp/v1-study sound    # the sound part alone
tools/violet-vasm/v1-audi.py 16000 1.5 a.audi [--low-first]   # a test tone for a real rabbit
```

Can the downloaded program escape Violet's VM and run native PIC code (the
question under "a loader without a PICkit"): `firmware-arm/v1-icsp/VM-ESCAPE.md`
(2026-11-24). **No, on neither firmware.** Measured 2026-11-24/25 on the dev VM:

```sh
cd /tmp/vmesc                                  # a copy of firmware-arm, not local
flock ~/heavy.lock env V1_PORT=28140 sh firmware-arm/emulator/tools/vm-escape-proof.sh
# == 1. static: TBLWT=0 EECON=0 in 1.4b and 2.0b (no native flash-write gadget)
# == 2. 2.0b: vm-audit 6405 stores, destinations A73..CFE, outside arena: 0
#       1.4b: vm-audit 6492 stores, destinations A70..CFE, outside arena: 0
# == 3. control: native code DID write flash row 00100 on the same emulator
# PASS
```

The hostile program (`firmware-arm/emulator/tools/violet-vasm/escape.vasm`) drives every VM store and
load at its largest index; `nabv1 --vm-audit LO HI` records where each lands.
All stay in the VM arena (registers 0xA70-0xA7F, data 0xB00-0xBFF, call stack
0xC00-0xCFF): never an SFR, the hardware stack or EECON1. The emulator now
models the flash self-write (TBLWT holding latches + the EECON1 55h/AAh
unlock, `pic18.c`/`board.c`), which `firmware-arm/emulator/tools/selfwrite_stub.py` exercises as the
control. A loader still needs ICSP (a PICkit) to install the first time.

### The Karotz (Nabaztag v3)

`firmware-arm/emulator/karotz`: OpenKarotz's real CGI, the hardware
recorded, the client under qemu-arm (see its README; `measure.sh` is what a
real Karotz must give for the whole-system emulator).

```sh
tools/karotz-proof.sh                          # the released client asks for its key
tools/karotz-proof.sh --terrier <terrier checkout>   # the Terrier's scenario
```

A whole Karotz's own system, from a dump (`firmware-arm/emulator/karotz/fros/README.md`): its
root ramdisk and its /usr on a mainline ARM kernel in QEMU. The dump stays
outside the repository (it holds the owner's Wi-Fi password). On the dev VM,
one heavy job at a time (the image builds a kernel):

```sh
cd firmware-arm/emulator/karotz/fros
podman build -t fros -f Containerfile .
SECONDS_UP=240 podman run --rm -e SECONDS_UP -v <dump dir>:/dump:ro,Z fros
# 2026-11-17, FR's Karotz: FROS banner, karotz_init.sh, startup.sh, its
# binaries and libraries load; stops at the hardware (wlan0, LED, /dev/rabbit_*)
```

### WPA3 on the Karotz's kernel

Two patches for LapinLibre's Linux 2.6.32.71 (SAE through nl80211 and
mac80211, 802.11w on the RT73 in software), proven in QEMU against a
control kernel without them: `firmware-arm/emulator/karotz/wpa3/README.md`.

```sh
podman build -t karotz-wpa3 firmware-arm/emulator/karotz/wpa3
podman run --rm karotz-wpa3     # control: not connected; patched: connected
```

### LapinLibre, built here, and its key as an image

`karotz/lapinlibre/build.sh` builds LapinLibre (our fork, `LL_COMMIT` in its
Containerfile) in an Ubuntu container and runs its tests; `LL_STAGES=test`
runs the tests alone on the built tree. `karotz/lapinlibre/binfmt-arm.sh`
registers qemu-arm first. A commit that leaves the kernel alone reuses it
(`LL_REUSE_UNCHANGED=1`).

The build runs no tests by default, as for Naboot; `LL_TESTS=1` adds
upstream's whole suite, for a change to LapinLibre's own code (a new
`LL_COMMIT`), and `LL_STAGES=test` reruns it on the built tree.
On the dev VM (4 cores, 2026-11-20): 293 of 297 tests pass in about 39
minutes (the kernel reused, the tests in balanced shards, one per core). The
four left all time karotzVM under qemu-user: its record's order,
karotzvm_handles' memory growth, a tag listener's tone, and
karotzvm_reply_ends, which alone takes 17 minutes to fail (1 of 10 memory
rounds in 900 s). LapinLibre's own CI does not run these; asked upstream.

`karotz/lapinlibre/make-img.sh` turns the built key into **Terrier OS**, our
LapinLibre (its MIT notice kept, our patches on top), in beta. Why a name of
our own: "LapinLibre" is "FreeRabbits" in French. FreeRabbits, whose OS many
Karotz run and who have already helped this project, are on good terms with
us; we do not want to compete with them, to look like we borrow their name,
or to leave owners unsure which system they are installing.
`terrier-os-<version>.img.xz` (one FAT32 partition, label TERRIEROS) for
Raspberry Pi Imager or balenaEtcher, and `terrier-os.rpi-imager.json`, an
Imager repository with that one image. Publish the image in a
`terrier-os-<version>` release (or set `LL_URL` to where it will be), then
commit the json as `karotz/lapinlibre/terrier-os.rpi-imager.json`: owners paste
its raw GitHub address once (App options > Content repository, or
`rpi-imager --repo <url>`) and every later release reaches them there.
Terrier OS appears by name, downloaded and checked by its sha256. Pick any
device. Imager's OS customisation fills the Wi-Fi network, the name and the
SSH key (our LapinLibre reads its `network-config` and `user-data`;
`lapinlibre.txt`, still its name on the key, wins); the user account and the
locale it asks for are ignored.

### Publishing a Terrier OS build

As done for `terrier-os-v0.2.0-50-ga60c9e8f14c5` (2026-11-20). From this
repository's root on the dev VM, one heavy job at a time:

1. Point `LL_COMMIT` in `karotz/lapinlibre/Containerfile` at the fork's
   commit to ship, then build: `sh karotz/lapinlibre/build.sh`
   (`LL_TESTS=1` when LapinLibre's own code changed). The key lands in
   `~/lapinlibre-work/usbkey`.
2. Make the image: `sh karotz/lapinlibre/make-img.sh ~/lapinlibre-work/usbkey out`.
   The version is the key's `BUILD_VERSION` (`install.conf`, the fork's
   `git describe`), e.g. `v0.2.0-50-ga60c9e8f14c5`. `out/` then holds
   `terrier-os-<version>.img.xz`, its `.sha256` and
   `terrier-os.rpi-imager.json`, which already points at the release of step 4.
3. Install it on a real Karotz from the image (Imager or balenaEtcher, then
   the key in the rabbit) and check that it starts, joins the Wi-Fi and
   reaches the Terrier. Not before this.
4. Publish, as a pre-release while Terrier OS is in beta:
   ```sh
   gh release create terrier-os-<version> -R ClaraVnk/nabaztag --prerelease \
     --title "Terrier OS <version> (beta)" --notes-file notes.md \
     out/terrier-os-<version>.img.xz out/terrier-os-<version>.img.xz.sha256 \
     out/terrier-os.rpi-imager.json
   ```
   Then download the release into an empty directory and run
   `sha256sum -c terrier-os-<version>.img.xz.sha256` there.
5. Copy the json from out/ over
   `karotz/lapinlibre/terrier-os.rpi-imager.json` and commit it (a PR): the address owners
   pasted into Imager reads this file, so the new build reaches them only
   once it is merged.
6. On the Terrier (its private repository), add the build to
   `TERRIER_OS_BUILDS` in its karotz module (`"<version>": "<short name>"`). The client (0.1.16 and
   later) reports `lapinlibre <version>`, and without this line the rabbit's
   page and the admin table call the rabbit "LapinLibre". That file is in the
   core: it goes out with the next grouped core deploy.
7. The update from the rabbit's page (Karotz client 0.1.17 and later):
   `python3 karotz/lapinlibre/make-ota.py out/terrier-os-<version>.img.xz out --priv ~/.nabaztag/karotz_signing_key.bin`
   makes `terrier-os-<version>.ota.tar`, its manifest signed with the Karotz
   client key: on Clara's explicit request only, as every signature.
   Check it (`--check out/terrier-os-<version>.ota --pub` the Terrier's
   `karotz_signing_pubkey.h`), attach the `.ota.tar` to the release, then
   on the VPS: `podman cp` it into the core container's /tmp and run
   `podman exec terrier-core python3 -m terrier.terrieros /tmp/terrier-os-<version>.ota.tar`.
   It keeps the package only if it holds as the client checks it, and makes
   it the one offered (`/data/ota/terrier-os/LATEST`). Every Karotz on
   Terrier OS with an older build then sees "Update Terrier OS" on its
   firmware card. Prove it first in the emulator: `karotz/ota`,
   `OTA_MODE=package`.

### The three rabbits at once

Every new rabbit feature is proven on the v1, the v2 and the v3:

```sh
tools/rabbits-proof.sh --terrier <terrier checkout> --v2 "<xmpp-tls-proof.sh inputs>"
```

One verdict per rabbit; one without its inputs is NOT RUN, never passed.

## 5. The runtime bytecode

Details: [`bytecode/README.md`](../bytecode/README.md).

The two runtimes the warren serves, rebuilt from this repository:

```sh
# today's: baseline-https (Naboot 1.2.x) and baseline-tls (firmwares 1.0.0.x)
RUNTIME=https bytecode/build-bytecode.sh <tree> firmware-arm/tools/mtl_comp.py /tmp/baseline-https.hybrid
RUNTIME=tls   bytecode/build-bytecode.sh <tree> firmware-arm/tools/mtl_comp.py /tmp/baseline-tls.hybrid
# the control: the ones served now, byte for byte (prints match or not)
bytecode/reproduce-served.sh https /tmp/served-https.hybrid
bytecode/reproduce-served.sh tls   /tmp/served-tls.hybrid
```

The add-on ships both, built by these two commands: `nabaztag-violet/bootcode.https` and
`bootcode.tls` (checksums in its Dockerfile).

`<tree>` is a copy of `firmware-arm/vendor/openjabnab/bootcode`. A new
runtime is proven in the emulator against that control (same scenario, the
control first) before it goes to `/data/bytecode/`.

```sh
cp -r firmware-arm/vendor/openjabnab/bootcode /tmp/tree     # stock Violet sources
bash bytecode/build-bytecode.sh /tmp/tree firmware-arm/tools/mtl_comp.py /tmp/runtime.hybrid
```

The script applies every patch in order. It checks that each policy literal
(`xmpp-tls:0`, `xmpp-ota:0`, ...) is in the result exactly once, because the
Terrier flips them per rabbit. Prove it in the emulator (section 4,
`--bytecode`). Then serve it to one rabbit first: the Terrier's
RUNBOOK, "The runtime bytecode".

When an update does not install on a real rabbit and the emulator says it
should (2026-11-13: Naboot 1.2.0.0 on the HTTPS runtime), build a test
runtime that reports each step of the update to the warren, serve it to that
rabbit alone (`bytecode: <name>` in its settings, admins only), and read the
Terrier's log for `<ota>` while the owner clicks "Update this rabbit":

```sh
OTA_REPORT=1 bash bytecode/build-bytecode.sh /tmp/tree firmware-arm/tools/mtl_comp.py /tmp/otareport.hybrid
# announced N buffer-ok | received N | installing N | collected, verifying | signature ok, flashing
```

The last step logged is where it stopped; the next boot's reset report
(`verify`, `verify_ok`, `flash` in rabbit_events) says what happened after.
Never in a release: `bytecode/patch_ota_report.py`.

### Loading a new runtime without unplugging (RB)

`bytecode/patch_reload.py` (2026-11-23): the warren sends `RB` in the
session, the runtime calls Violet's reboot (sysReboot, the reset_uc an update
ends with), and the rabbit boots again on the runtime served now. The warren
side is `POST /api/rabbits/<mac>/reload` (Naboot v2 only). Proof, control
first (a runtime without the patch ignores RB):

```sh
cd firmware-arm/emulator
tools/xmpp-tls-proof.sh --terrier <terrier image with /reload> --bytecode <runtime.hybrid> \
    --emulator <emu of naboot-1.2.1.3> --elf <Nab.elf> --out /tmp/rl \
    --scenario scenarios/vm/xmpp-reload.txt --at 100 reload '{}'
# fix: "the rabbit restarts after its reset (reason 0)", a third "serving
# bytecode" and a new session in /tmp/rl/terrier.log; control: neither
```

## 6. The JTAG station

Details: [`firmware-arm/jtag/README.md`](../firmware-arm/jtag/README.md) and
[`firmware-arm/jtag/RUNBOOK.md`](../firmware-arm/jtag/RUNBOOK.md); wiring in
`firmware-arm/jtag/wiring.svg`.

```sh
./firmware-arm/jtag/station.sh setup          # once, on the Pi
./firmware-arm/jtag/station.sh check          # JTAG answers, the flash reads back
./firmware-arm/jtag/station.sh listen         # the live status line, no halt
./firmware-arm/jtag/station.sh dump out.bin   # what the rabbit really runs
./firmware-arm/jtag/station.sh flash firmware-arm/releases/naboot-1.2.1.0.signed.sim
```

Never halt a rabbit during its association (it can lose its Wi-Fi), never
arm the watchdog over JTAG, and read symbols only from the ELF of the build
that is in the rabbit.

## 7. A rabbit that does not come back

[`firmware-arm/docs/RECOVERY.md`](../firmware-arm/docs/RECOVERY.md): the
setup page and the release first, the JTAG station last.

## Karotz: measure before installing

A factory Karotz (no network, no SSH) is read with a USB key before anything
is installed on it: `karotz/measure-key/README.md`
(`sh karotz/measure-key/make-key.sh /Volumes/KAROTZ`). Read-only on the
rabbit; the flash copy and the readings land on the key under `measure/`.

### Config mode is a captive portal

`emulator/tools/config-captive.sh firmware-arm/bin/naboot-1.2.1.0/Nab.elf`
runs `scenarios/vm/config-captive.txt`: a phone on the rabbit's setup network
asks the rabbit's DNS for `captive.apple.com` and
`connectivitycheck.gstatic.com` (with an EDNS record, as iOS sends one), then
fetches `/hotspot-detect.html`. Both names must resolve to 192.168.0.1 with no
additional record echoed, and the probe page must get the setup page (200):
that is what makes a phone open the setup page by itself. Before the
`captive` mod (2026-11-20) nothing answered on port 53, and Chrome on iOS
refused the network ("Internet déconnecté"). Cost: 392 bytes of flash.

### A stopped USB host restarts the rabbit

`emulator/tools/usb-watch-proof.sh firmware-arm/bin/naboot-1.2.1.0/Nab.elf`
runs `scenarios/vm/usb-host-stop.txt` with the 1.2.1.0 steps
(`firmware-arm/emulator/tools/emu-steps.py`): the emulated USB host runs 12 s with no reset, then
stops; the firmware must restart with reason 5 (`usb_watch`, 25 radio-timer
ticks: 5 s on the rabbit, 10 s in the emulator, whose tick is 400 ms).
Measured cause, 2026-11-20 over JTAG on Nabazprout: HcControl 0 and
HcFmNumber frozen after the JTAG wires were plugged into the running rabbit;
the radio went silent, COMRAM filled with unsent frames, and the scan sat on
channel 1 until the mains.

`naboot-1.2.1.0-tel` is the release plus `dcc_telemetry`, for the JTAG
station only (listen.sh): the first warm boot after an OTA left the radio
deaf twice on Nabazprout (2026-11-18, 2026-11-20).

### A stricter access point: the EAPOL replies' length (Livebox 4)

`firmware-arm/emulator/tools/livebox-check.sh` (usage in its header) runs every
`scenarios/vm/livebox-*.txt` under the runtime VM. `livebox-wpa2-strict-eapol.txt`
puts `strict-eapol` on the virtual AP: it drops an EAPOL-Key frame whose
declared body is longer than its fields and key data. Before the
`eapol_exact_body` step every reply the rabbit sent declared 28 bytes too
many (the key_data member counted on top of the key data), and that
scenario ended in EAPOL, which is Lapinou's diagnostic behind a Livebox 4
(issue #163, 2026-11-20: E1, k0, D0/0/0; the same rabbit joins a GL.iNet in
access-point mode behind it). hostapd only checks that the key data fits.
With the step, the seven scenarios and `make check` pass. Whether the Livebox
4 is that strict is for a rabbit on one to tell: `naboot-1.2.1.1` is 1.2.1.0
plus this step. It joined Lapinou's Livebox 4 on 2026-11-20 at 23:39.

### An AP offering WPA and WPA2 together (MikroTik)

`firmware-arm/emulator/scenarios/mikrotik-wpa-wpa2.txt`, in `make check`:
`wpa1-too` makes the virtual AP add the WPA1 vendor element (CCMP) after its
RSN element, as RouterOS does with `wpa-psk,wpa2-psk`, and refuse a WPA1
request for TKIP (status 41, as hostapd). Before the `scan_prefers_rsn` step
the scan took such an AP for WPA: configured WPA, the rabbit refuses it
(`no_tkip_pairwise`); configured WPA2, OPnetAuth never calls netAuth. Lapinou's
line behind his MikroTik (issue #163, 2026-11-21): W0 a0 e20, scanning
forever. To see it fail, build the image with run.sh's STEPS minus
`scan_prefers_rsn`. `naboot-1.2.1.2` is 1.2.1.1 plus this step; until a
rabbit runs it, the AP set to WPA2 only is the workaround.

### A Nabaztag v1's serial log on a Raspberry Pi

`firmware-arm/v1-serial/` (README there): two jumpers from the v1's header J3
(its PIC's UART1, 9600 baud; pin 4 TX and pin 2 GND on Violet's FCC
schematic) to the Pi's pin 10 (RXD) and pin 6 (GND), drawn by
`mkwiring.py` into `wiring.svg`; `listen.sh` prints the log on the Pi, each
line timed. Read only: nothing is sent to the rabbit, the Pi does not power
it. Our v1 runs firmware 1.4b (`v=14` in its polls, 2026-11-21); the
emulator's dump is 2.0b.

### Terrier OS's install, run on a simulated flash (the ground of its update)

`firmware-arm/emulator/karotz/ota/` (usage in its Containerfile) boots the
Karotz's 2.6.32.71 on QEMU Versatile with nandsim cut as the Karotz's flash
(mtd1 3 MiB, mtd2 5 MiB, 128 KiB blocks; 2 KiB pages assumed), a released
Terrier OS image's own root filesystem as the initramfs (unpacked by
`uncramfs.py`: 7z dropped busybox's links, 2026-11-21), and its key's files
in /mnt/usbkey; a /usr stands for an installed rabbit's (the image's /usr,
settings, Wi-Fi, the client's folder `/usr/etc/conf/terrier` with a key and
a newer client). `init.sh` runs the key's own `autorun` and checks, after it
returns: the kernel and the root filesystem read back from the flash
(nanddump) equal the key's, the build is recorded, the settings, the Wi-Fi,
the adoption key and the client the Terrier sent are kept, the image's
client is shipped beside them. All ten pass on `v0.2.0-50` (2026-11-21). A
key placed outside `/usr/etc/conf` (an `install.sh` rabbit's `/usr/terrier`)
is wiped: the key check tells the two apart.

`OTA_MODE=package` (`podman run --rm -e OTA_MODE=package karotz-ota`) is the
update from the rabbit's page: no stick, the package in the client's folder
(`/usr/etc/conf/terrier/os-update`, which installs keep) bound on
/mnt/usbkey where the install reads its stick. The same ten checks pass, and
the package outlives the install, for the client to remove (2026-11-21).

`OTA_MODE=package-usr-terrier` is Gengar's case (2026-11-21, the first update
on a real Karotz): a client installed by `install.sh` lives in `/usr/terrier`,
which the install empties, and the package there went with it while the
install read it: "ERROR: the settings in lapinlibre.txt were not applied",
the red nose, the adoption key and the client lost. It fails as Gengar did;
the client (0.1.18) puts the package in `/usr/etc/conf/os-update` and refuses
when it does not live in `/usr/etc/conf` itself.

`karotz/lapinlibre/make-ota.py` makes that package from a released image (or
a key dir): the key's files and a `manifest` (version, build, then
`file <sha256> <size> <path>` per file), whose bytes the Karotz client key
signs into `manifest.sig` (`--priv`, on Clara's explicit request only).
`--check` does what the client will: the signature on the manifest alone,
then each file's size and sha256, nothing on disk it does not name. The
client never needs the 24 MB in RAM at once. `test-make-ota.sh` (usage in
its header) runs it with a throwaway key: made, signed, accepted, and
refused once one byte of the kernel changed. The package carries no
settings: its `lapinlibre.txt` is emptied (empty keeps the rabbit's), which
the test checks. The stick's own said `TERRIER=<host>`, and an update would
have rewritten the client's settings and drawn a new web password (found by
`karotz/ota-live`, 2026-11-21).

### Terrier OS's update with the whole rabbit running

`firmware-arm/emulator/karotz/ota-live/` (usage in its Containerfile, built
from the repository's root) boots a released Terrier OS for real on Linux
6.12 / QEMU Versatile: its own linuxrc, init, `startup.sh`, D-Bus, the
daemons and the controller (../fros's stand-ins for the devices), the
client from `/usr/etc/conf/terrier`, nandsim as the flash. Its only changes
to the rabbit (`root.sh`): /usr on a disk, QEMU's network card for the
Wi-Fi (with the loopback, which the client needs), the proof's hook before
`startup.sh`'s `exit 0`, and a `/sbin/reboot` that copies mtd1/mtd2 first.
Once up, `phase.sh` runs the install of `make-ota.py`'s package with the
client's own command (0.1.18), then the rabbit boots the root filesystem the
install wrote with the /usr it left. Checks: the client and the controller
run during the install; after it, the build runs and is recorded, the Wi-Fi,
voice, adoption key, client settings and the Terrier's client are kept, the
client and the controller run again. All pass on `v0.2.0-50` (2026-11-21,
about 25 minutes on dev-loutre). Not covered: the radio (Gengar lost its
Wi-Fi after the power cycle), the real flash's timing.
A build not released yet is the update when its key's directory is mounted
on `/key` (`podman run --rm -v ~/lapinlibre-work/usbkey:/key:ro,Z
karotz-ota-live`): the rabbit runs the released image and updates to it.

### Linux 6.1 for the Karotz: the size

Step 1 of `docs/TERRIER-OS-KERNEL.md`: does a useful 6.1 fit the Karotz's
3 MiB kernel partition? `karotz/kernel/Containerfile` fetches Linux 6.1.192
(kernel.org's sha256) and `karotz/kernel/size.sh` builds tinyconfig plus
`karotz/kernel/karotz-6.1.config`, lists every asked option Kconfig did not
keep (an empty list or the size means nothing), and prints the xz zImage's
size against 3145728 bytes:

    podman build -t karotz-kernel61 -f karotz/kernel/Containerfile karotz/kernel
    podman run --rm karotz-kernel61

### Linux 6.1 for the Karotz: the boot in QEMU

Step 2: the same kernel in QEMU's S3C2440 (Michel Pollet's mini2440 machine,
ywandy's fork, pinned in `karotz/kernel/qemu-mini2440/Containerfile`) to a
shell on LapinLibre's root file system. `direct-boot.py` boots `-kernel` as
the Karotz's locked bootloader does (ATAGs, a machine number, the watchdog
off, no U-Boot) and moves the initrd clear of the decompressor;
`karotz-6.1-debug.config` adds the early UART and the initrd, and leaves out
the NAND driver (QEMU's NAND is not one 6.1 identifies; the NAND is step 3,
on nandsim). On the dev VM, from the repository root:

    podman build -t qemu-mini2440 -f karotz/kernel/qemu-mini2440/Containerfile karotz/kernel/qemu-mini2440
    podman build -t karotz-kernel61 -f karotz/kernel/Containerfile karotz/kernel
    mkdir -p /tmp/kz && podman run --rm -e EXTRA=/k/karotz-6.1-debug.config -e OUT=/out \
        -v /tmp/kz:/out:Z karotz-kernel61
    zcat <terrier-os package>/rootfs.img.gz > /tmp/kz/rootfs.cramfs
    karotz/kernel/qemu-mini2440/boot.sh /tmp/kz
    # 2026-11-22: Linux 6.1.192, ARM920T, cramfs root, "PASS: a shell on the 6.1 kernel"

### Linux 6.1 for the Karotz: YAFFS1

Step 3. The 6.1 kernel carries YAFFS from Yocto's 6.1 tree
(`karotz/kernel/yaffs2/`, its README) with two changes, kept apart: YAFFS1
was refused on 512-byte pages (`yaffs1-small-page.patch`), and its tags
were the 16-byte spare of before 2.6.17 where the Karotz's 2.6.32 wrote
8-byte packed tags in the free OOB (`yaffs1-mtd1/`, 2009's
`yaffs_mtdif1.c` ported). nandsim gets `noecc=1`, as the Karotz's NAND
runs (`nandsim-noecc.patch`, the emulator's kernel only). With the zImage
and rootfs of the step above:

    karotz/kernel/qemu-mini2440/yaffs.sh /tmp/kz
    # 2026-11-22: "PASS: YAFFS1 written and read back on 6.1" (mount -t yaffs
    # on a blank 256 MiB NAND of 512-byte pages, a directory and a file,
    # unmount, mount, read back)

What is not proven yet: the /usr of a real Karotz. The data-only dump of
2026-11-17 cannot be mounted (no OOB, so no tags); `karotz/measure-key`
now dumps the YAFFS partitions with their OOB on Terrier OS.

### A night without Wi-Fi (Linotte)

A box that turns its Wi-Fi off for the night left Linotte "orange fixe"
until unplugged (2026-11-21, 23:45 to 04:30). The firmware alone rejoins
after five hours (`firmware-arm/emulator/scenarios/night-wifi-off.txt`); the whole rabbit did
not: after 3 h 12 without Wi-Fi the runtime ran out of memory and reset in
a loop. Every reconnect attempt started a DNS lookup chain that never ended
(`bytecode/patch_xmpp_dns_chain.py`). `fast <from> <to>` in a scenario runs
a stretch of a `realtime` run unpaced, so the night takes six minutes:

    tools/xmpp-tls-proof.sh --terrier ghcr.io/claravnk/terrier:latest \
        --bytecode <runtime.hybrid> --emulator <naboot-1.2.1.2 emulator image> \
        --elf <Nab.elf> --out <dir> --scenario scenarios/vm/xmpp-night-wifi-off.txt
    # 2026-11-22, runtime without the fix: heap 100% at 11,505,850 ms,
    #   20,880 resets, final state IDLE
    # with patch_xmpp_dns_chain.py: no reset, heap 48%, the TLS session back
    #   after the Wi-Fi, final state RUN

### Linux 6.1 for the Karotz: its own board file

Step 4. `karotz/kernel/rabbitv3/` is the Karotz's board file ported from
LapinLibre's 2.6.32 (machine RABBITV3, 1912) and `hook.sh` puts it in the
6.1 tree (mach-types, Kconfig, Makefile). Three faults of 6.1 found on the
way, each a patch of its own: the SPI controller had no clock lookup and an
unprepared clock (the board file supplies both), its driver put a devres on
the SPI device before probe so nothing could bind
(`spi-s3c24xx-devres.patch`), and spidev no longer binds "spidev"
(`spidev-karotz.patch`, the LED driver is "karotz-leds"). With the zImage
and rootfs of the steps above:

    MACH=1912 karotz/kernel/qemu-mini2440/boot.sh /tmp/kz
    # 2026-11-22: "Machine: RABBITV3 (KAROTZ)", /dev/spidev0.1, PASS
    # (QEMU's OHCI fails the same under mini2440's board: the emulator's, not ours)

### Linux 6.1 for the Karotz: a real Karotz's NAND

Step 3 on a real flash. `karotz-flash.py` puts the measure key's copies back
into one NAND image, each partition at its own offset (Gengar's dmesg: the
partitions have gaps); QEMU emulates the chip the factory kernel names in the
measure's dmesg (Gengar: Hynix `0xDA`, 256 MiB of 2 KiB pages with 64 bytes of
spare; the 512-byte `0x71` was a guess) with the clocks its bootloader leaves
(405/135/67.5 MHz); the kernel built with `karotz-6.1-realnand.config` reads it:

    karotz/kernel/qemu-mini2440/real-usr.sh <dir> <measure dir>
    # 2026-11-22: "nand: Samsung NAND 256MiB", Gengar's 8 partitions, data
    #   byte-identical to the copy; /usr mounts EMPTY: the copy's spare is not
    #   real (busybox nanddump -o put file text there, 3,445 blocks "bad")

Two 6.1 faults found on the way: the NAND driver read the chip before
enabling its controller (`s3c2410-nand-inithw.patch`), and QEMU's reset
clocks left no timing for the board's 20 ns. The spare is now read by
`karotz/measure-key/oobdump` (MEMREADOOB, every page, bad blocks included),
proven first against known spares:

    karotz/kernel/qemu-mini2440/oob-proof.sh <dir> <oobdump binary>
    # 2026-11-22: the two spares written into the flash come back, PASS

Gengar's measure with oobdump (2026-11-23): 119 808 pages of 2 112 bytes in
mtd6, real YAFFS2 tags, no bad block. Above 128 MiB a 2 KiB-page chip takes a
third row address byte; QEMU's `nand.c` ran the read at the fourth and kept
32 bits, so every page past 65535 read as the one 64 Ki pages below, and /usr
mounted with half its directories. `direct-boot.py` now waits for the fifth
byte on such a chip and holds the address in 64 bits. `nand-compare.sh` reads
the flash back under 6.1 (mtd5 with its spare through oobdump, mtd7, and 8 MiB
slices across mtd6) and compares it with the measure:

    karotz/kernel/qemu-mini2440/nand-compare.sh <dir> <measure dir> <oobdump binary>
    # control (QEMU without the fifth byte): mtd7 and mtd5 DIFFERENT, FAIL
    # 2026-11-23: every slice the same, PASS
    karotz/kernel/qemu-mini2440/real-usr.sh <dir> <measure dir>
    # 2026-11-23: bin devtools etc karotz lib sbin scripts share spool www,
    #   VERSION=v0.2.0-58-g19ea8d7cb2db (Gengar's own), PASS

### Linux 6.1 for the Karotz: its own system

Step 4. `real-boot.sh` boots the 6.1 kernel in QEMU on the Karotz's own
ramdisk (mtd2, a gzipped cramfs) with the command line its bootloader gives
(Gengar's `/proc/cmdline`), the NAND rebuilt by `karotz-flash.py`:

    karotz/kernel/qemu-mini2440/real-boot.sh <dir> <measure dir> [seconds]
    # 2026-11-23: "Run /linuxrc", /usr mounted rw from the NAND, "[START]
    #   starting init", its services up; those that need Wi-Fi fail (no
    #   wlan0: QEMU has no USB here)

Two things on the way. The key's `nanddump -o` copies (kernel, ramdisks)
keep each 2 KiB page's 64-byte spare after it: `karotz-flash.py` and
`real-boot.sh` take the data out of them (read whole, the ramdisk would not
inflate). And QEMU's NAND never said ready (status bit 6): 6.1 warned at
every operation, 45 000 console lines, and /usr was still mounting after
five minutes; `direct-boot.py` now answers ready.

### Linux 6.1 for the Karotz: its own drivers

Step 5. `karotz/kernel/rabbitv3/rabbitv3-chardevs.c` ports LapinLibre's
`rabbit_button`, `rabbit_poweron` and `rabbit_ears` (majors 52, 53, 60) to
6.1 with the same bytes and ioctls, so the rootfs runs unchanged; the board
file adds the Samsung PWM and the ears' channels by name. `boot.sh` with
MACH=1912 now also checks the three drivers probed and reads one byte from
/dev/rabbit_poweron:

    MACH=1912 karotz/kernel/qemu-mini2440/boot.sh /tmp/kz
    # 2026-11-22: SPIDEV-OK, the three /dev/rabbit_* drivers, PO "1", PASS

Not proven in QEMU: the ears moving (no motors, no encoder) and the
encoders' trigger, left as the bootloader set it as 2.6.32 did.

### Terrier OS's setup network, without the radio

`karotz/lapinlibre/test-hotspot.sh` (usage in its header) runs the pieces of
the setup network with a built key's own binaries under qemu-arm: the scan's
parser on a real `iwlist` listing (strongest first, WPA3/WPA2/WPA/open, hidden
and quoted names left out), `dnsmasq` as `hotspot.sh` starts it (every name is
the rabbit; AAAA and HTTPS answered empty, where busybox `dnsd` answered NOTIMP
and iPhones never opened the page, 2026-11-21), and `httpd` with the setup
rules (the phones' check paths answer 200 and lead to `setup.html`; the old
E404 rule named the setup page at the root and served busybox's own 404).
The radio itself (the scan, the access point) is only proven on a Karotz.

### Terrier OS in Raspberry Pi Imager, and the guide's screenshots

Imager 2.x asks for the device before the system: `terrier-os.rpi-imager.json`
carries `imager.devices` (one, Karotz, tag `karotz`) and each system its
`devices`, or the list stays empty and Next greyed (found 2026-11-20 with the
tool below; `make-img.sh` writes both). The Karotz guide's screenshots come
from `karotz/lapinlibre/imager-shots/` (Imager 2.0.12 on Xvfb, steps in
`shots.sh`): repository URL, device, system, Wi-Fi; Ubuntu by exception,
removed after use.
