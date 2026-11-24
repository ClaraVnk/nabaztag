#!/bin/sh
# FLASH-PLAN steps 0 and 1 in QEMU (2026-11-24): the Karotz's own BIOS, read
# out of its NAND (mtd0 of the measure), boots the NAND as the rabbit does:
# the banner, the head button polled, "Load Kernel..." (mtd1 to RAM),
# "Load Ramdisk..." (mtd2), its own command line, Linux. No -kernel: QEMU's
# mini2440 copies the first 256 KiB of the NAND to 0x33f80000 and jumps
# (direct-boot.py reads the 2 KiB pages of Gengar's chip there).
#
#   karotz/kernel/qemu-mini2440/bios-boot.sh <dir> <measure dir> [seconds]
#
# As it is, the NAND is the rabbit's: its 2.6.32 kernel and ramdisk boot
# to Terrier OS's linuxrc ("[START]"), the witness. Two variables change
# the run:
#
#   KERNEL=<zImage>  step 1: that kernel written over mtd1 in the image (the
#                    6.1 flash kernel, karotz-6.1.config alone), the BIOS
#                    boots it with its own line: initrd=0x31800000,0x00500000
#                    root=/dev/ram rw init=/linuxrc quiet mem=65536K ...
#   RESTORE=<ms>     step 0: the head button held that long at power-on, the
#                    BIOS's "restore default partitions" (mtd3 -> mtd1,
#                    mtd4 -> mtd2, mtd6 erased), checked in the image after.
#
# The console is kept in <dir>/bios-boot.log; the image written by the BIOS
# in <dir>/bios-nand-after.bin (RESTORE only). "quiet" is on the BIOS's
# line, so the kernel's own banner does not show: what shows is the BIOS,
# then linuxrc.
set -eu
DIR=$(cd "${1:?a directory}" && pwd)
M=$(cd "${2:?the measure/ directory from the key}" && pwd)
SECS=${3:-300}
HERE=$(cd "$(dirname "$0")" && pwd)
CID=$(python3 "$HERE/karotz-flash.py" "$M" "$DIR/bios-nand.bin" | tail -1)
if [ -n "${KERNEL:-}" ]; then
	python3 - "$DIR/bios-nand.bin" "$KERNEL" "$CID" <<'PY'
