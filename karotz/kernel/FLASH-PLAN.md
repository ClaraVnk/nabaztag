# Writing Linux 6.1 into a Karotz's flash: the way back first

Study of 2026-11-24, before any 6.1 kernel is written to a Karotz. Nothing
here was run on hardware and nothing was built: the bootloader was read in
Gengar's own copy of it, the rest in LapinLibre's, OpenKarotz's and this
repository's code. Each statement names its source; what could not be
established is listed as such.

Why this comes first: on 2026-11-21 a live install of Terrier OS left Gengar
without Wi-Fi (red nose, then cyan), and the way back was a USB-key reinstall.
That way back runs on the kernel in the flash. A 6.1 kernel that does not
boot takes it away, so the question is what remains then.

## 1. What the bootloader does

Source: measure/mtd0.bin from Gengar's measure key (2026-11-23,
`karotz/measure-key`), 256 KiB, the "bootloader" partition. Strings and a
disassembly (`arm-none-eabi-objdump -D -b binary -marm
--adjust-vma=0x33f80000`); the base 0x33f80000 is where its literal pool
points, and where QEMU's mini2440 copies the first 256 KiB of a NAND and
jumps when it boots from NAND (hw/mini2440.c:271-279 of the fork pinned in
`qemu-mini2440/Containerfile`). The banner reads "Rabbit V3 - (BIOS v1.16)
PRAGMATEC/Point Core" (offset 0xb3f8). oneearedrabbit's root guide
(github.com/oneearedrabbit/karotz, "Hardware root") has a serial capture of
the same BIOS on another Karotz: same banner, menu and boot parameters,
which checks the reading below.

### At power-on

