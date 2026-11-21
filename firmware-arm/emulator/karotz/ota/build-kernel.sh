#!/bin/sh
# build-kernel.sh -- 2.6.32.71 for Versatile with nandsim, to /out/zImage
set -eu
rm -rf /k && cp -a /linux-2.6.32.71 /k && cd /k
patch -p1 -s < /modern-gcc.patch
make -s ARCH=arm CROSS_COMPILE=arm-none-eabi- versatile_defconfig
while IFS= read -r l; do
    case "$l" in ''|'#'*) continue ;; esac
    o=${l%%=*}
    sed -i "/^$o=/d;/^# $o is not set/d" .config
    [ "${l#*=}" = n ] && echo "# $o is not set" >> .config || echo "$l" >> .config
done < /kernel.config
yes '' | make -s ARCH=arm CROSS_COMPILE=arm-none-eabi- oldconfig > /dev/null
for o in AEABI MTD_NAND_NANDSIM MTD_CHAR BLK_DEV_RAM DEVTMPFS; do
    grep -q "^CONFIG_$o=y" .config || { echo "kernel: CONFIG_$o did not stay" >&2; exit 1; }
done
make -s -j"$(nproc)" ARCH=arm CROSS_COMPILE=arm-none-eabi- zImage 2>&1 | grep -E "error" || true
mkdir -p /out && cp arch/arm/boot/zImage /out/zImage
echo "kernel: $(wc -c < /out/zImage) bytes"
