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
```

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

On the dev VM (4 cores, 2026-11-20): 293 of 297 tests pass in about 39
minutes (the kernel reused, the tests in balanced shards, one per core). The
four left all time karotzVM under qemu-user: its record's order,
karotzvm_handles' memory growth, a tag listener's tone, and
karotzvm_reply_ends, which alone takes 17 minutes to fail (1 of 10 memory
rounds in 900 s). LapinLibre's own CI does not run these; asked upstream.

`karotz/lapinlibre/make-img.sh` turns the built key into
`lapinlibre-<version>.img.xz` (one FAT32 partition, label LAPINLIBRE) for
Raspberry Pi Imager or balenaEtcher: write it, edit `lapinlibre.txt` on the
key, plug it into the Karotz.

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

## 6. The JTAG station

Details: [`firmware-arm/jtag/README.md`](../firmware-arm/jtag/README.md) and
[`firmware-arm/jtag/RUNBOOK.md`](../firmware-arm/jtag/RUNBOOK.md); wiring in
`firmware-arm/jtag/wiring.svg`.

```sh
./firmware-arm/jtag/station.sh setup          # once, on the Pi
./firmware-arm/jtag/station.sh check          # JTAG answers, the flash reads back
./firmware-arm/jtag/station.sh listen         # the live status line, no halt
./firmware-arm/jtag/station.sh dump out.bin   # what the rabbit really runs
./firmware-arm/jtag/station.sh flash firmware-arm/releases/naboot-1.2.0.1.signed.sim
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
