#!/bin/sh
# Step 2 of docs/TERRIER-OS-KERNEL.md: the 6.1 zImage boots in QEMU's S3C2440
# to a shell on LapinLibre's root file system. Run where podman is (the dev
# VM), from the repository root:
#
#   karotz/kernel/qemu-mini2440/boot.sh <dir>
#
# <dir> holds zImage (size.sh with EXTRA=/k/karotz-6.1-debug.config OUT=...)
# and rootfs.cramfs (a Terrier OS package's rootfs.img.gz, gunzipped). The
# shell is fed four commands; PASS when it answers them.
set -eu
DIR=$(cd "${1:?a directory with zImage and rootfs.cramfs}" && pwd)
LOG="$DIR/boot.log"
podman run --rm -v "$DIR":/k:ro,Z qemu-mini2440 sh -c '
  dd if=/dev/zero of=/tmp/nand.bin bs=2112 count=65536 2>/dev/null
  (sleep 60; echo; echo uname -a; echo ls /; echo echo SHELL-OK) |
  timeout 90 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage -initrd /k/rootfs.cramfs \
    -append "console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh" 2>&1 |
  tr -d "\r"' > "$LOG" 2>&1 || true
grep -E "^Linux version|^Machine:|^CPU: ARM920T|VFS: Mounted root|armv4tl GNU" "$LOG" || true
if grep -qx "SHELL-OK" "$LOG"; then echo "PASS: a shell on the 6.1 kernel ($LOG)"; else echo "FAIL ($LOG)"; exit 1; fi
