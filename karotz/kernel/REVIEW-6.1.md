# Linux 6.1 for the Karotz: review of 2026-11-24

Read: `karotz/kernel/` (Containerfile, the four configs, `rabbitv3/`, the
seven patches, `qemu-mini2440/`), `karotz/kexec/`, `karotz/lapinlibre/`,
the RUNBOOK's 6.1 sections and FLASH-PLAN.md; LapinLibre's 2.6.32 tree on
the dev VM (`~/lapinlibre-kexec/ll/work/system-kernel/linux-2.6.32.71`,
read only) and the 6.1 sources in the `karotz-kernel61` image; Gengar's
2.6.32 dmesg from the measure key; the Terrier client (`terrier/karotz/src`,
read only). Nothing was run on a Karotz. Each finding names its evidence;
what could not be established is said so. Ranked by what Terrier OS 1.0.0
(6.1 in the flash) needs first, then by value over cost.

## 1. Blocking for the flash install, or close to it

### 1.1 The USB hub: a power problem is a reading problem

`usb-bus-power-rabbitv3.patch` lets every configuration through whatever
its current. The 2.6.32 kernel has **the same rule** (its
`drivers/usb/core/generic.c:100`, `bMaxPower * 2 > udev->bus_mA`) and the
same budget for a bus-powered hub (`hub.c:1102-1114`, 100 mA a port), and
under it the rt73 and the key behind the TI TUSB2046 hub (0451:2046) were
never refused. Gengar's 2.6.32 dmesg (measure of 2026-11-23, lines
`hub 1-1:1.0`) says why: "local power source is good", no "insufficient
power" line, the hub's GET_STATUS answered **self-powered**. Under 6.1 the
same hub answered bus-powered, and on other boots a descriptor with no
ports ("hub doesn't have any ports") or one that is not a hub's at all
("bad descriptor, ignoring hub", `hub.c:1938`, `hub_descriptor_is_sane`),
and a device descriptor that read empty without `old_scheme_first`.

Four different control-IN answers wrong, on one device, intermittently:
that is **control transfers returning stale or wrong data**, not a hub
that lacks power. The two workarounds in place (`old_scheme_first=1`, the
power patch) each hide one symptom of it; the next symptom will be a
configuration or string descriptor of the rt73 read wrong. Candidates, in
the order to measure (needs Gengar; QEMU's OHCI answers HcRevision 0 under
2.6.32 as under 6.1, `/tmp/kx/kexec-proof.log:1154` and `:2644`, so the
emulator cannot show it):

1. Cache coherency of the HCD's small buffers on the ARM920T (VIVT,
   write-back). 2.6.32 allocated the hub's status buffers with
   `usb_buffer_alloc` (coherent memory); 6.1 kmallocs them and maps them
   per transfer (`dma_map_single`). `CONFIG_ARM_L1_CACHE_SHIFT=5` and
   kmalloc's minimum alignment should keep them in their own lines; worth
   reading `/sys/kernel/debug` is not there, so: boot once with
   `usbcore.old_scheme_first=0` and the power patch out, log
   `usb 1-1` status reads, and compare a run with `CONFIG_DMA_API_DEBUG=y`.
2. The UPLL: 2.6.32 writes MDIV 56 / PDIV 2 / SDIV 2
   (`mach-rabbitv3.c:84-91`, VCO 192 MHz); 6.1's table gives 48 MHz as
   120 / 2 / 3 (`clk-s3c2410.c:120-146`, VCO 384 MHz), and
   `clk_set_rate(upll)` in `mach-rabbitv3.c:468` ignores its return. Under
   kexec the PLL is still 2.6.32's (the rate is already 48 MHz, the clock
   core writes nothing); **from the BIOS it will be whatever the BIOS left
   or 6.1's own values**: the flash boot is the first time 6.1's UPLL
   setting runs. Read UPLLCON (0x4C000008) under both kernels in
   `autorun`'s register dump (one line to add beside the GPIO dump) before
   the flash install.
3. `clk_ignore_unused` is on every kexec command line
   (`karotz/kexec/autorun:120`) and will not be on the BIOS's. QEMU's
   `real-boot.sh` runs without it and the NAND and SPI survive the gating,
   but USB in QEMU is dead anyway: whether a gated clock (uclk's parent,
   the camera's, the ADC's) matters to the OHCI is not shown.

Until the cause is found, the flash kernel should keep both workarounds
(they are harmless) **and** `os-prelude`'s rebind loop should move into
the ramdisk's own boot (it is only in the kexec key today,
`karotz/kexec/os-prelude:26-49`): a hub that reads wrong at boot otherwise
leaves the rabbit without Wi-Fi until the next power cut.

