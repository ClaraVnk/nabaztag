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

# ota=kernel* (flash-proof.sh): FLASH-PLAN step 3, karotz/kexec/flash-kernel
# (mounted at /ota/flash-kernel) writing a kernel into mtd1 from the key, on
# a flash that already holds an installed rabbit's mtd1 (the key's 2.6.32)
# and mtd2 (its ramdisk). kernel-fault1: the first read-back is spoiled
# (written again, then fine); kernel-fault2: every read-back of the new
# kernel is spoiled (2.6.32 written back). Then go-back writes 2.6.32.
case " $(cat /proc/cmdline) " in *" ota=kernel"*)
    K=/mnt/usbkey; F=$K/flash
    FAULT=$(sed -n 's/.* ota=kernel-fault\([12]\).*/\1/p' /proc/cmdline)
    echo "OTA: kernel write, fault ${FAULT:-none}"
    nandwrite -q -p /dev/mtd1 $K/zImage; nandwrite -q -p /dev/mtd2 $K/rootfs.img.gz
    r=$(wc -c < $K/rootfs.img.gz)
    mkdir -p $F && cp $K/zImage $F/zImage-2632
    # The new kernel's bytes do not matter here, only that they are not the
    # old ones and are written and read back whole.
    head -c 2500000 /dev/urandom > $F/zImage-6.1
    ( cd $F && md5sum zImage-6.1 zImage-2632 > MD5SUMS )
    if [ -n "$FAULT" ]; then
        # nanddump spoiled: one byte changed in what it reads of mtd1, the
        # first time (fault 1) or each time the 6.1 image is read (fault 2).
        # mtd1 only: flash-kernel's check of the ramdisk reads mtd2.
        WHERE=$(command -v nanddump); REAL=$WHERE.real
        rm -f "$WHERE.tmp"; cp "$WHERE" "$REAL" && rm -f "$WHERE"
        printf '#!/bin/sh\n%s "$@"\nout=""; dev=""; while [ $# -gt 0 ]; do [ "$1" = -f ] && out=$2; dev=$1; shift; done\nn=$(cat /var/tmp/spoil 2>/dev/null || echo 0)\nif [ "$n" -gt 0 ] && [ -n "$out" ] && [ "$dev" = /dev/mtd1 ] && ! head -c $(wc -c < /mnt/usbkey/flash/zImage-2632) "$out" | cmp -s - /mnt/usbkey/flash/zImage-2632; then printf "\\377" | dd of="$out" bs=1 seek=1000 conv=notrunc 2>/dev/null; [ "$n" = 9 ] || echo $((n - 1)) > /var/tmp/spoil; fi\n' "$REAL" > "$WHERE"
        chmod 755 "$WHERE"
        [ "$FAULT" = 1 ] && echo 1 > /var/tmp/spoil || echo 9 > /var/tmp/spoil
    fi
    check() { if eval "$2"; then echo "OTA-PASS $1"; else echo "OTA-FAIL $1"; fi; }
    nanddump.real -q -l $r -f /var/tmp/mtd2-before.bin /dev/mtd2 2>/dev/null || nanddump -q -l $r -f /var/tmp/mtd2-before.bin /dev/mtd2
    touch $F/go-write
    KEY=$K FLASH_NO_REBOOT=1 FLASH_MTD_NAME= sh /ota/flash-kernel; rc=$?
    echo "OTA: flash-kernel returned $rc; its log:"; cat $F/log.txt
    rm -f /var/tmp/spoil
    z6=$(wc -c < $F/zImage-6.1); z2=$(wc -c < $F/zImage-2632)
    D=$(command -v nanddump.real || command -v nanddump)
    pages() { echo $(( ($1 + 2047) / 2048 * 2048 )); }      # nanddump reads whole pages
    $D -q -l $(pages $z6) -f /var/tmp/mtd1.bin /dev/mtd1
    $D -q -l $r -f /var/tmp/mtd2.bin /dev/mtd2
    if [ "$FAULT" = 2 ]; then
        check "it gave up on 6.1 and said so" "[ $rc = 2 ] && grep -q 'FAILED: 6.1' $F/log.txt"
        check "2.6.32 is back in mtd1" "head -c $z2 /var/tmp/mtd1.bin | cmp -s - $F/zImage-2632"
    else
        check "it wrote 6.1" "[ $rc = 0 ] && grep -q 'DONE: 6.1' $F/log.txt"
        check "6.1 is in mtd1" "head -c $z6 /var/tmp/mtd1.bin | cmp -s - $F/zImage-6.1"
        [ "$FAULT" = 1 ] && check "a spoiled read-back was written once more" "grep -q 'once more' $F/log.txt"
    fi
    check "the ramdisk (mtd2) was not touched" "cmp -s /var/tmp/mtd2.bin /var/tmp/mtd2-before.bin"
    check "/usr was not touched: key, settings, client" "grep -q THE-ADOPTION-KEY /usr/etc/conf/terrier/key && grep -q lang=fr /usr/etc/conf/voice.conf && grep -q A-NEWER-CLIENT /usr/etc/conf/terrier/terrier-karotz"
    check "the settings were copied to the key" "grep -q THE-ADOPTION-KEY $F/conf-backup/terrier/key"
    check "the triggers are gone" "[ ! -e $F/go-write ] && [ ! -e $F/go-back ]"
    # The way back: go-back writes 2.6.32.
    touch $F/go-back
    KEY=$K FLASH_NO_REBOOT=1 FLASH_MTD_NAME= sh /ota/flash-kernel; rc=$?
    $D -q -l $(pages $z2) -f /var/tmp/mtd1.bin /dev/mtd1
    check "go-back wrote 2.6.32" "[ $rc = 0 ] && head -c $z2 /var/tmp/mtd1.bin | cmp -s - $F/zImage-2632"
    # A bad MD5SUMS stops before anything is written.
    echo "00000000000000000000000000000000  zImage-6.1" > $F/MD5SUMS; touch $F/go-write
    KEY=$K FLASH_NO_REBOOT=1 FLASH_MTD_NAME= sh /ota/flash-kernel; rc=$?
    $D -q -l $(pages $z2) -f /var/tmp/mtd1.bin /dev/mtd1
    check "it restarts the rabbit with reboot -f (linuxrc has no init to ask)" "grep -q 'reboot -f' /ota/flash-kernel"
    check "a bad MD5SUMS writes nothing" "[ $rc = 1 ] && head -c $z2 /var/tmp/mtd1.bin | cmp -s - $F/zImage-2632"
    # A ramdisk in mtd2 that is not the key's stops 6.1 before anything.
    ( cd $F && md5sum zImage-6.1 zImage-2632 > MD5SUMS )
    flash_erase -q /dev/mtd2 0 1; touch $F/go-write
    KEY=$K FLASH_NO_REBOOT=1 FLASH_MTD_NAME= sh /ota/flash-kernel; rc=$?
    $D -q -l $(pages $z2) -f /var/tmp/mtd1.bin /dev/mtd1
    check "another ramdisk in mtd2 writes nothing" "[ $rc = 1 ] && grep -q 'not the key.s rootfs.img.gz' $F/log.txt && head -c $z2 /var/tmp/mtd1.bin | cmp -s - $F/zImage-2632"
    echo "OTA-RESULT"
    /bin/busybox poweroff -f
    sleep 600 ;;                      # poweroff returns before QEMU stops: nothing after it runs