1. Clocks: UPLLCON written first, (56, 2, 2) = 0x00038022, 48 MHz for the
   USB (0x33f845b8, the call into the writer at 0x33f80df8; the same value
   2.6.32 writes at its map_io), then CLKDIVN (13, 12) and MPLLCON (127,
   2, 1) = 405 MHz (0x33f845c8-0x33f845dc; read 2026-11-24 on the image
   with its spare stripped, 2048 of every 2112 bytes). The power-wheel
   loop writes the UPLL again (0x33f84b58) before it slows the clocks.
   NAND identified ("Read chip id"), boot parameters
   read from the last page of its own partition (the `bootpara` block at
   mtd0 offset 0x3f800: `AUTORUN`, `machine` 1912, `runAddr` 0x30200000,
   `initrdA` 0x31800000, `initrdL` 0x500000, `memsize`, `devfs`, `ostore`,
   `userpara`; the values are in oneearedrabbit's capture).
2. "Press ENTER to stop the autoboot" (0x33f84948): a key on the serial port
   opens the menu. Without one, the head button is polled 40 times with a
   dot printed each time it reads released (main loop 0x33f84950-0x33f849a0;
   the 40 dots are in the capture).
3. "Load Kernel..." (0x33f83cd8): partition 1 (mtd1, 3 MiB) is copied page
   by page to RAM, bad blocks skipped ("Skipped bad block at 0x%x"). **No
   checksum, no size field, no fallback**: whatever is in mtd1 is run.
4. "Load Ramdisk..." (0x33f83db4): partition 2 (mtd2, 5 MiB) to 0x31800000.
5. The command line is formatted from the parameters (0x33f83c98, format at
   0xb1a4: `%s root=%s init=/linuxrc quiet mem=%dK %s %s k_nbclick=%d`,
   giving Gengar's `initrd=0x31800000,0x00500000 root=/dev/ram rw
   init=/linuxrc quiet mem=65536K devfs=udev  k_nbclick=0`), ATAGs are
   written at 0x30000100, and the kernel is started at `runAddr` with
   machine 1912 (0x33f82530). `k_nbclick` counts the power wheel (GPC2)
   going off and on while the BIOS runs (0x33f84b50-0x33f84ba8), nothing to
   do with the head.

The BIOS has a USB *device* stack (descriptor strings `[GDD]`, `[SETUPEND]`,
`VendID`, `ProdID`) and **no USB host, no FAT, no file names**: it never
reads a USB key. The key's `autorun` is run by `linuxrc` in the ramdisk,
after `/usr` is mounted (LapinLibre `image/core/rootfs/linuxrc`, the built
copy on the dev VM `sysroot-core/linuxrc:84-86`; the factory one checks
`autorun.sig` with gpg first, `rofra/karotz-openkarotz-firmware`
`firmware/linuxrc:123-125`). **A kernel in mtd1 that does not boot removes
the USB-key path.**

### The rescue partitions and what uses them

Gengar's flash (its dmesg, 2026-11-23):

| mtd | name | offset | size | Gengar's content |
|---|---|---|---|---|
| 0 | bootloader | 0x000000 | 256 KiB | BIOS v1.16, parameters in its last page |
| 1 | Kernel | 0x040000 | 3 MiB | Terrier OS zImage, 2,419,708 bytes |
| 2 | ramdisk | 0x340000 | 5 MiB | `rootfs.img` gzip of 2026-11-22 |
| 3 | kernel rescue | 0x840000 | 3 MiB | a zImage of 2,314,316 bytes |
| 4 | ramdisk rescue | 0xc40000 | 5 MiB | `rootfs.img` gzip dated 2011-03-12 |
| 5 | yaffs rescue | 0x1240000 | 3 MiB | 2.1 MB of data, no YAFFS tags in any spare |
| 6 | yaffs | 0x1540000 | 234 MiB | `/usr`, YAFFS2 tags, 2 KiB pages |
| 7 | mystery box | 0xff40000 | 768 KiB | all 0xFF |

mtd3 and mtd4 are byte-identical in Gengar's two measures and dated from the
factory: Terrier OS's install writes mtd1, mtd2 and `/usr` only (the key's
`autorun`, step 4), OpenKarotz's key install writes mtd2 only
(`rofra/karotz-openkarotz-allinone-installer` `ressources/autorun:146-151`);
FROS's installer was not read. The factory rescue is still there on a
rabbit that went through OpenKarotz or Terrier OS, as far as those installs
go.

The BIOS menu's function 5, **"Restore default partitions"** (0x33f82dcc):
"Now coping partition 3 to 1", "Now coping partition 4 to 2", "Now erasing
partition 6". It copies the rescue kernel and ramdisk over mtd1 and mtd2 and
**erases /usr**. It checks nothing in partition 3 first.

The same function runs without a serial port. In the main loop, after the
autoboot delay (0x33f84968-0x33f84ac4): while the head button (GPF0, low
when pressed: LapinLibre drivers/char/rabbit_button.c:83, our
`rabbitv3-chardevs.c:107`) reads *released*, a dot is printed and after 40
of them the kernel is loaded. While it reads *pressed*, a second counter
runs from 300 with the nose alternating between two colours (values
(255,255,255) and (0,255,0) at 0x33f84900 and 0x33f84988; whether that is
white and green is to be seen on the rabbit). When it reaches 0 the BIOS
waits for the button to be released, prints "Loading default partitions...",
sets the nose to (255,255,0), restores, sets it to (0,255,255) and boots.
**Head button held at power-on through the blinking, then released = the
factory system back, /usr erased.** This is the Karotz's factory reset the
measure README warns about ("a held head at power-on is the factory
reset"), now read in the code. It does not depend on mtd1 or mtd2.

"Locked" is not enforced. LapinLibre's documentation calls mtd0, mtd3 and
mtd4 locked (docs/system.md, the partition table). The board file masks
`MTD_POWERUP_LOCK` on them (LapinLibre
kernel/src/arch/arm/mach-s3c2440/mach-rabbitv3.c:157,173,179, ours
rabbitv3/mach-rabbitv3.c:127,143,149), which is 0x2000 "always locked
after reset", not `MTD_WRITEABLE` 0x400 (include/mtd/mtd-abi.h:41,44 of
2.6.32). Root can write `/dev/mtd0`, `/dev/mtd3` and `/dev/mtd4` on both
kernels. Nothing in this plan writes them; a write to mtd0 would leave only
JTAG.

### The serial menu, and what it can write

The serial port is a four-pin header on the board ("four pins at the right
top corner", oneearedrabbit's photo; GND, RX, TX; 115200 8N1; our photos of
2026-11-24 show four holes near C67/U7). ENTER during the autoboot gives:

    0 : Uart download file
    1 : Write Nand flash with download file
    2 : Load Pragram from Nand flash and run
    3 : Erase Nand flash regions
    4 : Test NAND flash
    5 : Restore default partitions
    6 : Set boot params

Function 0 (0x33f81310) receives one frame on the same port into RAM at
0x31000000: a 32-bit little-endian total size first (data length + 6),
then the data, then a 16-bit little-endian sum of the data bytes
("Checksum fail!" otherwise); then "Are you sure to run? [y/n]". It is
Samsung's DNW download format. Function 1 then writes what was received
into a chosen region ("Please select which region to write", the six
regions kernel, ramdisk, kernel rescue, ramdisk rescue, yaffs rescue,
yaffs; "Download file size is larger than selected partition size !"
refused), skipping bad blocks. **A 2.6.32 zImage can be put back into mtd1
over three wires, /usr untouched**, at 115200 baud (about four and a half
minutes for 3 MiB). Not done yet by anyone we know of; the frame format is
read from the code only.

### JTAG

J1 (2x4) is on the board; its pinout is not known. OpenOCD has the ARM920T
target and an `s3c2440` NAND driver (`src/flash/nand/s3c2440.c`,
tcl/board/mini2440.cfg), and our station bit-bangs JTAG from a Raspberry
Pi (`firmware-arm/jtag/`, linuxgpiod). Reports on the driver are mixed
(a U-Boot written in 853 s on one board; "very experimental", erase errors
and dropped links on others). It is the way back if the bootloader itself
is lost, and the last one to try: the pinout to find, the SDRAM init to
script, hours per attempt.

### What the main loop reads (for the emulator)

The head button is GPFDAT bit 0 (0x33f84968: `ands r5, r3, #1`; released =
1, the normal count of 40 dots; pressed = 0, the 300-count with the nose
blinking, then the restore). The power wheel is GPCDAT bit 2 (0x33f84914,
0x33f849b4: set = the wheel off, the BIOS slows the clocks and waits). A
GPIO model whose inputs read 0 holds the head pressed and the wheel on:
QEMU's does, which `direct-boot.py` corrects (GPF0 released unless
`MINI2440_HEAD_HELD=<ms>`).

