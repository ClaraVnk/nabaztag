#!/bin/sh
# Terrier OS's update while the whole rabbit runs, then the boot after it.
# Built and run as in the Containerfile's header; prints LIVE-PASS/FAIL.
set -eu
W=/w
K=/ota/key
mkdir -p $W
rm -rf $W/*

echo "== the rabbit before: Terrier OS's root, its /usr installed, its settings, the client"
sh /ota/root.sh $K/rootfs.img.gz $W/root1.cramfs
U=$W/usr
mkdir -p $U
gunzip -c $K/usr.tar.gz | tar -xf - -C $U
C=$U/etc/conf
mkdir -p $C/terrier $C/ota-test $C/os-update
echo "[karotz]" > $C/karotz.conf
echo "lang=fr" > $C/voice.conf
printf 'network={\n\tssid="Terrier"\n\tpsk="nabaztag"\n}\n' > $C/wpa.conf
echo "an-older-build" > $C/lapinlibre_build_id
echo "v0.0.0-older" > $C/sys_version
# The client as Terrier OS runs it (S50terrier): its folder in /usr/etc/conf.
cp $U/karotz/terrier/terrier-karotz $U/karotz/terrier/run.sh $C/terrier/
head -c 32 /dev/urandom > $C/terrier/key
printf "SERVER=10.0.2.2\nIFACE=eth0\nEXTRA='--ok http://127.0.0.1'\n" > $C/terrier/settings
cp $C/terrier/key $C/ota-test/key.before
cp $C/terrier/settings $C/ota-test/settings.before
cp $C/terrier/terrier-karotz $C/ota-test/client.before
# The package where client 0.1.18 puts it: make-ota.py's files.
cp -a /ota/pkg/. $C/os-update/
cp /ota/phase.sh $C/ota-test/phase.sh
chmod 755 $C/ota-test/phase.sh
mke2fs -q -t ext2 -L usr -d $U $W/usr.ext2 400M

boot() { # initrd, seconds
    timeout "$2" qemu-system-arm -M versatilepb -m 64 -nographic -no-reboot \
        -kernel /ota/zImage -dtb /ota/versatile-pb.dtb -initrd "$1" \
        -append "root=/dev/ram0 rw init=/linuxrc console=ttyAMA0,115200 mem=64M k_nbclick=0 nandsim.id_bytes=0xec,0xda,0x10,0x95 nandsim.parts=2,24,40" \
        -drive "file=$W/usr.ext2,if=scsi,format=raw" -nic user,model=smc91c111 \
        < /dev/null 2>&1 | tr -d '\000\r' | tee -a $W/console.log | grep -E "^LIVE|ERROR|end$|no network" || true
}

echo "== boot 1: the whole system, then the install while it runs"
boot $W/root1.cramfs 1500

echo "== boot 2: the root filesystem the install wrote, the /usr it left"
debugfs -R "dump /etc/conf/ota-test/mtd2.bin $W/mtd2.bin" $W/usr.ext2 2>/dev/null
if [ ! -s $W/mtd2.bin ]; then
    echo "LIVE-FAIL no flash copied: the install never asked for its reboot"
    echo "== the console's last lines:"
    tail -40 $W/console.log
    exit 1
fi
gzip -dc $W/mtd2.bin > $W/root2.gz.raw 2>/dev/null || true
gzip -c $W/root2.gz.raw > $W/root2.img.gz
sh /ota/root.sh $W/root2.img.gz $W/root2.cramfs
boot $W/root2.cramfs 900
