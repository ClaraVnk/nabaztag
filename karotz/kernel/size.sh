#!/bin/sh
# Step 1 of docs/TERRIER-OS-KERNEL.md: does a useful Linux 6.1 for the Karotz
# fit its 3 MiB kernel partition (mtd1)? Builds tinyconfig plus
# karotz-6.1.config, says which asked options Kconfig could not keep, and the
# zImage's size against 3145728 bytes. No YAFFS yet (out of tree, step 3).
set -eu
cd /linux
export ARCH=arm CROSS_COMPILE=arm-none-eabi-
# The banner's date, user and host fixed: the same zImage from one build to
# the next (Terrier OS 1.0.0 records its sha256). Without "UTC": BusyBox's
# date refuses it, and gen_initramfs.sh then dated the built-in cpio at the
# build's own time (two zImages, 2026-11-26). The container runs in UTC.
export KBUILD_BUILD_TIMESTAMP="${KBUILD_BUILD_TIMESTAMP:-2026-11-26 00:00:00}" \
       KBUILD_BUILD_USER=terrier-os KBUILD_BUILD_HOST=build
make -s tinyconfig
# EXTRA: more fragments (karotz-6.1-debug.config for the emulator, step 2).
scripts/kconfig/merge_config.sh -m .config /k/karotz-6.1.config ${EXTRA:-} > /dev/null
make -s olddefconfig
echo "== asked but not kept by Kconfig:"
cat /k/karotz-6.1.config ${EXTRA:-} | grep -E '^CONFIG_[A-Z0-9_]+=|^# CONFIG_[A-Z0-9_]+ is not set' | while IFS= read -r line; do
    grep -qx "$line" .config || echo "   $line"
done
make -s -j"$(nproc)" zImage 2>&1 | grep -vE '^\s*$' | grep -v "warning: -z norelro" | tail -20
z=$(stat -c %s arch/arm/boot/zImage)
echo "== zImage: $z bytes (mtd1 holds 3145728: $(( (3145728 - z) / 1024 )) KiB left)"
# The compiler and the image's hash, to record with a release (the Alpine
# toolchain moved from GCC 15.1.0 to 16.1.0 between 2026-11-22 and 11-24).
echo "== $(arm-none-eabi-gcc --version | head -1)"
echo "== sha256 $(sha256sum arch/arm/boot/zImage | cut -d" " -f1)"
if [ "$z" -le 3145728 ]; then echo "FITS"; else echo "TOO BIG"; fi
# OUT: where to copy the zImage (a mounted directory), for the emulator, with
# vmlinux and System.map for syscalls-audit.py.
[ -n "${OUT:-}" ] && cp arch/arm/boot/zImage vmlinux System.map .config "$OUT/"
exit 0