## 2. Verdict

**Yes, there is a way back today, and it does not need a key or the kernel
in the flash: the bootloader's own restore from the rescue partitions, head
button held at power-on.** It is read in Gengar's bootloader and matched by
a serial capture of another Karotz; it has not been exercised by us. Its
cost is `/usr`: the adoption key, the Wi-Fi, the settings, the Terrier
client, then a factory system that needs the Terrier OS key install again.
The non-destructive way back, the serial menu, needs three wires soldered
and a small tool for the download frame; it is the one to prepare if
Gengar is to keep its `/usr` through a bad 6.1. Nothing is written to mtd1
before one of the two has been done once on Gengar, as the witness.

## 3. What is not established

- The restore has never been run on Gengar: the nose colours, how long the
  300-count window lasts, and whether the 2011 rescue system boots and runs
  the key's `autorun` on this rabbit (its `linuxrc` is the factory one, so
  `autorun.sig` is needed beside the script: `karotz/measure-key` already
  ships it). **Run in QEMU on 2026-11-25** (`bios-boot.sh`, RUNBOOK): the
  BIOS, from Gengar's own mtd0, boots the NAND to "booting the kernel",
  and with the head held restores mtd3 over mtd1, mtd4 over mtd2 and
  erases mtd6, checked in the image. What the rabbit adds is the nose,
  the timing and the rescue system itself.
- 6.1 has only run on Gengar through kexec: an initramfs (`rdinit=/init`),
  our command line, the ramoops memory carved out. From the BIOS it will
  get a gzipped cramfs on `/dev/ram0`, `root=/dev/ram rw init=/linuxrc`,
  `initrd=` by address, `ATAG_INITRD`, and whatever `CONFIG_CMDLINE` adds.
  `real-boot.sh` proved that path in QEMU on 2026-11-23, with
  `ramdisk_size=8192` added to the line; the BIOS adds nothing, so the
  kernel's `CONFIG_BLK_DEV_RAM_SIZE=5120` must carry the 5 MiB alone
  (2.6.32's value too). **The BIOS path itself ran in QEMU on 2026-11-25**
  (`KERNEL=` in `bios-boot.sh`): see the RUNBOOK for how far; the first
  run showed that the BIOS's line names no console and 6.1 enables none,
  hence `console=ttySAC0` in `CONFIG_CMDLINE`.
  **Open (2026-11-25 00:40):** booted by the BIOS, 6.1 sees neither the
  BIOS's command line nor its `mem=`, nor the initrd ("Kernel command
  line:" is the built-in one alone, "Memory: .../16384K", then the panic
  on the root device), while kexec's `--atags` were taken. What the BIOS
  writes at 0x30000100 is to be read (a 2.4-style param_struct would need
  `CONFIG_DEPRECATED_PARAM_STRUCT`, which converts it; 2.6.32's config to
  check first). Step 1 is not done until the BIOS-booted 6.1 reaches
  linuxrc in QEMU.
