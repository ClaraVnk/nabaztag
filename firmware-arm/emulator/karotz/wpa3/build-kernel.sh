#!/bin/sh
# build-kernel.sh control|wpa3 -- one 2.6.32.71 for Versatile, to /out/zImage-<name>
set -eu
name=$1
rm -rf /k && cp -a /linux-2.6.32.71 /k && cd /k
patch -p1 -s < /modern-gcc.patch
if [ "$name" = wpa3 ]; then
    patch -p1 < /p/74-mac80211-sae.patch
    patch -p1 < /p/75-rt73usb-mfp.patch
fi
make -s ARCH=arm CROSS_COMPILE=arm-none-eabi- versatile_defconfig
while IFS= read -r l; do
    case "$l" in ''|'#'*) continue ;; esac
    o=${l%%=*}
    sed -i "/^$o=/d;/^# $o is not set/d" .config
    [ "${l#*=}" = n ] && echo "# $o is not set" >> .config || echo "$l" >> .config
done < /kernel.config
yes '' | make -s ARCH=arm CROSS_COMPILE=arm-none-eabi- oldconfig > /dev/null
for o in AEABI CFG80211 MAC80211 MAC80211_HWSIM PACKET; do
    grep -q "^CONFIG_$o=y" .config || { echo "kernel $name: CONFIG_$o did not stay" >&2; exit 1; }
done
make -s -j"$(nproc)" ARCH=arm CROSS_COMPILE=arm-none-eabi- zImage 2>&1 | grep -E "error" || true
mkdir -p /out && cp arch/arm/boot/zImage /out/zImage-$name
echo "kernel $name: $(wc -c < /out/zImage-$name) bytes"
