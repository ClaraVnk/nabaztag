#!/bin/sh
# Step 2 of docs/TERRIER-OS-KERNEL.md: the 6.1 zImage boots in QEMU's S3C2440
# to a shell on LapinLibre's root file system. Run where podman is (the dev
# VM), from the repository root:
#
#   karotz/kernel/qemu-mini2440/boot.sh <dir>
#
# <dir> holds zImage (size.sh with EXTRA=/k/karotz-6.1-debug.config OUT=...)
# and rootfs.cramfs (a Terrier OS package's rootfs.img.gz, gunzipped). The
# shell is fed a few commands; PASS when it answers them. MACH=1912 boots it
# as the Karotz (its board file, rabbitv3/), the default 1999 as the mini2440.
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
LOG="$DIR/boot.log"
podman run --rm -e MINI2440_MACH="${MACH:-1999}" -v "$DIR":/k:ro,Z qemu-mini2440 sh -c '
  dd if=/dev/zero of=/tmp/nand.bin bs=2112 count=65536 2>/dev/null
  (sleep 60; echo; echo uname -a; echo ls /; echo mount -t devtmpfs dev /dev; echo "ls /dev/spidev0.1 >/dev/null && echo SPIDEV-OK"; echo "dmesg | grep rabbit- | sed s/^/DMESG-/"; echo "mknod /dev/po c 53 0 && dd if=/dev/po bs=1 count=1 2>/dev/null | od -c | head -1 | sed s/^/PO-/"; echo echo SHELL-OK) |
  timeout 90 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append "console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh" 2>&1 |
  tr -d "\r"' > "$LOG" 2>&1 || true
grep -E "^Linux version|^Machine:|^CPU: ARM920T|VFS: Mounted root|armv4tl GNU|direct boot|^SPIDEV-OK|^DMESG-|^PO-" "$LOG" || true
grep -qx "SHELL-OK" "$LOG" || { echo "FAIL: no shell ($LOG)"; exit 1; }
# As the Karotz (its board file), the LED driver's spidev must be there.
if [ "${MACH:-1999}" = 1912 ] && ! grep -aq "^SPIDEV-OK" "$LOG"; then
	echo "FAIL: no /dev/spidev0.1 for the LED driver ($LOG)"; exit 1
fi
# And its own drivers (step 5): button, power-on, ears, and a power-on byte.
if [ "${MACH:-1999}" = 1912 ]; then
	for d in rabbit_button rabbit_poweron rabbit_ears; do
		grep -aq "^DMESG-.*/dev/$d (" "$LOG" || { echo "FAIL: no $d driver ($LOG)"; exit 1; }
	done
	grep -aEq "^PO-0000000 +[01]" "$LOG" || { echo "FAIL: /dev/rabbit_poweron gave no byte ($LOG)"; exit 1; }
fi
echo "PASS: a shell on the 6.1 kernel ($LOG)"
