#!/bin/sh
# The kexec directory of a Karotz's USB key (Terrier OS with the kexec
# kernel already installed): kexec, the 6.1 zImage, the measure initramfs,
# and the autorun that starts it once when kexec/go is there.
#
#   karotz/kexec/make-key.sh <kexec binary> <6.1 zImage> <ramdisk tree> <key dir>
#
# <ramdisk tree>: the rabbit's own ramdisk unpacked (its busybox and libs).
# The key's own autorun (the install) is kept as autorun.install.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
KEXEC=$1 ZIMAGE=$2 TREE=$3 KEY=$4
mkdir -p "$KEY/kexec"
python3 "$HERE/mkcpio.py" "$KEY/kexec/measure.cpio.gz" "$TREE" "$HERE/measure-init"
# The rabbit's own system for kexec/go-os: its ramdisk whole, its linuxrc as
# /init with /usr mounted read-only (6.1's YAFFS writing not yet proven).
sed 's#^mount -t yaffs /dev/mtdblock6 /usr$#mount -t yaffs -o ro /dev/mtdblock6 /usr#' "$TREE/linuxrc" > "$KEY/kexec/linuxrc-ro"
grep -q "mount -t yaffs -o ro /dev/mtdblock6 /usr" "$KEY/kexec/linuxrc-ro" || { echo "linuxrc: /usr mount line not found"; exit 1; }
# /usr/etc (/etc, the client's conf/terrier among it) and /usr/terrier in
# RAM over the read-only /usr: run.sh appends to client.log there, and a
# failed redirection never started the client; udhcpc writes resolv.conf
# there, and the rabbit had no DNS (Gengar under 6.1, 2026-11-24). The
# flash is not touched.
sed -i.bak 's#^mount -t yaffs -o ro /dev/mtdblock6 /usr$#&\
for d in /usr/etc /usr/terrier; do [ -d $d ] || continue; m=/var/rw$(echo $d | tr / _); mkdir -p $m; cp -a $d/. $m/; mount --bind $m $d; done#' "$KEY/kexec/linuxrc-ro" && rm -f "$KEY/kexec/linuxrc-ro.bak"
grep -q "mount --bind" "$KEY/kexec/linuxrc-ro" || { echo "linuxrc: the client directory bind failed"; exit 1; }
# Traced: os-prelude, right after the #! line.
sed -i.bak "1r $HERE/os-prelude" "$KEY/kexec/linuxrc-ro" && rm -f "$KEY/kexec/linuxrc-ro.bak"
# 6.1 names the key sda (usb-storage), 2.6.32 uba (ub): mdev's rule for
# both, in a copy of the ramdisk. 6.1 has no uevent helper either, so the
# key is mounted by linuxrc's second `mdev -s`, as at power-on.
OS=$(mktemp -d)
cp -a "$TREE/." "$OS/"
printf '%s\n' 'sd[a-z][0-9]*    0:0 0660 */karotz/scripts/usbkey.sh' >> "$OS/karotz/etc/mdev.conf"
python3 "$HERE/mkcpio.py" --all "$KEY/kexec/terrieros.cpio.gz" "$OS" "$KEY/kexec/linuxrc-ro"
rm -rf "$OS"
cp "$KEXEC" "$KEY/kexec/kexec"
cp "$ZIMAGE" "$KEY/kexec/zImage-6.1"
[ -e "$KEY/autorun.install" ] || cp "$KEY/autorun" "$KEY/autorun.install"
cp "$HERE/autorun" "$KEY/autorun"
echo "ready: touch $KEY/kexec/go (the measure) or $KEY/kexec/go-os (Terrier OS) to start 6.1 at the next boot"