import sys
img, kernel, cid = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
page, oob = {0x71: (512, 16), 0xDA: (2048, 64)}[cid]
start, size = 0x40000, 3 * 1024 * 1024          # mtd1, "Kernel"
data = open(kernel, "rb").read()
assert len(data) <= size, f"{kernel}: {len(data)} bytes, mtd1 holds {size}"
print(f"mtd1 <- {kernel}: {len(data)} bytes ({size - len(data)} left)")
data += b"\xff" * (size - len(data))
with open(img, "r+b") as f:
    for i in range(size // page):
        f.seek((start // page + i) * (page + oob))
        f.write(data[i * page:(i + 1) * page] + b"\xff" * oob)
PY
fi
LOG="$DIR/bios-boot.log"
# stdin closed: a byte on the serial port would stop the autoboot. The
# monitor on a socket: when the time is up, the MiB at 0x32000000 (ramoops,
# the 6.1 kernel's console as the next boot's autorun would read it) is
# saved before QEMU quits, and karotz/kexec/ramoops.py reads it.
podman run --rm -e MINI2440_NAND_CID="$CID" ${RESTORE:+-e MINI2440_HEAD_HELD=$RESTORE} -v "$DIR":/k:Z qemu-mini2440 sh -c "
  cp /k/bios-nand.bin /tmp/nand.bin
  ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic -monitor unix:/tmp/mon,server,nowait \
    -mtdblock /tmp/nand.bin < /dev/null 2>&1 | tr -d '\r' &
  sleep $SECS
  python3 -c '
import socket, time
s = socket.socket(socket.AF_UNIX); s.connect(\"/tmp/mon\"); s.settimeout(5)
for cmd in (b\"pmemsave 0x32000000 0x100000 /tmp/ramoops.bin\\n\", b\"quit\\n\"):
    s.sendall(cmd); time.sleep(2)
    try: s.recv(4096)
    except Exception: pass
'
  sleep 1; cp /tmp/ramoops.bin /k/bios-ramoops.bin 2> /dev/null
  ${RESTORE:+cp /tmp/nand.bin /k/bios-nand-after.bin}" > "$LOG" 2>&1 || true
if [ -s "$DIR/bios-ramoops.bin" ]; then
	python3 "$HERE/../../kexec/ramoops.py" "$DIR/bios-ramoops.bin" > "$DIR/bios-ramoops.txt" 2>&1 || true
	echo "ramoops: $(wc -l < "$DIR/bios-ramoops.txt") lines kept at 0x32000000 ($DIR/bios-ramoops.txt)"
fi
grep -aE "BIOS v|head button|Load Kernel|Load Ramdisk|Loading default|Now coping|Now erasing|Skipped bad|Linux version|Kernel panic|\[START\]|Run /linuxrc|rabbitv3:|ramoops|pstore|RAMDISK|Mounted root|init=" "$LOG" | cut -c1-160 | head -40
grep -aq "BIOS v1" "$LOG" || { echo "FAIL: no BIOS banner ($LOG)"; exit 1; }
if [ -n "${RESTORE:-}" ]; then
	grep -aq "Loading default partitions" "$LOG" || { echo "FAIL: the BIOS did not restore ($LOG)"; exit 1; }
	python3 - "$DIR/bios-nand-after.bin" "$DIR/bios-nand.bin" "$CID" <<'PY'
import sys
after, before, cid = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
page, oob = {0x71: (512, 16), 0xDA: (2048, 64)}[cid]
MB = 1024 * 1024
parts = {1: (0x40000, 3 * MB), 2: (0x340000, 5 * MB), 3: (0x840000, 3 * MB),
         4: (0xc40000, 5 * MB), 6: (0x1540000, 234 * MB)}
def data(path, n):
    start, size = parts[n]
    out = bytearray()
    with open(path, "rb") as f:
        for i in range(size // page):
            f.seek((start // page + i) * (page + oob))
            out += f.read(page)
    return bytes(out)
ok = True
for dst, src in ((1, 3), (2, 4)):
    same = data(after, dst) == data(after, src)
    print(f"mtd{dst} == mtd{src} after the restore: {same}"); ok &= same
    was = data(before, dst) == data(before, src)
    print(f"  (before: {was})")
erased = data(after, 6).count(0xFF) == len(data(after, 6))
print(f"mtd6 erased (all FF): {erased}; before: {data(before, 6).count(0xFF) == 234 * MB}"); ok &= erased
sys.exit(0 if ok else 1)
PY
	echo "PASS: the BIOS restored its default partitions in QEMU ($LOG)"
	exit 0
fi
grep -aq "Load Kernel" "$LOG" || { echo "FAIL: the BIOS did not load the kernel ($LOG)"; exit 1; }
if [ -n "${KERNEL:-}" ]; then
	# 6.1's console is the serial port (no VT); linuxrc's lines show.
	grep -aq "\[START\]" "$LOG" || { echo "FAIL: linuxrc did not start ($LOG)"; exit 1; }
	echo "PASS: the BIOS booted $KERNEL to linuxrc ($LOG)"
else
	# The rabbit's 2.6.32 puts its console on tty0 (Gengar's dmesg: "console
	# [tty0] enabled"), nothing of Linux on the serial port after the
	# decompressor: the witness is the BIOS's whole chain to that point.
	grep -aq "booting the kernel" "$LOG" || { echo "FAIL: the kernel did not decompress ($LOG)"; exit 1; }
	echo "PASS: the BIOS booted the rabbit's own kernel ($LOG)"
fi
