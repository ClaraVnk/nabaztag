#!/bin/sh
# root.sh <rootfs.img.gz> <out cramfs>: a Terrier OS root filesystem made
# bootable on QEMU Versatile, nothing else changed:
#  - linuxrc mounts /usr from the SCSI disk (ext2) instead of yaffs on mtd6;
#  - network.sh brings up QEMU's network card where the rabbit has Wi-Fi;
#  - startup.sh ends with phase.sh (the proof, from /usr/etc/conf/ota-test);
#  - /sbin/reboot copies the flash (mtd1, mtd2) to /usr/etc/conf/ota-test
#    before the real reboot, so the next boot can start from what the install
#    wrote (nandsim forgets it with QEMU).
set -eu
W=$(mktemp -d)
gunzip -c "$1" > "$W/root.cramfs"
python3 /ota/uncramfs.py "$W/root.cramfs" "$W/root"
R=$W/root
grep -q 'mount -t yaffs /dev/mtdblock6 /usr' $R/linuxrc
sed -i 's|^\( *\)mount -t yaffs /dev/mtdblock6 /usr|\1for i in 1 2 3 4 5 6 7 8 9 10; do [ -e /sys/block/sda/dev ] \&\& break; sleep 1; done; mknod /dev/sda b $(sed "s/:/ /" /sys/block/sda/dev) 2>/dev/null; mount -t ext2 /dev/sda /usr|' $R/linuxrc
cat > $R/karotz/scripts/network.sh <<'NET'
#!/bin/sh
# ota-live: QEMU's network card stands for the rabbit's Wi-Fi.
# The loopback as Terrier OS's own network.sh brings it up: the client
# listens on 127.0.0.1:8188 and died without it.
ifconfig lo 127.0.0.1 up
ifconfig eth0 up
udhcpc -i eth0 -n -q -t 10 > /dev/null 2>&1
exit 0
NET
chmod 755 $R/karotz/scripts/network.sh
# startup.sh ends with "exit 0": the hook goes before it.
[ "$(tail -n 1 $R/karotz/scripts/startup.sh)" = "exit 0" ]
sed -i '$ d' $R/karotz/scripts/startup.sh
printf '# ota-live: the proof\n[ -x /usr/etc/conf/ota-test/phase.sh ] && /usr/etc/conf/ota-test/phase.sh &\nexit 0\n' >> $R/karotz/scripts/startup.sh
rm -f $R/sbin/reboot
cat > $R/sbin/reboot <<'RB'
#!/bin/sh
# ota-live: the flash to /usr/etc/conf/ota-test, then the real reboot.
T=/usr/etc/conf/ota-test
if [ -f $T/installed ] && [ ! -f $T/mtd2.bin ]; then
    nanddump -q -f $T/mtd1.bin /dev/mtd1 && nanddump -q -f $T/mtd2.bin /dev/mtd2 && sync
    echo "LIVE: the install asked for its reboot; the flash is copied" > /dev/console
fi
exec /bin/busybox reboot "$@"
RB
chmod 755 $R/sbin/reboot
mkfs.cramfs "$R" "$2" > /dev/null
rm -rf "$W"
