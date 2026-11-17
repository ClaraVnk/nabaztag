#!/bin/sh
# Boot each kernel with the same initramfs; the control must not connect,
# the patched one must.
set -u
R=/tmp/root
for k in control wpa3; do
    rm -rf $R && mkdir -p $R/sbin $R/etc $R/proc $R/sys $R/tmp $R/dev
    cp /wpa3/rootfs/sbin/* $R/sbin/ && cp /wpa3/rootfs/init $R/init
    cp /wpa3/rootfs-etc/hostapd.conf /wpa3/rootfs-etc/wpa_supplicant.conf $R/etc/
    ( cd $R && find . | cpio -o -H newc 2>/dev/null | gzip ) > /tmp/initramfs-$k.gz
    echo "######## $k"
    timeout 240 qemu-system-arm -M versatilepb -m 128 -nographic -no-reboot \
        -kernel /wpa3/zImage-$k -initrd /tmp/initramfs-$k.gz \
        -append "console=ttyAMA0 rdinit=/init" </dev/null 2>&1 \
        | tr -d '\000\r' | sed -n '/WPA3: kernel/,/WPA3-RESULT/p'
done