### 1.2 The belly's colours: the pulse, not the bytes

The LED frames are not where to look. led-daemon sends **two-byte SPI
messages** (`SPI_IOC_MESSAGE(1)`, len 2, full duplex: its disassembly at
0x1010-0x1064, the ioctl word 0x40206b00 at 0x10f8) over the same
`spi-s3c24xx` register path as 2.6.32 (`s3c24xx_spi_update_state` is
unchanged; CS is the GPIO line, active low in both: 2.6.32 `set_cs`
`mach-rabbitv3.c:533-539`, 6.1 `GPIO_ACTIVE_LOW` in the lookup
`rabbitv3/mach-rabbitv3.c:212`). A static colour through the local API
("green stays green") is proof that bytes, order and chip select are
right.

What differs is the **animation**. The Terrier client never asks for a
static yellow: e-mail is `leds?color=FFFF00&pulse=1&color2=000000&speed=600`
(`terrier/karotz/src/rabbit.c:145`, `ok.c:96`), which LapinLibre turns into
led-daemon's `pulse(id, FFFF00, 000000, 600, -1)` (`web.sh:814`), and
led-daemon steps its pulses and fades with a **30 ms glib timeout**
(`g_timeout_add_full(..., 30, ...)` at 0x1a3c and 0x1cfc). Under 6.1 that
timer runs on a kernel with `HZ=100` and no high-resolution timers
(tinyconfig's defaults; 2.6.32's rabbitv3 config had `HZ=200`, also
without hrtimers): every `poll()` timeout is rounded to 10 ms ticks instead
of 5, and a pulse whose 30 ms steps land on 30 or 40 ms is the first
place a wrong intermediate colour can come from. It is a hypothesis, not a
measure: **`CONFIG_HZ_200` is now in the config** (the known-good value;
size unchanged) and the proof is Gengar's belly on an e-mail. If yellow
still comes out blue, the next measure is the bytes: `ledmon.txt`'s
D-Bus orders beside a `strace -e ioctl -x` of led-daemon under both
kernels (busybox has no strace; the tool would have to ride on the key).

### 1.3 YAFFS written by 6.1: still the open risk of the plan

Nothing new found; stated for the ranking. `yaffs.sh` proves YAFFS1 on
512-byte pages, Gengar's `/usr` is YAFFS2 on 2 KiB pages, and 6.1 has
only ever read it (FLASH-PLAN §3). The `yaffs1-mtd1.patch` tag format
does not apply to YAFFS2, so what is unproven is upstream Yocto YAFFS2's
write path on this chip, with 2.6.32 reading it back. FLASH-PLAN step 5's
round trip is the right proof; `mtd7` as the first real write is right.

### 1.4 The way back needs the 2.6.32 zImage on the key under both names

`autorun` keeps `$K/zImage` (2.6.32) for `go-2632`; FLASH-PLAN step 3's
`go-back` will need it too, and the kexec key's `autorun` overwrote the
install's (`make-key.sh:42-43`, kept as `autorun.install`). Fine as is;
only: the kexec `autorun` runs `/tmp/kexec -e` with `/usr` and the key
remounted read-only but **without `sync` after the watchdog is armed and
before the remount** (`autorun:134-140`: `sync` at 138 is after the
remounts, which is enough). No change needed; noted because the
watchdog is already counting there (~30 s) and a slow key could eat it.

## 2. Correctness and robustness (fixed in this branch unless marked)

### 2.1 Syscalls the kernel does not serve, measured before the rabbit says so

Three of 2026-11-24's faults were ENOSYS (`nanosleep`, `clock_gettime`,
`select` without `COMPAT_32BIT_TIME`; `flock` without `FILE_LOCKING`),
each found one boot at a time. `syscalls-audit.py` (new) reads
`sys_call_table` out of vmlinux and the `svc` numbers out of every ARM
ELF of a root tree, and names the holes. On Terrier OS's root
(`sysroot-core`) against the config as it was:

    kernel: 452 entries in sys_call_table, 127 answered with ENOSYS
    inotify_init/add_watch/rm_watch/init1: via libc: dbus-daemon libgio-2.0 libmount
    shm*/sem*/msg*: busybox (ipcs), gpg, libortp;  madvise: libcrypto, madplay
    mq_*, acct, init_module, quotactl, bdflush, _sysctl: libuClibc only