esac

# ota=package (run.sh OTA_MODE): no stick. The update from the rabbit's page
# puts the package where installs keep things, the client's own folder, and
# shows it to the install where it reads the stick (KEY=/mnt/usbkey).
MODE=stick
case " $(cat /proc/cmdline) " in
    *" ota=package "*) MODE=package ;;
    *" ota=package-usr-terrier "*) MODE=package-usr-terrier ;;
esac
PKG=/usr/etc/conf/terrier/os-update
CLIENT=/usr/etc/conf/terrier
if [ "$MODE" = package-usr-terrier ]; then
    # A client installed by install.sh (Gengar, 2026-11-21): its folder,
    # key and package in /usr/terrier, which the install does not keep.
    CLIENT=/usr/terrier
    PKG=/usr/terrier/os-update
    mkdir -p $CLIENT && mv /usr/etc/conf/terrier/* $CLIENT/ && rmdir /usr/etc/conf/terrier
fi
if [ "$MODE" != stick ]; then
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
check "the adoption key was kept" "grep -q THE-ADOPTION-KEY $CLIENT/key 2>/dev/null"
check "the client the Terrier sent was kept" "grep -q A-NEWER-CLIENT $CLIENT/terrier-karotz 2>/dev/null"
check "the image's client is shipped" "[ -x /usr/karotz/terrier/terrier-karotz ]"
if [ "$MODE" != stick ]; then
    check "the package outlived the install (the client removes it)" "[ -f $PKG/build_id ] && [ -f $PKG/install-log.txt ]"
fi
echo "OTA-RESULT"
/bin/busybox poweroff -f
