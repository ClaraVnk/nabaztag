#!/bin/sh
# What a whole-Karotz emulator needs from a real Karotz, collected on the
# rabbit itself (FROS or OpenKarotz, over SSH as karotz/root), onto a USB key:
#
#   sh measure.sh /mnt/usbkey/karotz-measure
#
# Read-only on the rabbit: nanddump reads the NAND, nothing is written to it.
# What it takes: the SoC and board (cpuinfo, dmesg, iomem, the kernel's command
# line), the flash layout (/proc/mtd) and a dump of every MTD partition --
# the kernel image is in one of them, and qemu needs it to boot the rabbit's
# own Linux 2.6.28 on an S3C24xx board model (the console is ttySAC0) -- the
# device nodes the Karotz programs open, the loaded modules, and the D-Bus
# traffic of ten seconds of turning an ear by hand (the backlog's
# ears-by-hand measurement).
set -u
OUT=${1:-/mnt/usbkey/karotz-measure}
mkdir -p "$OUT" || exit 1
say() { echo "== $*"; }

say "system"
for f in /proc/cpuinfo /proc/version /proc/cmdline /proc/mtd /proc/iomem \
         /proc/interrupts /proc/devices /proc/modules /proc/meminfo /proc/partitions; do
  cat "$f" > "$OUT/$(echo "$f" | tr / _)" 2>&1
done
dmesg > "$OUT/dmesg.txt" 2>&1
uname -a > "$OUT/uname.txt" 2>&1
ls -l /dev > "$OUT/dev.txt" 2>&1
ls -lR /sys/class > "$OUT/sys-class.txt" 2>&1
cat /karotz/etc/rootfs_version /usr/etc/conf/*version* > "$OUT/versions.txt" 2>&1
ps > "$OUT/ps.txt" 2>&1

say "flash"
for dev in /dev/mtd[0-9]*; do
  case "$dev" in *ro|*block*) continue ;; esac
  n=${dev#/dev/mtd}
  if command -v nanddump >/dev/null 2>&1; then
    nanddump -f "$OUT/mtd$n.bin" "$dev" > "$OUT/mtd$n.log" 2>&1
  else
    dd if="$dev" of="$OUT/mtd$n.bin" bs=2048 > "$OUT/mtd$n.log" 2>&1
  fi
done

say "ears by hand: turn one ear now, slowly, for ten seconds"
if command -v dbus-monitor >/dev/null 2>&1; then
  dbus-monitor --system > "$OUT/dbus-ears.txt" 2>&1 &
  pid=$!
  sleep 10
  kill "$pid" 2>/dev/null
fi

sync
say "done: $OUT"
ls -l "$OUT"
