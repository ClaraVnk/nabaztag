#!/bin/sh
# FLASH-PLAN step 5 in QEMU: YAFFS written by 6.1, read by 2.6.32, and back,
# on a copy of a real Karotz's NAND (karotz-flash.py, Gengar's measure), so
# that /usr can be mounted read-write under 6.1 (it is read-only there until
# this holds, its /usr/etc in RAM). Three boots of the same image file, each
# an initramfs whose /init does its part and powers off:
#
#   A  6.1      mounts /usr (mtd6) read-write, notes what is there (a listing
#               and the sha of every file), writes /step5/by-61, unmounts,
#               mounts again and reads it back
#   B  2.6.32   mounts the same /usr, reads by-61, checks the listing of A,
#               writes /step5/by-2632
#   C  6.1      reads both files and checks the listing again
#
# PASS when every read matches and nothing of A's listing changed. The 2.6.32
# is the rabbit's own (Terrier OS's, zImage-2632), the 6.1 the flash kernel
# with the real NAND config (karotz-6.1-realnand.config).
#
#   karotz/kernel/qemu-mini2440/yaffs-roundtrip.sh <dir> <measure dir> <ramdisk tree>
#
# <dir> holds zImage (6.1) and zImage-2632; <ramdisk tree> an unpacked
# Terrier OS ramdisk (its busybox runs on both kernels). The image and the
# logs stay in <dir>.
set -eu
DIR=$(cd "${1:?a directory with zImage and zImage-2632}" && pwd)
M=$(cd "${2:?the measure/ directory from the key}" && pwd)
TREE=$(cd "${3:?an unpacked Terrier OS ramdisk}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
MK=$(cd "$HERE/../../kexec" && pwd)/mkcpio.py
CID=$(python3 "$HERE/karotz-flash.py" "$M" "$DIR/roundtrip-nand.bin" | tail -1)

cat > "$DIR/roundtrip-init" <<'EOF'
#!/bin/busybox sh
/bin/busybox --install -s /bin 2> /dev/null
export PATH=/bin
mount -t proc p /proc; mount -t sysfs s /sys; mount -t devtmpfs d /dev 2> /dev/null
# 2.6.32 has no devtmpfs: the node by hand (mtdblock is block major 31).
[ -b /dev/mtdblock6 ] || mknod /dev/mtdblock6 b 31 6
step=$(sed -n 's/.* step=\([ABCWR]\).*/\1/p' /proc/cmdline)
echo "RT-$step kernel $(uname -r)"
mkdir -p /mnt
# The listing of /usr as it was: names, sizes and the sha of every file,
# step5/ left out (what A, B and C write there is checked apart).
files() { (cd /mnt && find . -path ./step5 -prune -o -type f -print | sort |
           while read -r f; do echo "$f $(sha1sum < "$f" | cut -c1-40)"; done); }
listing() { files > /tmp/files; sed "s/^/RTF-$step /" /tmp/files; echo "RT-$step listing $(sha1sum < /tmp/files | cut -c1-40)"; }
t0=$(cut -d' ' -f1 /proc/uptime)
if mount -t yaffs /dev/mtdblock6 /mnt; then
    echo "RT-$step mounted read-write in $(echo "$(cut -d' ' -f1 /proc/uptime) $t0" | awk '{print $1 - $2}') s"
else
    echo "RT-$step MOUNT-FAILED"; poweroff -f; sleep 600
fi
listing
echo "RT-$step version $(cat /mnt/etc/conf/sys_version 2> /dev/null)"
case "$step" in
W)  mkdir -p /mnt/step5 && echo "written by $(uname -r)" > /mnt/step5/by-$(uname -r | cut -c1-3) && sync
    umount /mnt && mount -t yaffs /dev/mtdblock6 /mnt
    listing; echo "RT-W relisted" ;;
R)  ;;
A)  mkdir -p /mnt/step5 && echo "written by 6.1 $(uname -r)" > /mnt/step5/by-61 && sync
    umount /mnt && mount -t yaffs /dev/mtdblock6 /mnt
    echo "RT-A reread $(cat /mnt/step5/by-61)" ;;
B)  echo "RT-B read-61 $(cat /mnt/step5/by-61 2>&1)"
    echo "written by 2.6.32 $(uname -r)" > /mnt/step5/by-2632 && sync ;;
C)  echo "RT-C read-61 $(cat /mnt/step5/by-61 2>&1)"
    echo "RT-C read-2632 $(cat /mnt/step5/by-2632 2>&1)" ;;
esac
umount /mnt && echo "RT-$step unmounted"
dmesg | grep -i -E "yaffs|nand|ecc" | tail -6 | sed "s/^/RT-$step dmesg /"
echo "RT-$step END"
poweroff -f
EOF
python3 "$MK" "$DIR/roundtrip.cpio.gz" "$TREE" "$DIR/roundtrip-init" > /dev/null

run() {   # step kernel
    podman run --rm -e MINI2440_MACH=1912 -e MINI2440_NAND_CID=$CID -e QEMU_ARM920_PIPELINE=1 \
        -v "$DIR":/k:Z qemu-mini2440 sh -c "
      timeout 900 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
        -mtdblock /k/roundtrip-nand.bin -kernel /k/$2 -initrd /k/roundtrip.cpio.gz \
        -append 'console=ttySAC0,115200 rdinit=/init mem=65536K step=$1' 2>&1 | tr -d '\r'" \
        > "$DIR/roundtrip-$1.log" 2>&1 || true
    grep -a "^RT-$1" "$DIR/roundtrip-$1.log" || true
}
if [ -n "${SEQ:-}" ]; then
    # SEQ="W:zImage-2632 R:zImage-2632": the control and the culprit, each run
    # with its own log (roundtrip-<n><step>.log), the same image all along.
    i=0
    for pair in $SEQ; do
        i=$((i + 1)); st=${pair%%:*}; k=${pair#*:}
        run "$st" "$k"; mv "$DIR/roundtrip-$st.log" "$DIR/roundtrip-$i$st.log"
    done
    exit 0
fi
run A zImage
run B zImage-2632
run C zImage
L=$DIR/roundtrip
v() { grep -ah "^RT-$1 $2" "$L-$1.log" | head -1 | cut -d' ' -f3-; }
fail=0
[ "$(v A reread)" = "written by 6.1 $(v A kernel)" ] || { echo "FAIL: 6.1 did not read back its own file"; fail=1; }
case "$(v B read-61)" in "written by 6.1 "*) ;; *) echo "FAIL: 2.6.32 did not read 6.1's file"; fail=1 ;; esac
case "$(v C read-2632)" in "written by 2.6.32 "*) ;; *) echo "FAIL: 6.1 did not read 2.6.32's file"; fail=1 ;; esac
[ -n "$(v A listing)" ] && [ "$(v A listing)" = "$(v B listing)" ] && [ "$(v B listing)" = "$(v C listing)" ] \
    || { echo "FAIL: /usr's files changed between the kernels"; fail=1; }
for s in A B C; do grep -aq "^RT-$s unmounted" "$L-$s.log" || { echo "FAIL: step $s did not unmount"; fail=1; }; done
[ $fail = 0 ] && echo "PASS yaffs-roundtrip: 6.1 writes /usr, 2.6.32 reads it and writes, 6.1 reads it, nothing else changed" || exit 1
