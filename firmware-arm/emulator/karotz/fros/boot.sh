#!/bin/sh
# Boot a Karotz dump: its root ramdisk (mtd2) and, when it can be rebuilt,
# its /usr (mtd6), on the Versatile kernel built into this image.
#
#   podman run --rm -it -v <dump dir>:/dump:ro,Z fros
#   SECONDS_UP=60 podman run --rm -v <dump dir>:/dump:ro,Z fros   # boot, log, stop
#
# <dump dir> holds karotz-mtdN.bin as measure.sh or nanddump wrote them.
# It stays outside the image and outside the repository: a dump carries the
# owner's Wi-Fi password.
set -eu
D=/dump
W=$(mktemp -d)

[ -f "$D/karotz-mtd2.bin" ] || { echo "no karotz-mtd2.bin in $D" >&2; exit 2; }
# mtd2 is a gzip'd cramfs padded with erased flash (0xff): gzip complains
# about the trailing bytes and still writes the whole image.
gzip -dc "$D/karotz-mtd2.bin" > "$W/mtd2.img" 2>/dev/null || true
python3 /fros/root-for-qemu.py "$W/mtd2.img" "$W/root"
mkfs.cramfs "$W/root" "$W/root.img" >/dev/null

# /usr: rebuilt from mtd6 when the dump has it, empty otherwise (linuxrc
# then fills it from the root, as on a factory-reset rabbit).
mkdir -p "$W/usr"
if [ -f "$D/karotz-mtd6.bin" ]; then
	python3 /fros/usr-from-yaffs.py "$D/karotz-mtd6.bin" "$W/usr"
	sed 's/^/usr: /' "$W/report.txt" >&2
fi
# Two inittabs in the dump without the OOB to order them: the factory one and
# "::once:/sbin/reboot" (2026-11-17, FR's Karotz). The latter reboots at once;
# without a file linuxrc copies the factory one, as it does on a new rabbit.
if [ "$(cat "$W/usr/etc/inittab" 2>/dev/null)" = "::once:/sbin/reboot" ]; then
	echo "usr: /etc/inittab only reboots; removed, linuxrc puts the factory one back" >&2
	rm -f "$W/usr/etc/inittab"
fi
mke2fs -q -t ext2 -L usr -d "$W/usr" "$W/usr.ext2" 256M

# The bootloader's own line (BIOS v1.16, mtd0) with the console moved from
# the S3C2440 UART (ttySAC0) to Versatile's (ttyAMA0).
APPEND="root=/dev/ram0 rw init=/linuxrc console=ttyAMA0,115200 mem=64M k_nbclick=0"

set -- qemu-system-arm -M versatilepb -m 64 -nographic -no-reboot \
	-kernel /fros/zImage -dtb /fros/versatile-pb.dtb \
	-initrd "$W/root.img" -append "$APPEND" \
	-drive "file=$W/usr.ext2,if=scsi,format=raw" \
	-nic user,model=smc91c111
if [ -n "${SECONDS_UP:-}" ]; then
	timeout "$SECONDS_UP" "$@" </dev/null || true
else
	exec "$@"
fi
