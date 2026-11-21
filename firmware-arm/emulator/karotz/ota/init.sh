#!/bin/bash
# /init in QEMU: Terrier OS's own root filesystem (its bash, busybox and
# mtd-utils), a simulated NAND cut as the Karotz's, a /usr standing for an
# installed rabbit's, and the key's files in /mnt/usbkey. LapinLibre's
# install (autorun) then runs as on the rabbit; its final reboot is
# replaced by the checks below.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
# /dev first: the console node in the initramfs, made without root, does not
# carry the output; devtmpfs's does.
mount -t devtmpfs devtmpfs /dev
exec < /dev/console > /dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
# /tmp is var/tmp, and /var a RAM file system, as the rabbit's linuxrc mounts it.
mount -t tmpfs tmpfs /var
mkdir -p /var/tmp /var/run /var/log /var/lock
mount -t tmpfs -o size=200m tmpfs /usr
echo "OTA: kernel $(uname -r), flash:"
cat /proc/mtd

# An installed rabbit's /usr: the build's own (most commands live there), its
# settings, an older build id, and its adoption key.
gunzip -c /mnt/usbkey/usr.tar.gz | tar -xf - -C /usr
mkdir -p /usr/etc/conf
echo "[karotz]" > /usr/etc/conf/karotz.conf
echo "lang=fr" > /usr/etc/conf/voice.conf
printf 'network={\n\tssid="Terrier"\n\tpsk="nabaztag"\n}\n' > /usr/etc/conf/wpa.conf
echo "an-older-build" > /usr/etc/conf/lapinlibre_build_id
# Terrier OS's client keeps its key, its settings and the client the
# Terrier last sent in /usr/etc/conf/terrier (S50terrier): an install must
# keep all three, and not put the image's older client back.
mkdir -p /usr/etc/conf/terrier
echo "THE-ADOPTION-KEY" > /usr/etc/conf/terrier/key
chmod 600 /usr/etc/conf/terrier/key
echo "SERVER=terrier.example.com" > /usr/etc/conf/terrier/settings
echo "A-NEWER-CLIENT" > /usr/etc/conf/terrier/terrier-karotz

# The install ends with /sbin/reboot -f, then exit 0: a reboot that only
# notes it was asked for lets the checks run here once the install returns.
# The link to busybox goes first, or the script would be written into busybox.
rm -f /sbin/reboot
printf '#!/bin/sh\ntouch /var/tmp/reboot-asked\n' > /sbin/reboot
chmod 755 /sbin/reboot

# ota=package (run.sh OTA_MODE): no stick. The update from the rabbit's page
# puts the package where installs keep things, the client's own folder, and
# shows it to the install where it reads the stick (KEY=/mnt/usbkey).
MODE=stick
case " $(cat /proc/cmdline) " in *" ota=package "*) MODE=package ;; esac
PKG=/usr/etc/conf/terrier/os-update
if [ "$MODE" = package ]; then
    mkdir -p $PKG && cp -a /mnt/usbkey/. $PKG/ && rm -rf /mnt/usbkey/* && mount --bind $PKG /mnt/usbkey
    echo "OTA: the package is in $PKG, bound on /mnt/usbkey ($(ls /mnt/usbkey | wc -l) files)"
fi

echo "OTA: running the key's install ($MODE)"
bash /mnt/usbkey/autorun > /var/tmp/autorun.log 2>&1
rc=$?
echo "OTA: the install returned $rc; its log:"
cat /mnt/usbkey/install-log.txt 2>/dev/null | grep -v "^$"
grep -i "error" /var/tmp/autorun.log | head -5

check() { if eval "$2"; then echo "OTA-PASS $1"; else echo "OTA-FAIL $1"; fi; }
K=/mnt/usbkey
z=$(wc -c < $K/zImage); r=$(wc -c < $K/rootfs.img.gz)
# nanddump, mtd-utils' reader (data only, no OOB): head on /dev/mtdN oopsed
# 2.6.32's nandsim on close.
nanddump -q -l $z -f /var/tmp/mtd1.bin /dev/mtd1
nanddump -q -l $r -f /var/tmp/mtd2.bin /dev/mtd2
check "the install asked for its reboot" "[ -f /var/tmp/reboot-asked ]"
check "the kernel is in mtd1" "head -c $z /var/tmp/mtd1.bin | cmp -s - $K/zImage"
check "the root filesystem is in mtd2" "head -c $r /var/tmp/mtd2.bin | cmp -s - $K/rootfs.img.gz"
check "the build is recorded" "cmp -s $K/build_id /usr/etc/conf/lapinlibre_build_id"
check "the settings were kept" "grep -q lang=fr /usr/etc/conf/voice.conf"
check "the Wi-Fi was kept" "grep -q 'ssid=\"Terrier\"' /usr/etc/conf/wpa.conf"
check "the new /usr is unpacked" "[ -n \"\$(ls /usr/bin 2>/dev/null)\" ]"
check "the adoption key was kept" "grep -q THE-ADOPTION-KEY /usr/etc/conf/terrier/key 2>/dev/null"
check "the client the Terrier sent was kept" "grep -q A-NEWER-CLIENT /usr/etc/conf/terrier/terrier-karotz 2>/dev/null"
check "the image's client is shipped" "[ -x /usr/karotz/terrier/terrier-karotz ]"
if [ "$MODE" = package ]; then
    check "the package outlived the install (the client removes it)" "[ -f $PKG/build_id ] && [ -f $PKG/install-log.txt ]"
fi
echo "OTA-RESULT"
/bin/busybox poweroff -f
