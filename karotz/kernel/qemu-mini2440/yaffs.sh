#!/bin/sh
# Step 3 of docs/TERRIER-OS-KERNEL.md, in QEMU: the 6.1 kernel's YAFFS1 on a
# simulated 256 MiB NAND of 512-byte pages without ECC (nandsim, Samsung
# id ec 71, noecc=1 from ../nandsim-noecc.patch, as the Karotz runs it): it
# mounts the blank flash as the Karotz mounts /usr (-t yaffs), writes a
# file and a directory, unmounts, mounts again and reads them back. Run
# like boot.sh, with the same <dir> (zImage built with the debug config,
# rootfs.cramfs). PASS when the file comes back.
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
LOG="$DIR/yaffs.log"
podman run --rm -v "$DIR":/k:ro,Z qemu-mini2440 sh -c '
  dd if=/dev/zero of=/tmp/nand.bin bs=2112 count=65536 2>/dev/null
  (sleep 60
   echo; echo mount -t proc proc /proc; echo mount -t devtmpfs dev /dev
   echo cat /proc/mtd
   echo mount -t yaffs /dev/mtdblock0 /mnt
   echo mkdir /mnt/etc; echo echo carotte-6.1 \> /mnt/etc/karotz.txt; echo sync
   echo umount /mnt; echo mount -t yaffs /dev/mtdblock0 /mnt
   echo ls -la /mnt /mnt/etc; echo echo READ=\$\(cat /mnt/etc/karotz.txt\)
   echo dmesg \| grep -i -E \"yaffs\|nand\" \| tail -8
   sleep 25) |
  timeout 120 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append "console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh nandsim.id_bytes=0xec,0x71 nandsim.noecc=1" 2>&1 |
  tr -d "\r"' > "$LOG" 2>&1 || true
grep -E "^dev:|^mtd0|NAND device|nandsim|yaffs" "$LOG" | head -12 || true
if grep -qx "READ=carotte-6.1" "$LOG"; then echo "PASS: YAFFS1 written and read back on 6.1 ($LOG)"; else echo "FAIL ($LOG)"; exit 1; fi
