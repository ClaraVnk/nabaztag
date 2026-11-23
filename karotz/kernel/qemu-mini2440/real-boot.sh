#!/bin/sh
# Step 4 on a real Karotz: its own system on the 6.1 kernel. As real-usr.sh,
# with the Karotz's own ramdisk (mtd2: a gzipped cramfs, its initrd) in place
# of ours and the command line its bootloader gives (Gengar's /proc/cmdline,
# measure of 2026-11-23), so its /linuxrc runs, mounts /usr from the NAND
# and starts what it starts. The console is kept whole in <dir>/real-boot.log:
#
#   karotz/kernel/qemu-mini2440/real-boot.sh <dir> <measure dir> [seconds]
#
# <dir> holds the 6.1 zImage. Prints the lines that say how far it got.
set -eu
DIR=$(cd "${1:?a directory with zImage}" && pwd)
M=$(cd "${2:?the measure/ directory from the key}" && pwd)
SECS=${3:-300}
HERE=$(cd "$(dirname "$0")" && pwd)
CID=$(python3 "$HERE/karotz-flash.py" "$M" "$DIR/karotz-nand.bin" | tail -1)
# mtd2 as nanddump -o wrote it: each 2 KiB page followed by its 64-byte
# spare, which is not part of the gzip (karotz-flash.py, 2026-11-23).
python3 -c '
import sys
raw = open(sys.argv[1], "rb").read()
step = 2112 if len(raw) % 2112 == 0 else len(raw)
open(sys.argv[2], "wb").write(b"".join(raw[i:i + 2048] for i in range(0, len(raw), step)))
' "$M/mtd2.bin" "$DIR/karotz-ramdisk.gz"
LOG="$DIR/real-boot.log"
podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=$CID -v "$DIR":/k:Z qemu-mini2440 sh -c "
  cp /k/karotz-nand.bin /tmp/nand.bin
  timeout $SECS ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/karotz-ramdisk.gz \
    -append 'console=ttySAC0,115200 root=/dev/ram rw init=/linuxrc ramdisk_size=8192 mem=65536K k_nbclick=0' 2>&1 |
  tr -d '\r'" > "$LOG" 2>&1 || true
rm -f "$DIR/karotz-nand.bin" "$DIR/karotz-ramdisk.gz"
grep -aE "VFS: Mounted|Kernel panic|Run /linuxrc|yaffs: |mount|not found|No such|Starting|starting|[Ee]rror|failed|Oops" "$LOG" | head -60
