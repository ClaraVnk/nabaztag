#!/bin/sh
# Run by startup.sh once the rabbit is up (the line root.sh adds), from
# /usr/etc/conf/ota-test, which installs keep.
#
# Phase 1, the whole system running (daemons, controller, the Terrier's
# client from /usr/etc/conf/terrier): the install of the package in
# /usr/etc/conf/os-update, with the very command the client runs (Karotz
# client 0.1.18, osupdate.c). The install reboots; root.sh's /sbin/reboot
# copies the flash to this folder first.
# Phase 2, the next boot, on the root filesystem the install wrote and the
# /usr it left: what the owner would find.
T=/usr/etc/conf/ota-test
exec > /dev/console 2>&1
check() { if eval "$2"; then echo "LIVE-PASS $1"; else echo "LIVE-FAIL $1"; fi; }
running() { ps | grep -v grep | grep -q "$1"; }

# Let everything start: the services, the clock, the client.
sleep 40
if [ ! -f $T/installed ]; then
    echo "LIVE: phase 1 -- what runs while the install runs:"
    ps | grep -E "daemon|controller|terrier-karotz|dbus" | grep -v grep | sed 's/^/LIVE-PS /'
    check "the client runs" "running terrier-karotz"
    if ! running terrier-karotz; then
        echo "LIVE: why -- the services started, the client's folder, its log:"
        ls /etc/init.d/ /usr/etc/init.d/ 2>&1 | sed 's/^/LIVE-DIAG /'
        ls -la /usr/etc/conf/terrier 2>&1 | sed 's/^/LIVE-DIAG /'
        cat /usr/etc/conf/terrier/settings /usr/etc/conf/terrier/client.log 2>&1 | sed 's/^/LIVE-DIAG /'
        cat /proc/mtd | sed 's/^/LIVE-DIAG /'
        tail -20 /var/log/messages /tmp/*.log /var/log/*.log 2>/dev/null | grep -iE "terrier|S50|==>" | sed 's/^/LIVE-DIAG /'
    fi
    check "the controller runs" "running controller"
    touch $T/installed
    # What to expect after: the package goes once the client starts again.
    cp /usr/etc/conf/os-update/install.conf /usr/etc/conf/os-update/build_id $T/
    # osupdate.c's default install command, the package at /usr/etc/conf/os-update.
    sh -c "grep -q ' /mnt/usbkey ' /proc/mounts && exit 9; mkdir -p /mnt/usbkey && mount --bind '/usr/etc/conf/os-update' /mnt/usbkey && bash /mnt/usbkey/autorun; r=\$?; umount /mnt/usbkey 2>/dev/null; exit \$r"
    echo "LIVE: the install returned $? without rebooting:"
    tail -15 /usr/etc/conf/os-update/install-log.txt 2>/dev/null
    echo "LIVE-RESULT"
    busybox poweroff -f
fi

echo "LIVE: phase 2 -- the boot after the install:"
K=$T
check "the build runs (sys_version)" "grep -qx \"\$(sed -n 's/^BUILD_VERSION=//p' $K/install.conf)\" /usr/etc/conf/sys_version"
check "the build is recorded" "cmp -s $K/build_id /usr/etc/conf/lapinlibre_build_id"
check "the root filesystem booted is the one written" "[ -f $T/mtd2.bin ]"
check "the Wi-Fi settings were kept" "grep -q 'ssid=\"Terrier\"' /usr/etc/conf/wpa.conf"
check "the voice settings were kept" "grep -q lang=fr /usr/etc/conf/voice.conf"
check "the adoption key was kept" "cmp -s /usr/etc/conf/terrier/key $T/key.before"
check "the client's settings were kept" "cmp -s /usr/etc/conf/terrier/settings $T/settings.before"
check "the client the Terrier sent was kept" "cmp -s /usr/etc/conf/terrier/terrier-karotz $T/client.before"
check "the client runs again" "running terrier-karotz"
check "the controller runs again" "running controller"
check "the new /usr is in place" "[ -x /usr/etc/init.d/S50terrier ] && [ -n \"\$(ls /usr/bin)\" ]"
echo "LIVE-RESULT"
busybox poweroff -f
