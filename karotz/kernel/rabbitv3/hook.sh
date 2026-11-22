#!/bin/sh
# Hooks the Karotz's board file (mach-rabbitv3.c, beside this script) into a
# Linux 6.1 tree: machine 1912 back in mach-types (6.1 pruned it), its
# Kconfig entry before MINI2440's, its object in the Makefile. Step 4 of
# docs/TERRIER-OS-KERNEL.md. Usage: hook.sh <linux tree>
set -eu
L=${1:?linux tree}
D=$(dirname "$0")
cp "$D/mach-rabbitv3.c" "$L/arch/arm/mach-s3c/"
grep -q "MACH_RABBITV3" "$L/arch/arm/tools/mach-types" ||
	printf 'rabbitv3\t\tMACH_RABBITV3\t\tRABBITV3\t\t1912\n' >> "$L/arch/arm/tools/mach-types"
grep -q "MACH_RABBITV3" "$L/arch/arm/mach-s3c/Makefile.s3c24xx" ||
	printf 'obj-$(CONFIG_MACH_RABBITV3)\t\t+= mach-rabbitv3.o\n' >> "$L/arch/arm/mach-s3c/Makefile.s3c24xx"
K="$L/arch/arm/mach-s3c/Kconfig.s3c24xx"
grep -q "MACH_RABBITV3" "$K" || {
	awk '/^config MACH_MINI2440$/ {
		print "config MACH_RABBITV3"
		print "\tbool \"Karotz (RABBITV3)\""
		print "\tselect S3C_DEV_NAND"
		print "\tselect S3C_DEV_USB_HOST"
		print "\thelp"
		print "\t  The Karotz (Nabaztag v3), machine 1912 (Terrier OS)."
		print ""
	} { print }' "$K" > "$K.new" && mv "$K.new" "$K"
}
grep -q "config MACH_RABBITV3" "$K"
