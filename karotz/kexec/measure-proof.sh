#!/bin/sh
# The measure key's whole chain in QEMU before Gengar: Terrier OS's 2.6.32
# with kexec, on the rabbit's own NAND (karotz-flash.py from the measure
# key), runs autorun's own kexec line (the same --append) into 6.1 with the
# measure initramfs (measure-init). PASS when 6.1 mounts /usr read-only and
# the measure finishes. QEMU has no USB here: the key is "NOT mounted".
#
#   karotz/kexec/measure-proof.sh <dir> <ramdisk tree> <measure dir>
#
# <dir> holds zImage-2632 (with kexec), kexec and zImage (6.1), as for
# kexec-proof.sh. NEXT=2632 kexecs into zImage-2632 itself instead: the
# control autorun's kexec/go-2632 runs on the rabbit.
set -eu
DIR=$(cd "${1:?a directory with zImage-2632, kexec, zImage}" && pwd)
TREE=$(cd "${2:?an unpacked Terrier OS ramdisk}" && pwd)
M=$(cd "${3:?the measure/ directory from the key}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "${NEXT:-61}" = 2632 ]; then NEXTK=zImage-2632; WANT="Linux version 2.6.32"; else NEXTK=zImage; WANT="Linux version 6.1"; fi
CID=$(python3 "$HERE/../kernel/qemu-mini2440/karotz-flash.py" "$M" "$DIR/karotz-nand.bin" | tail -1)
python3 "$HERE/mkcpio.py" "$DIR/measure.cpio.gz" "$TREE" "$HERE/measure-init"
APPEND=$(sed -n 's/.*--append="\([^"]*\)".*/\1/p' "$HERE/autorun")
# The key's kexec/append.txt is EXTRA here.
APPEND=$(echo "$APPEND" | sed 's/ \$EXTRA$//')" ${EXTRA:-}"
cat > "$DIR/outer-init" <<EOI
#!/bin/busybox sh
/bin/busybox mount -t proc p /proc
/kexec -l /zImage61 --atags --initrd=/measure.cpio.gz --append="$APPEND"
/bin/busybox echo KEXEC-LOAD rc=\$?
/kexec -e
/bin/busybox echo KEXEC-FAILED
exec /bin/busybox sh
EOI
python3 "$HERE/mkcpio.py" "$DIR/outer.cpio.gz" "$TREE" "$DIR/outer-init" \
    kexec="$DIR/kexec" zImage61="$DIR/$NEXTK" measure.cpio.gz="$DIR/measure.cpio.gz"
LOG="$DIR/measure-proof.log"
podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=$CID -e QEMU_ARM920_PIPELINE=1 \
    -v "$DIR":/k:Z qemu-mini2440 sh -c "
  cp /k/karotz-nand.bin /tmp/nand.bin
  timeout 300 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage-2632 -initrd /k/outer.cpio.gz \
    -append 'console=ttySAC0,115200 rdinit=/init mem=65536K' 2>&1 | tr -d '\r'" > "$LOG" 2>&1 || true
rm -f "$DIR/karotz-nand.bin" "$DIR/measure.cpio.gz" "$DIR/outer.cpio.gz" "$DIR/outer-init"
grep -aE "^Linux version|KEXEC-|^MEASURE|Kernel panic" "$LOG" | cut -c1-150 | head -30
if [ "$(grep -ac "^Linux version" "$LOG")" -ge 2 ] && grep -aq "^$WANT" "$LOG" \
   && grep -aq "^MEASURE usr mounted ro" "$LOG" \
   && grep -aq "^MEASURE done" "$LOG"; then
  echo "PASS: 2.6.32 -> kexec -> ${NEXTK} measure, /usr read-only ($LOG)"
else
  echo "FAIL ($LOG)"; exit 1
fi
