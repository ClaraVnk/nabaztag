#!/bin/sh
# Step 3 on a real Karotz's /usr: the 6.1 kernel, as the Karotz (MACH=1912,
# its board file and partitions), on QEMU's 256 MiB small-page NAND (id 71)
# holding the flash the measure key copied (karotz-flash.py), mounts the
# YAFFS1 /usr partition (mtd6) read-only and reads it. Run like boot.sh:
#
#   karotz/kernel/qemu-mini2440/real-usr.sh <dir> <measure dir>
#
# PASS when /usr's sys_version reads back. The flash file is made in <dir>.
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
M=$(cd "${2:?the measure/ directory from the key}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
python3 "$HERE/karotz-flash.py" "$M" "$DIR/karotz-nand.bin"
LOG="$DIR/real-usr.log"
podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=0x71 -v "$DIR":/k:Z qemu-mini2440 sh -c '
  cp /k/karotz-nand.bin /tmp/nand.bin
  (sleep 60
   echo; echo mount -t proc proc /proc; echo mount -t devtmpfs dev /dev
   echo "cat /proc/mtd | sed s/^/MTD-/"
   echo "mount -t yaffs -o ro /dev/mtdblock6 /mnt && echo MOUNT-OK"
   echo "ls /mnt | head -20 | sed s/^/LS-/"
   echo "echo VERSION=\$(cat /mnt/etc/conf/sys_version 2>/dev/null)"
   echo "du -s /mnt | sed s/^/DU-/"
   echo "dmesg | grep -i -E \"yaffs|nand\" | tail -8 | sed s/^/DMESG-/"
   sleep 40) |
  timeout 180 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append "console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh" 2>&1 |
  tr -d "\r"' > "$LOG" 2>&1 || true
grep -aE "^MTD-|^MOUNT-OK|^LS-|^VERSION=|^DU-|^DMESG-" "$LOG" || true
if grep -aq "^VERSION=." "$LOG"; then echo "PASS: the Karotz's own /usr read on 6.1 ($LOG)"; else echo "FAIL ($LOG)"; exit 1; fi
