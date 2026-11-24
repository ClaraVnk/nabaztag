#!/bin/sh
# Does a 6.1 kernel that panics restart the Karotz by itself? CONFIG_PANIC_TIMEOUT=10
# (karotz-6.1.config, review of 2026-11-24): the BIOS adds nothing to the command line, so
# panic= cannot come from there, and a kernel that stops in a panic would otherwise sit
# until someone unplugs the rabbit. In QEMU as the Karotz (MACH=1912) the kernel is given
# an init that does not exist: it must panic and say "Rebooting in 10 seconds" (-no-reboot
# then ends QEMU). Run like boot.sh, with the same <dir> (zImage, rootfs.cramfs):
#
#   karotz/kernel/qemu-mini2440/panic.sh <dir>
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
LOG="$DIR/panic.log"
podman run --rm -e MINI2440_MACH="${MACH:-1912}" -v "$DIR":/k:ro,Z qemu-mini2440 sh -c '
  dd if=/dev/zero of=/tmp/nand.bin bs=2112 count=65536 2>/dev/null
  timeout 120 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append "console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/nonexistent" 2>&1 |
  tr -d "\r"' > "$LOG" 2>&1 || true
grep -aE "^Linux version|Kernel panic|Rebooting in" "$LOG" | head -5
if grep -aq "Kernel panic" "$LOG" && grep -aq "Rebooting in 10 seconds" "$LOG"; then
    echo "PASS: a panic restarts the rabbit after 10 s ($LOG)"
else
    echo "FAIL ($LOG)"; exit 1
fi
