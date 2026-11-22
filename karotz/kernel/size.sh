#!/bin/sh
# Step 1 of docs/TERRIER-OS-KERNEL.md: does a useful Linux 6.1 for the Karotz
# fit its 3 MiB kernel partition (mtd1)? Builds tinyconfig plus
# karotz-6.1.config, says which asked options Kconfig could not keep, and the
# zImage's size against 3145728 bytes. No YAFFS yet (out of tree, step 3).
set -eu
cd /linux
export ARCH=arm CROSS_COMPILE=arm-none-eabi-
make -s tinyconfig
scripts/kconfig/merge_config.sh -m .config /k/karotz-6.1.config > /dev/null
make -s olddefconfig
echo "== asked but not kept by Kconfig:"
grep -E '^CONFIG_[A-Z0-9_]+=y' /k/karotz-6.1.config | while IFS= read -r line; do
    grep -qx "$line" .config || echo "   ${line%=y}"
done
make -s -j"$(nproc)" zImage 2>&1 | grep -vE '^\s*$' | grep -v "warning: -z norelro" | tail -20
z=$(stat -c %s arch/arm/boot/zImage)
echo "== zImage: $z bytes (mtd1 holds 3145728: $(( (3145728 - z) / 1024 )) KiB left)"
[ "$z" -le 3145728 ] && echo "FITS" || echo "TOO BIG"
