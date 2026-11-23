#!/bin/sh
# Does the 6.1 kernel on QEMU's NAND read back what the Karotz's flash holds?
# real-usr.sh mounted Gengar's /usr with half its top directories missing
# (2026-11-23); this tells QEMU's NAND and the kernel's driver from the dump.
# Under the 6.1 kernel as the Karotz (MACH=1912), with the chip the measure
# names, it reads mtd5 with its spare (oobdump, carried in mtd7's data) and
# 8 MiB slices of mtd6's data across the partition (dd skips by reading, so
# the far ones take minutes), and compares their MD5 with the measure's.
#
#   karotz/kernel/qemu-mini2440/nand-compare.sh <dir> <measure dir> <oobdump binary>
#
# <dir> holds zImage and rootfs.cramfs, as for real-usr.sh.
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
M=$(cd "${2:?the measure/ directory from the key}" && pwd)
TOOL=${3:?the oobdump binary (karotz/measure-key/Containerfile)}
HERE=$(cd "$(dirname "$0")" && pwd)
CID=$(python3 "$HERE/karotz-flash.py" "$M" "$DIR/karotz-nand.bin" | tail -1)
WANT=$(python3 - "$DIR/karotz-nand.bin" "$M" "$TOOL" "$CID" <<'PY'
import hashlib, sys
img, m, tool, cid = sys.argv[1], sys.argv[2], open(sys.argv[3], "rb").read(), int(sys.argv[4], 16)
page, oob = {0x71: (512, 16), 0xDA: (2048, 64)}[cid]
f = open(img, "r+b")
q = 0xff40000 // page                     # mtd7: the tool rides in its data
for i in range(0, len(tool), page):
    f.seek((q + i // page) * (page + oob)); f.write(tool[i:i + page].ljust(page, b"\0"))
raw5 = open(f"{m}/mtd5-raw.bin", "rb").read()
raw6 = open(f"{m}/mtd6-raw.bin", "rb").read()
n = 8 * 1024 * 1024 // page
def data(raw, first, count):
    return b"".join(raw[i * (page + oob):i * (page + oob) + page] for i in range(first, first + count))
slices = " ".join(f"{mib}:{hashlib.md5(data(raw6, mib * 1024 * 1024 // page, n)).hexdigest()}"
                  for mib in (0, 64, 112, 120, 128, 192))
f.flush()
# mtd7's first 40 KiB as the image now holds them: the tool, then what the
# dump had after it.
g = open(img, "rb")
m7 = b"".join((g.seek((q + i) * (page + oob)), g.read(page))[1] for i in range(40960 // page))
print(hashlib.md5(raw5).hexdigest(), hashlib.md5(m7).hexdigest(), page, n, slices)
PY
)
set -- $WANT
W5=$1 W7=$2 PAGE=$3 N=$4; shift 4; SLICES="$*"
LOG="$DIR/nand-compare.log"
podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=$CID -v "$DIR":/k:Z qemu-mini2440 sh -c "
  cp /k/karotz-nand.bin /tmp/n.bin
  (sleep 60; echo; echo mount -t proc p /proc; echo mount -t devtmpfs d /dev; echo mount -t tmpfs t /mnt
   echo 'dd if=/dev/mtd7 of=/mnt/oobdump bs=$PAGE count=$((40960 / PAGE)) 2>/dev/null; chmod 755 /mnt/oobdump'
   echo '/mnt/oobdump /dev/mtd5 /mnt/o5.bin | sed s/^/OD-/'
   echo 'md5sum /mnt/o5.bin | sed s/^/M5-/'
   echo 'dd if=/dev/mtd7 bs=$PAGE count=$((40960 / PAGE)) 2>/dev/null | md5sum | sed s/^/M7-/'
   for sl in $SLICES; do mib=\${sl%%:*}
     echo \"dd if=/dev/mtd6 bs=$PAGE skip=\$((mib * 1048576 / PAGE)) count=$N 2>/dev/null | md5sum | sed s/^/M6-\$mib-/\"; done
   sleep 1500) |
  timeout 1700 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic -mtdblock /tmp/n.bin \
    -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append 'console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh' 2>&1 |
  tr -d '\r'" > "$LOG" 2>&1 || true
rm -f "$DIR/karotz-nand.bin"
grep -a "^OD-\|^M5-\|^M6-" "$LOG" || true
ok=1
grep -aq "^M7-$W7" "$LOG" && echo "mtd7 first 40 KiB: same" || { echo "mtd7 first 40 KiB: DIFFERENT"; ok=0; }
grep -aq "^M5-$W5" "$LOG" && echo "mtd5 with its spare: same" || { echo "mtd5 with its spare: DIFFERENT"; ok=0; }
for sl in $SLICES; do mib=${sl%%:*} h=${sl#*:}
  grep -aq "^M6-$mib-$h" "$LOG" && echo "mtd6 8 MiB at $mib MiB: same" || { echo "mtd6 8 MiB at $mib MiB: DIFFERENT"; ok=0; }
done
[ $ok = 1 ] && echo "PASS ($LOG)" || { echo "FAIL ($LOG)"; exit 1; }