`CONFIG_INOTIFY_USER=y` added (dbus-daemon and glib; 2.6.32 had it). SysV
IPC left out: busybox's `ipcs`, gpg's shared-memory coprocess and libortp
are not on the rabbit's paths; `madvise` failing is harmless to both
callers. The audit runs in the RUNBOOK's recipe after every config change.

### 2.2 Config: what tinyconfig left and the review changes

| option | was | now | why |
|---|---|---|---|
| `HZ` | 100 | 200 | 2.6.32's value; every userland timer (§1.2). Needs Gengar. |
| `PANIC_TIMEOUT` | 0 | 10 | the BIOS adds nothing to the line; a panic must restart the rabbit (`qemu-mini2440/panic.sh`). |
| `INOTIFY_USER` | n | y | §2.1 |
| `SWAP` | y | n | no swap device; 5.6 KB of zImage |
| `LEGACY_PTYS` | y | n | BSD ptys: surface, no user (dropbear uses Unix98) |
| `USB_OHCI_HCD_S3C2410` | default | named | the glue `os-prelude` rebinds; a default must not drop it |

zImage: 2,508,304 bytes before, 2,502,688 after (`size.sh`, both under
3,145,728). `size.sh` now also checks the `is not set` lines it asks for
and copies vmlinux and System.map out for the audit.

Left as findings (each is a behaviour change only Gengar can judge):
`HIGH_RES_TIMERS` (the samsung_pwm clockevent can do one-shot; would give
led-daemon its 30 ms exactly; not in 2.6.32 either, so not the known-good
first), `STACKPROTECTOR` (ARM920T can; ~2 % of text; the attack surface is
the LAN API in shell, not the kernel), `PREEMPT` (none, as 2.6.32).

### 2.3 `mkcpio.py` read device nodes as files

`karotz/kexec/mkcpio.py` opened every entry of the tree with `open().read()`:
a FIFO or `/dev/zero` in an unpacked ramdisk (`--all` takes `/dev` too)
hangs it for good, a tty blocks. Fixed: character and block devices become
device entries (major/minor), FIFOs and sockets keep their type.
`test-mkcpio.sh` builds a tree with a FIFO (and a node when run as root):
with the fix "PASS" in a second, without it the 20 s timeout (control run
2026-11-24, exit 124).

### 2.4 `rabbit_ears` took any speed

`rabbitv3-chardevs.c:369-376` passed `abs(move.pwm) << 6` to
`pwm_config()`; above 255 the duty exceeds the 16,320 ns period,
`pwm_config` refuses (-EINVAL) and the ear keeps its previous speed with
the direction line already flipped; `abs(INT_MIN)` is undefined. Clamped
to `PWM_SPEED_MAX` (255). Compiles; the motors need Gengar (ears-daemon
sends 1 to 255, so nothing changes for it).

### 2.5 The encoders' interrupt trigger is inherited

`rabbitv3-chardevs.c:449` requests EINT1/EINT2 with no trigger type, "as
2.6.32 did" (`rabbit_ears.c:198-202`): the EXTINT register keeps what the
previous software set. Under kexec that is 2.6.32's; **from the BIOS it is
the BIOS's**, which nobody has read. Measure on the first flash boot
(`devmem 0x56000088` EXTINT0, bits 4-11 for EINT1/2) and then name the
trigger in the driver. Not changed: a wrong guess would stop the ears.

### 2.6 Things in the scripts

- `os-prelude:26-49`: the rebind loop runs five tries of 8 s whatever
  happens, and `continue` after a rebind skips the `authorized` toggle on
  that try: fine as a measure, not as the boot's fix (§1.1).
- `make-key.sh:32`: `mktemp -d` without a trap; a failed `cp -a` leaves
  the copy of the ramdisk in `/tmp`. Cosmetic.
- `autorun:19,73,85,104`: the log directory's number is `ls | wc -l`,
  which counts files too (`N-ramoops.bin` beside `N-.../`): two runs can
  share a number, nothing is lost (the directory names differ).
- qemu-mini2440/boot.sh:17,26 still looked for `/dev/spidev0.1`: the LED
  moved to chip select 0 in b260135 and the proof failed (QEMU has no mdev
  alias). Fixed to `spidev0.0`; PASS again 2026-11-24.
- `Containerfile:7-9` pins `alpine:3.24` but not the toolchain: GCC 15.1.0
  built the kernel on 2026-11-22, 16.1.0 today. Pin `gcc-arm-none-eabi`'s
  package version for the flash kernel and record the zImage's sha256.
- `hook.sh` appends to `mach-types` and the Makefile without checking the
  tree's version: fine while `LINUX` is pinned in the Containerfile.
