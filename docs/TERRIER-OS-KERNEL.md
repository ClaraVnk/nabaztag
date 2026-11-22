# Terrier OS on Linux 6.1: the roadmap

Terrier OS runs LapinLibre's Linux 2.6.32.71 (2009, out of support since
2016). It moves to **Linux 6.1 LTS**, whatever LapinLibre itself does: if
upstream takes the work, Terrier OS stays a light fork; if not, it is a
distribution derived from LapinLibre (MIT credit kept, kernel sources
published under the GPL, as today). Decided by Clara on 2026-11-22.

## Why 6.1 and nothing later

The Karotz's processor is a Samsung **S3C2440A** (ARM920T, ARMv4T, no FPU),
64 MB of SDRAM, 256 MB of NAND. ARMv4T itself is still supported; what went
is the whole S3C24xx platform: deprecated, then removed by Arnd Bergmann's
21-patch series of October 2022 ("ARM: s3c: remove all s3c24xx support",
linux-arm-kernel), merged for 6.3. 6.1 is the last LTS that still carries
it. Its support window on kernel.org, and CIP's longer one, are to be
checked before the work starts.

## What changes, part by part

From LapinLibre's system documentation (its hardware table) and its
kernel configuration, rabbitv3_defconfig, in ClaraVnk/LapinLibre.

| Part | 2.6.32 today | In 6.1 | Work |
|---|---|---|---|
| S3C2440A, board `rabbitv3` (machine 1912) | out-of-tree board file `mach-rabbitv3.c` | platform present (deprecated); the locked bootloader boots with ATAGs, no device tree | port the board file |
| Wi-Fi, RT73 on USB | `rt73usb` with our WPA3 patches | mainline `rt73usb`, modern mac80211 | WPA3 (SAE) likely from wpa_supplicant and mac80211: our patches may go, to prove |
| NAND, no ECC | `s3c2410` NAND | driver present | check the no-ECC mode |
| `/usr` on **YAFFS1** | YAFFS added to the tree | out of tree; Aleph One's current YAFFS2 still reads YAFFS1 | integrate; an existing rabbit's `/usr` must survive |
| Sound, UDA1341 | ASoC `s3c24xx_uda134x`, OSS emulation | present up to 6.1, OSS emulation kept | configuration |
| Camera, OV7675 | own `s3c2440camif` | mainline `s3c-camif` (S3C244x) and `ov7670` (OV7675 to check) | move to mainline, adapt the client |
| Ears, head button, power switch | 3 own drivers (`rabbit_ears`, `rabbit_button`, `rabbit_poweron`) | none | port them; the button can be `gpio-keys` |
| Nose LED (SPI), RFID (I2C), serial, RTC, USB gadget | `spidev`, `i2c-dev`, `s3c2410` serial, `rtc-s3c`, `g_serial` | present | configuration; serial ports become `ttySAC*` |
| USB stick | `ub` (`/dev/uba1`) | `ub` long gone: `usb-storage` (`/dev/sda1`) | `usbkey.sh`, the install |
| Size | kernel partition `mtd1`: **3 MiB** | 6.1 with mac80211/cfg80211 is tight | measure first (xz zImage) |
| Userland | uClibc-ng with 2.6.32 headers | upward-compatible ABI | toolchain rebuilt with 6.1 headers |

## The three risks, lifted first

1. **Size**: a useful 6.1 zImage fits 3 MiB.
2. **YAFFS1**: the new kernel reads an existing rabbit's `/usr` (settings,
   adoption key) without losing it, and writes it so that the old kernel
   (the rescue path) still reads it.
3. **Boot**: the locked bootloader starts a 6.1 kernel with machine number
   1912 and its ATAGs, without a device tree.

None of the work below starts before each has its proof.

## Steps

Every step has its proof tool in this repository and its RUNBOOK entry, in
the same PR. Emulator first; a real Karotz (Gengar) only confirms.

1. **Size** — a 6.1 kernel for the S3C2440 from the rabbit's configuration
   translated to 6.1, built on the dev VM (Alpine container, versioned
   recipe in this repository): its zImage size, then what must go to fit.
2. **Boot in QEMU** — the same kernel in an emulator of an S3C2440 board
   (or the closest QEMU machine), ATAGs with machine 1912, LapinLibre's
   root file system: it reaches its shell.
3. **YAFFS1** — Aleph One's YAFFS2 in 6.1, on a nandsim copy of a real
   `/usr` image (Clara's own dump): mount, read the settings and the key,
   write, then read back with the 2.6.32 kernel.
4. **Board file** — `mach-rabbitv3.c` ported: NAND partitions, SPI, I2C,
   I2S, camera interface, PWM, GPIOs.
5. **Own drivers** — `rabbit_ears`, `rabbit_button`, `rabbit_poweron` ported
   with the same device nodes and ioctls, so the daemons need no change.
6. **Wi-Fi** — `rt73usb` with mac80211 and wpa_supplicant: WPA2, then WPA3
   (SAE) against hostapd in the emulator, as `firmware-arm` proved it for
   the Nabaztag.
7. **The rest** — sound, camera, USB stick (`usb-storage`), serial names.
8. **Terrier OS build** — the fork builds 6.1 instead of 2.6.32; the image
   tests and `test-hotspot.sh` pass; `karotz/ota-live` updates a running
   2.6.32 rabbit to the 6.1 build and back to a 2.6.32 one.
9. **Gengar** — the update from its page, then a week of normal life before
   any other Karotz is offered it.
10. **Upstream** — the work proposed to LapinLibre (sweetlilmre), as the
    WPA3 one was (LapinLibre#1).

## Status

- 2026-11-22: inventory done (this page).
- 2026-11-22, step 1 done: Linux 6.1.192 with every part of the table
  (S3C2440 on mini2440's board file, NAND, rt73usb with mac80211, the
  UDA1341 with OSS, the camera interface, SPI, I2C, PWM, GPIO keys, USB
  gadget and storage, cramfs) builds to a **2,456,408-byte xz zImage: it
  fits mtd1 with 673 KiB to spare**, before YAFFS and the three own
  drivers. `karotz/kernel/size.sh` says so (RUNBOOK). 6.1 hides S3C24xx
  behind `UNUSED_BOARD_FILES` and an ARMv4T/v5-only kernel; tinyconfig also
  turns off the MMU, power management and the block and network layers.
  Next: step 2, the boot in QEMU.