- The USB hub's wrong reads under 6.1 are explained (REVIEW-6.1.md 1.1,
  2026-11-24: SLOB's cache-line sharing on an ARM920T) and the kernel
  built with SLUB; the proof is Gengar's, with `karotz/kexec/usb-measure`.
  The UPLL is not a question any more: the BIOS writes 56/2/2 at power-on.
- The kernel that ran is the debug/kexec one (no size limit). The flash
  kernel must fit 3,145,728 bytes; the last size measured is 2,492,976
  before the three drivers, the DMA driver and the pstore options.
- `/usr` on Gengar is YAFFS2 on 2 KiB pages (oobdump, 2026-11-23). The
  write proof "YAFFS1 written and read back on 6.1" (`yaffs.sh`) ran on a
  simulated 512-byte-page chip: it does not cover Gengar's `/usr`. Reading
  it under 6.1 is proven (QEMU `real-usr.sh`, and read-only on Gengar);
  writing it is not, and neither is 2.6.32 reading what 6.1 wrote.
- The BIOS has no boot counter: a 6.1 that panics (`panic=10`) reboots in a
  loop; one that hangs stays hung. Both end at the head button.

## 4. The install, step by step

Boot-time, from a key, nothing live; each step proven in QEMU
(`karotz/kernel/qemu-mini2440/`) before Gengar; each tool versioned here
with its RUNBOOK entry, in the same PR as its proof.

### Step 0: the way back, witnessed

Two tools, both without a flash write. The first is done (2026-11-25,
`qemu-mini2440/bios-boot.sh`: the BIOS boots Gengar's NAND, and restores
with the head held; the serial download is not done); the second is
Clara's.

- **The bootloader in QEMU.** `mini2440` boots from NAND when no `-kernel`
  is given (hw/mini2440.c:271-284: the first 256 KiB to 0x33f80000). With
  Gengar's NAND image from `karotz-flash.py`, the serial console must show
  the banner, the forty dots, "Load Kernel...", then Linux: the whole chain
  as the rabbit runs it, the BIOS included. QEMU's loader reads 512-byte
  pages there and may need the 2 KiB fix `direct-boot.py` gave the kernel
  path. Then, GPF0 held low (a patch of the same kind), the restore: after
  it, mtd1 equals mtd3, mtd2 equals mtd4, mtd6 is 0xFF. Then the serial
  download: a frame sent to QEMU's serial port, function 1 into region 1,
  mtd1 equal to the file.
- **On Gengar, Clara's call**, since it costs the reinstall: head held at
  power-on through the blinking, released; the factory system (solid cyan,
  no network) and the Terrier OS key after it. Before that, the key takes a
  copy of `/usr/etc/conf` (the adoption key and settings; the install
  already keeps that directory, this keeps it through an erased `/usr`).
  Or the serial tool first (three wires), and the restore stays the
  fallback of the fallback.

### Step 1: the flash kernel

`karotz-6.1.config` alone (the kexec and debug additions are EXTRA
fragments), the board file reserving 1 MiB at 0x32000000 for ramoops and
registering the device (no `mem=` games on top of the BIOS's
`mem=65536K`), `CONFIG_PANIC_TIMEOUT=10`, `CONFIG_CMDLINE` with
`usbcore.old_scheme_first=1 console=ttySAC0,115200`,
`CONFIG_CONSOLE_LOGLEVEL_QUIET=7` so the BIOS's `quiet` keeps the boot log
for ramoops, SLUB, `size.sh` under 3,145,728 bytes (2,530,336 on
2026-11-25). Proof in QEMU: `bios-boot.sh` with `KERNEL=`, the BIOS's own
chain and line on Gengar's NAND image (RUNBOOK, 2026-11-25); then on
Gengar through the key, **without the flash**: `kexec` with the key's
`rootfs.img.gz` as the initrd and `root=/dev/ram rw init=/linuxrc` on the
line (a non-cpio initrd goes to `/dev/ram0`), which is the BIOS's path
with kexec in the BIOS's place. The Terrier connection, ears, belly and
voice as on 2026-11-24, and `usb-measure`'s two files.

