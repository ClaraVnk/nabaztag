#!/bin/sh
# Step 1 of docs/TERRIER-OS-KERNEL.md: does a useful Linux 6.1 for the Karotz
# fit its 3 MiB kernel partition (mtd1)? Builds tinyconfig plus
# karotz-6.1.config, says which asked options Kconfig could not keep, and the
# zImage's size against 3145728 bytes. No YAFFS yet (out of tree, step 3).
set -eu
cd /linux
export ARCH=arm CROSS_COMPILE=arm-none-eabi-
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
if [ "$z" -le 3145728 ]; then echo "FITS"; else echo "TOO BIG"; fi
# OUT: where to copy the zImage (a mounted directory), for the emulator, with
# vmlinux and System.map for syscalls-audit.py.
[ -n "${OUT:-}" ] && cp arch/arm/boot/zImage vmlinux System.map .config "$OUT/"
exit 0
