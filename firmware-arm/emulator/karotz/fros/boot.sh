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
# No radio here: FROS's Wi-Fi script gives way to the emulator's (and the
# owner's Wi-Fi password, in that script and in wpa.conf, stays out of the disk).
if [ -f "$W/usr/scripts/waitfornetwork.sh" ]; then
	cp /fros/waitfornetwork.sh "$W/usr/scripts/waitfornetwork.sh"
	chmod 755 "$W/usr/scripts/waitfornetwork.sh"
	rm -f "$W/usr/etc/conf/wpa.conf"
fi
# PLATFORM=1: Mindscape's platform is fake_platform.py on this side of QEMU
# (10.0.2.2 from the rabbit), the rabbit's voos.conf holds test credentials
# instead of the owner's, and startup.sh starts the controller, which FROS
# no longer starts. The platform's log is $W/platform.log, printed at the end.
if [ -n "${PLATFORM:-}" ]; then
	sed -i 's/^ip_server=.*/ip_server=10.0.2.2/' "$W/usr/etc/conf/karotz.conf"
	printf '[voos]\nid=0123456789abcdef\npwd=terrier-test-pwd\n' > "$W/usr/etc/conf/voos.conf"
	# The daemons the controller drives over D-Bus that FROS does not start.
	for d in ears voice multimedia; do
		echo "/usr/karotz/bin/immortaldog /var/run/karotz/$d.pid /usr/karotz/bin/$d-daemon" >> "$W/usr/scripts/startup.sh"
	done
	echo '/usr/karotz/bin/controller &' >> "$W/usr/scripts/startup.sh"
	python3 -u /fros/fake_platform.py > "$W/platform.log" 2>&1 &
	trap 'echo "== the platform:"; cat "$W/platform.log"' EXIT
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
	"$@"
fi