### Step 2: one ramdisk for both kernels

`rootfs.img.gz` changed so that it boots under 2.6.32 and 6.1 alike: mdev
rules for `uba*` and `sd[a-z]*`, `spidev0.0 -> spidev0.1`, `/usr` mounted
read-only with `/usr/etc` and `/usr/terrier` in RAM when the kernel is 6.x
(until step 5), read-write under 2.6.32 as today. Then the way back is a
kernel swap alone, the ramdisk stays. Proof: `kexec-proof.sh` (2.6.32 on
it) and `real-boot.sh` (6.1 on it) in QEMU; on Gengar `go-2632` and the
step-1 run, both on this ramdisk.

Design notes (2026-11-25, not built): the ramdisk is `rootfs.img.gz`
from `karotz/lapinlibre`'s build, its `linuxrc` the place for the three
changes (`uname -r` decides: `6.*` mounts `/usr` read-only and binds
`/usr/etc` and `/usr/terrier` from RAM, as `make-key.sh` does today for
the kexec key; the mdev rules and the `spidev0.1` alias are unconditional,
2.6.32 never sees `sd*` nor `spidev0.0`). The proof is `bios-boot.sh`
twice on the same image with the new mtd2 written (as `KERNEL=` writes
mtd1): the rabbit's 2.6.32 in mtd1, then the flash 6.1, both to
`[START]`; and `kexec-proof.sh` unchanged.

Done in QEMU (2026-11-25): the fork's branch `terrier-os-61-ramdisk`
(linuxrc: `uname -r` 6.* mounts `/usr` read-only and binds `/usr/etc` and
`/usr/terrier` from RAM; mdev: `sd*` and `spidev0.0 -> spidev0.1`), built,
its `rootfs.img.gz` written over mtd2 and the flash 6.1 over mtd1 by
`RAMDISK= KERNEL= bios-boot.sh`: Gengar's BIOS boots both, `/linuxrc` and
`init` run, `/usr` is read-only (sshd.sh's `chmod 700 /usr/karotz/.ssh`
says so, harmless: `authorized_keys` is a link into `/usr/etc/conf`). The
2.6.32 side does not show in QEMU (its console is the VT): on Gengar, a
test key built from `terrier-os-61-kexec` (the same ramdisk with the
kexec-capable 2.6.32) installed, run under 2.6.32, then `go-os` with the
flash 6.1 and the key's `rootfs.img.gz` as the initrd.

On Gengar (2026-11-25, nothing written to the flash): a test install
built from the fork's `terrier-os-61-kexec` (the one ramdisk with the
kexec-capable 2.6.32, version `0.3.1-kexec`) ran under 2.6.32 as before
(`/usr` read-write, the client back on the Terrier); then the key's
`kexec/go-flash` started the flash 6.1 kernel on that same installed
ramdisk with the BIOS's line: 64 MiB, the ramdisk loaded, `/usr` read-only
and `/usr/etc` in RAM, the Wi-Fi up and the client 0.1.20 "idle, ready",
ramoops at 0x32000000. Steps 1 and 2 done on the rabbit.

### Step 3: the write, boot-time, verified

A reduced `autorun` on the key, LapinLibre's install with steps 5 to 7 left
out: it writes mtd1 and mtd2 and **does not touch `/usr`** (the 2026-11-21
failure was `/usr` emptied under running daemons; a kernel install has no
reason to go there). Preflight as today (md5, `check_fits`), then
`flash_erase` and `nandwrite -pm`, then what LapinLibre's install lacks: a
read-back of both partitions (`nanddump -l <size>`) compared with the key's
files before the reboot, and once more from the running 6.1 (`/dev/kernel`).
A read-back that differs is written again; a second failure writes the key's
2.6.32 zImage and ramdisk back (both on the key) and says so (red nose, the
install log on the key kept). The key also carries `go-back`: the same
`autorun`, under either kernel, writes 2.6.32 back. Proof: `karotz/ota`'s
emulator on nandsim (its ten checks reduced to the kernel, the ramdisk, the
log, the read-back, and `/usr` untouched: settings, Wi-Fi, key, client all
still there without being "kept"), then QEMU booting the written image
through the BIOS (step 0's tool); on Gengar the key, the nose, the Terrier.

Design notes (2026-11-25, not built): the key's `autorun` for this step
is `karotz/kexec/autorun`'s structure with the install in place of the
kexec: (1) preflight, `md5sum -c` of `zImage-6.1`, `rootfs.img.gz`,
`zImage-2632` on the key and `check_fits` against `/proc/mtd`; (2) a copy
of `/usr/etc/conf` to the key (never needed by this step, kept anyway);
(3) `flash_erase /dev/mtd1 0 0; nandwrite -pm /dev/mtd1 zImage-6.1`, then
`nanddump -l <size> /dev/mtd1 | cmp - zImage-6.1`; the same for mtd2 only
when the key's `rootfs.img.gz` differs from the running one (step 2's
ramdisk is the same for both kernels, so usually not); (4) a mismatch is
written again once, a second mismatch writes `zImage-2632` back and sets
the nose red, the log on the key; (5) `sync`, `reboot`. `go-back` is the
same script with `zImage-2632` as the kernel to write. `/usr` is not
touched, not even remounted. Proof: `firmware-arm/emulator/karotz/ota`'s
nandsim emulator with its checks reduced to the kernel, the log, the
read-back and `/usr` untouched; then `bios-boot.sh` on the image the
emulator wrote (its mtd1 and mtd2 copied in by `karotz-flash.py`'s
layout), to `[START]`; then the first `go-back` in QEMU the same way.

