#!/bin/sh
# The measure key's oobdump, proven before it reads a Karotz: under the 6.1
# kernel as the Karotz (MACH=1912) on QEMU's model of its NAND, it must give
# back the spare that is in the flash. Two known spares are written on the
# first pages of mtd5 (byte 5, the bad-block marker, left FF), the tool is
# carried in mtd7's data, and its copy of mtd5 is read back. Run like
# real-usr.sh, after it (it reuses <dir>/karotz-nand.bin):
#
#   karotz/kernel/qemu-mini2440/oob-proof.sh <dir> <oobdump binary>
#
# 2026-11-22: "first spare at page 0: 00 01 02 03 04 ff 06 ...", PASS.
set -eu
DIR=$(cd "${1:?a directory with zImage, rootfs.cramfs, karotz-nand.bin}" && pwd)
TOOL=${2:?the oobdump binary (karotz/measure-key/Containerfile)}
python3 - "$DIR" "$TOOL" <<'PY'
import shutil, sys
d, tool = sys.argv[1], open(sys.argv[2], "rb").read()
shutil.copy(f"{d}/karotz-nand.bin", f"{d}/oob-proof.bin")
f = open(f"{d}/oob-proof.bin", "r+b")
p = 0x1240000 // 512
f.seek(p * 528 + 512); f.write(bytes([0, 1, 2, 3, 4, 0xff, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]))
f.seek((p + 1) * 528 + 512); f.write(b"KAROTZ\xffOOB-TEST!")
q = 0xff40000 // 512
for i in range(0, len(tool), 512):
    f.seek((q + i // 512) * 528); f.write(tool[i:i + 512].ljust(512, b"\0"))
PY
LOG="$DIR/oob-proof.log"
podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=0x71 -v "$DIR":/k:Z qemu-mini2440 sh -c "
  cp /k/oob-proof.bin /tmp/n.bin
  (sleep 60; echo; echo mount -t proc p /proc; echo mount -t devtmpfs d /dev; echo mount -t tmpfs t /mnt
   echo 'dd if=/dev/mtd7 of=/mnt/oobdump bs=512 count=80 2>/dev/null; chmod 755 /mnt/oobdump'
   echo \"/mnt/oobdump /dev/mtd5 /mnt/o.bin | sed s/^/OD-/\"
   echo \"dd if=/mnt/o.bin bs=528 count=2 2>/dev/null | hexdump -C | grep -E '^00000(200|410)' | sed s/^/HX-/\"
   sleep 60) |
  timeout 160 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic -mtdblock /tmp/n.bin \
    -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append 'console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh' 2>&1 |
  tr -d '\r'" > "$LOG" 2>&1 || true
grep -a "^OD-\|^HX-" "$LOG" || true
if grep -aq "^HX-00000200  00 01 02 03 04 ff 06 07" "$LOG" && grep -aq "KAROTZ.OOB-TEST" "$LOG"; then
	echo "PASS: oobdump gives back the spare in the flash ($LOG)"
else
	echo "FAIL ($LOG)"; exit 1
fi
