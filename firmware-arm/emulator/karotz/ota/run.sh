#!/bin/sh
# Boot the kernel with Terrier OS's root filesystem and the key's files, and
# let LapinLibre's install run against the simulated flash (init.sh).
# OTA_MODE=package: from a package in the client's folder, no stick.
set -u
python3 /ota/cpio-list.py /ota/rootfs / /ota/key /mnt/usbkey > /tmp/list
echo "file /init /ota/init.sh 755 0 0" >> /tmp/list
# flash-proof.sh mounts karotz/kexec/flash-kernel at /ota/flash-kernel in the
# container; the guest boots its own initramfs, so it goes in there too.
if [ -f /ota/flash-kernel ] || [ -f /ota/flash-ramdisk ]; then
    echo "dir /ota 755 0 0" >> /tmp/list
    for t in flash-kernel flash-ramdisk; do
        [ -f /ota/$t ] && echo "file /ota/$t /ota/$t 755 0 0" >> /tmp/list
    done
fi
/ota/gen_init_cpio /tmp/list | gzip -1 > /tmp/initramfs.gz
echo "initramfs: $(wc -c < /tmp/initramfs.gz) bytes"
timeout 900 qemu-system-arm -M versatilepb -m 256 -nographic -no-reboot \
    -kernel /ota/zImage -initrd /tmp/initramfs.gz \
    -append "console=ttyAMA0 rdinit=/init ota=${OTA_MODE:-stick} nandsim.first_id_byte=0xec nandsim.second_id_byte=0xda nandsim.third_id_byte=0x10 nandsim.fourth_id_byte=0x95 nandsim.parts=2,24,40" \
    </dev/null 2>&1 | tr -d '\000\r' | grep -v "Writing data to block\|^\[<c0" | sed -n '/^OTA/,$p' | grep -v "^$" 