### Step 4: the first week

6.1 in the flash with `/usr` read-only and its two directories in RAM:
settings changed under 6.1 are lost at reboot, to be said before. ramoops
read by the next boot's `autorun` (`ramoops.py`) on any restart; `go-back`
on the key at hand; the head button behind it. Only Gengar, nothing offered
to anyone.

### Step 5: YAFFS written by 6.1, then /usr read-write

The proof is a round trip, not a write: in QEMU on a copy of Gengar's NAND,
6.1 (`real-boot.sh`) writes files in `/usr`, 2.6.32 (`kexec-proof.sh`'s
kernel, same image) mounts and reads them and writes its own, 6.1 reads
those; checkpoints both ways (YAFFS2 writes one at unmount; a mismatch is a
slow mount, not a loss, but it is measured). On Gengar, the first real write
goes to **mtd7**, the 768 KiB "mystery box": all 0xFF in the measure, named
by nothing (the BIOS region table stops at "yaffs", LapinLibre does not
mount it). Under 6.1 by key: `mount -t yaffs /dev/mtdblock7`, a file
written, a reboot into 2.6.32, the file read; undone by `flash_erase
/dev/mtd7`, which gives back the measured state. mtd5 is not a sandbox: it
holds 2.1 MB of factory data. Only after that, `/usr` read-write under 6.1,
with `/usr/etc/conf` copied to the key first.

## 5. Sources

- Gengar's measure, 2026-11-23: measure/log.txt (`/proc/mtd`,
  `/proc/cmdline`), `dmesg.txt`, `mtd0.bin` to `mtd7.bin`, `mtd5-raw.bin`,
  `mtd6-raw.bin`, `oobdump-mtd5.txt`, `oobdump-mtd6.txt`; the earlier
  measure of the same rabbit for the comparison of mtd3 and mtd4.
- BIOS v1.16 disassembly offsets, base 0x33f80000: banner 0xb376; main loop
  0x4900-0x4ac4 and its pools 0x4bac-0x4c40; restore 0x2dcc; the Linux boot
  0x3c98-0x3dac; UART download 0x1310; menu table 0xca70; parameter block
  0x3f800.
- github.com/oneearedrabbit/karotz, README "Hardware root": the serial
  capture (banner, dots, menu, parameters).
- sweetlilmre/LapinLibre: docs/system.md (partition table, the boot),
  kernel/src/arch/arm/mach-s3c2440/mach-rabbitv3.c,
  drivers/char/rabbit_button.c, rabbit_poweron.c, the key's `autorun`
  and `install-lib.sh`, `image/core/rootfs/linuxrc`.
- rofra/karotz-openkarotz-firmware `firmware/linuxrc` (the factory boot).
- ywandy/qemu-mini2440 hw/mini2440.c (NAND boot).
- OpenOCD `src/flash/nand/s3c2440.c`, tcl/board/mini2440.cfg; user
  reports on the sparkfun forum and the openocd-development list.
- This repository: `docs/RUNBOOK.md` ("Linux 6.1 for the Karotz", "Terrier
  OS on Linux 6.1, from the key"), `karotz/kexec/`, `karotz/kernel/`,
  `firmware-arm/emulator/karotz/ota/`.
