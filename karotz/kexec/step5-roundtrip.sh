#!/bin/sh
# FLASH-PLAN step 5 on the rabbit: YAFFS on /usr written by 6.1, read and
# written by 2.6.32, read again by 6.1, over SSH from a computer (the key is
# not needed; flash-kernel runs from a RAM folder, KEY=/var/fk). Run on
# Gengar 2026-11-26: PASS (FLASH-PLAN.md, step 5).
#
#   karotz/kexec/step5-roundtrip.sh <karotz address> backup <dir>   settings to <dir>, private
#   karotz/kexec/step5-roundtrip.sh <karotz address> write61        under 6.1: /usr rw a moment, /usr/step5 written
#   karotz/kexec/step5-roundtrip.sh <karotz address> kernel <zImage-6.1> <zImage-2632> <rootfs.img.gz> back|write
#                                   flash-kernel from RAM: 2.6.32 (back) or 6.1 (write) into mtd1, then reboot
#   karotz/kexec/step5-roundtrip.sh <karotz address> check          /usr/step5's sums, and a file by this kernel
#
# Sequence: backup, write61, kernel ... back, check (under 2.6.32), kernel ...
# write, check (under 6.1). Each kernel step reads mtd1 back before the
# reboot (flash-kernel); the 6.1 write checks mtd2 is the given ramdisk.
# Settings stay on the computer: never in a repository (adoption key).
set -eu
H=${1:?the Karotz address}; CMD=${2:?backup, write61, kernel or check}; shift 2
S="ssh -o ConnectTimeout=8 -o BatchMode=yes karotz@$H"
HERE=$(cd "$(dirname "$0")" && pwd)
case "$CMD" in
backup)
  D=${1:?a private folder}; mkdir -p "$D"; chmod 700 "$D"
  $S 'mkdir -p /tmp/flashusr && mount --bind /usr /tmp/flashusr; tar cf - -C /tmp/flashusr/etc conf' > "$D/flash-etc-conf.tar"
  $S 'tar cf - -C /usr/etc conf; umount /tmp/flashusr; rmdir /tmp/flashusr' > "$D/ram-etc-conf.tar"
  chmod 600 "$D"/*.tar; tar tf "$D/ram-etc-conf.tar" | grep -c "^conf/" ;;
write61)
  $S 'set -e; [ "$(uname -r | cut -c1-2)" = 6. ]; mount -o remount,rw /usr; mkdir -p /usr/step5
      head -c 200000 /dev/urandom > /usr/step5/blob.bin; echo "written by $(uname -r) $(date)" > /usr/step5/by-61.txt
      cd /usr/step5 && sha1sum blob.bin by-61.txt > sums.sha1; sync; echo 3 > /proc/sys/vm/drop_caches
      sha1sum -c sums.sha1; mount -o remount,ro /usr; echo 3 > /proc/sys/vm/drop_caches; sha1sum -c sums.sha1' ;;
kernel)
  K61=${1:?zImage-6.1}; K32=${2:?zImage-2632}; RD=${3:?rootfs.img.gz}; WHAT=${4:?back or write}
  $S 'rm -rf /var/fk; mkdir -p /var/fk/flash'
  $S 'cat > /var/fk/flash/zImage-6.1' < "$K61"; $S 'cat > /var/fk/flash/zImage-2632' < "$K32"
  $S 'cat > /var/fk/rootfs.img.gz' < "$RD"; $S 'cat > /var/fk/flash-kernel' < "$HERE/flash-kernel"
  $S "cd /var/fk/flash && md5sum zImage-6.1 zImage-2632 > MD5SUMS && touch go-$WHAT && KEY=/var/fk FLASH_NO_REBOOT=1 sh /var/fk/flash-kernel; r=\$?; tail -4 log.txt; [ \$r = 0 ] && grep -q DONE log.txt && sync && reboot" ;;
check)
  $S 'cd /usr/step5 && sha1sum -c sums.sha1; uname -r
      if [ "$(uname -r | cut -c1-2)" != 6. ]; then echo "written by $(uname -r) $(date)" > by-2632.txt; sha1sum by-2632.txt >> sums.sha1; sync; fi' ;;
*) echo "backup, write61, kernel or check"; exit 2 ;;
esac