- `nandsim-noecc.patch`, `yaffs1-small-page.patch`, `spi-s3c24xx-devres.patch`,
  `s3c2410-nand-inithw.patch`, `s3c24xx-dma-tx-status.patch`: read; each
  does what its header says and nothing else. The devres one leaks nothing
  (`cleanup` frees). The DMA one guards `s3cchan->at` and `phy` together,
  which is right (`phy` is NULL once the channel is released).

## 3. Security

The kernel itself is a small surface on a LAN appliance; the real one is
LapinLibre's web API (karotz/lapinlibre/REVIEW-LAPINLIBRE.md). Here:

- `CONFIG_DEVMEM` and `KALLSYMS` are in the kexec config only
  (`karotz-6.1-kexec.config:6-10`); the flash config has neither. Good.
  `MODULES` is off: nothing can be loaded. `DEBUG_FS`, `PROC_KCORE`,
  `MAGIC_SYSRQ` off.
- `USB_G_SERIAL=y` (as 2.6.32): a gadget serial on the device port.
  Nothing in Terrier OS's inittab runs a getty on `ttyGS0`
  (`sysroot-core/etc/inittab`: sshd.sh only), so it is a bare port. Could
  go for 30 KB; left, it is 2.6.32's behaviour.
- `usb-bus-power-rabbitv3.patch` also lets a hungry device on the external
  port draw more than the hub's budget. The board powered them under 2.6.32
  the same way (same rule, hub self-powered), so no change in practice.
- `/dev/mem` writes in `autorun` and `measure-init` (the KXLOG hand-over)
  run as root on the kexec key only; the flash kernel has no `/dev/mem`.
- The kexec `autorun` runs whatever `append.txt` on the key says
  (`autorun:111`): the key is the trust boundary already (it is the
  installer).

## 4. Size and boot time

- 2,502,688 bytes with the review's config: 628 KiB left in mtd1.
- The two biggest things still in: `MEDIA_SUPPORT`/`VIDEO_S3C_CAMIF`/
  `VIDEO_OV7670` (the camera, step 7, not yet used by any driver here) and
  `USB_GADGET`+`G_SERIAL`. Not removed: the camera is on the roadmap and
  the gadget is 2.6.32's.
- `CONFIG_CC_OPTIMIZE_FOR_SIZE` is already on; `-Os` on the firmware side
  failed on hardware once (memory), but this is the kernel, where it has
  run on Gengar since 2026-11-23.

## 5. Maintainability

- `karotz-6.1.config` is a merge fragment on tinyconfig: every option it
  does not name takes tinyconfig's default, which is how `HZ=100`, `SWAP`
  and `LEGACY_PTYS` came in. `size.sh`'s "asked but not kept" check now
  covers `is not set` lines, and the audit covers the syscalls; a saved
  full `.config` beside the fragment would make the next drift visible
  in a diff (out/.config after `size.sh`).
- The board file's `RV3_STEP`/`RV3_REGS` lines (`mach-rabbitv3.c:373,443`)
  were the 2026-11-23 bisect's; they cost nothing and stay useful in
  ramoops. Keep.
- `rabbitv3-chardevs.c` documents the 2.6.32 interface byte for byte at
  its head; the one divergence (threaded encoder IRQs, `pwm_config` may
  sleep) is stated. Good.

## 6. What was implemented here, and its proof

| change | proof |
|---|---|
| `syscalls-audit.py` | run on `sysroot-core` against the built vmlinux: the holes above; after the config change, no inotify left (RUNBOOK) |
| `size.sh`: `is not set` checked, vmlinux/System.map out | the second build's empty "asked but not kept" |
| `karotz-6.1.config`: `INOTIFY_USER`, `PANIC_TIMEOUT=10`, `HZ_200`, `SWAP`/`LEGACY_PTYS` off, OHCI glue named | `size.sh` 2,502,688 B; `boot.sh` MACH=1912 PASS; `panic.sh` "Rebooting in 10 seconds" |
| `mkcpio.py` device nodes | `test-mkcpio.sh` PASS, control hangs |
| `rabbitv3-chardevs.c` speed clamp | compiles in the same build; motors need Gengar |
| `qemu-mini2440/panic.sh` | new proof script |
| `qemu-mini2440/boot.sh` spidev0.0 | PASS 2026-11-24 (failed on 0.1 since b260135) |

Needs Gengar: `HZ=200`'s effect on the belly (§1.2), the hub's status
under both kernels and UPLLCON (§1.1), the encoder trigger from the BIOS
(§2.5), the ears with the clamp (§2.4).
