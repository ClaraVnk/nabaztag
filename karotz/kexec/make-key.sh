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
cp "$KEXEC" "$KEY/kexec/kexec"
cp "$ZIMAGE" "$KEY/kexec/zImage-6.1"
[ -e "$KEY/autorun.install" ] || cp "$KEY/autorun" "$KEY/autorun.install"
cp "$HERE/autorun" "$KEY/autorun"
echo "ready: touch $KEY/kexec/go to start 6.1 at the next boot"
