#!/bin/sh
# The built LapinLibre key as a disk image, for Raspberry Pi Imager or
# balenaEtcher: one MBR partition, FAT32, label LAPINLIBRE, the key's files
# at its root (LapinLibre's docs/install.md: "Copy the files, not the
# folder"). After writing it, the key shows up on the computer: edit
# lapinlibre.txt there (Wi-Fi, SSH key...), eject, plug it into the Karotz.
#
#   sh karotz/lapinlibre/make-img.sh [key dir] [out dir]
#   (defaults: ~/lapinlibre-work/usbkey, the build's output, and the current dir)
#
# Built in an Alpine container with mtools: no root, no loop device. Writes
# lapinlibre-<version>.img.xz and its .sha256.
set -eu
KEY=${1:-$HOME/lapinlibre-work/usbkey}
# A build whose tests failed leaves the key in the work tree only.
[ -n "${1:-}" ] || [ -d "$KEY" ] || KEY=$HOME/lapinlibre-work/ll/out/usbkey
OUT=${2:-$PWD}
[ -f "$KEY/autorun.sig" ] && [ -f "$KEY/lapinlibre.txt" ] || {
    echo "no LapinLibre key in $KEY (run karotz/lapinlibre/build.sh first)" >&2; exit 1; }
VERSION=$(sed -n 's/^BUILD_VERSION=//p' "$KEY/install.conf" 2>/dev/null | head -n 1)
VERSION=${VERSION:-$(date -u +%Y%m%d)}
# The files plus 32 MB, and never under 64 MB: FAT32's own floor.
MB=$(( $(du -sm "$KEY" | cut -f1) + 32 ))
[ "$MB" -ge 64 ] || MB=64
mkdir -p "$OUT"
podman run --rm -v "$KEY:/key:ro,Z" -v "$OUT:/out:Z" -e MB="$MB" -e V="$VERSION" \
    docker.io/library/alpine:3.22 sh -euc '
apk add -q mtools dosfstools sfdisk xz >/dev/null
img=/out/lapinlibre-$V.img
rm -f "$img" "$img.xz"
truncate -s "${MB}M" "$img"
# One partition from 1 MiB, type c (FAT32 LBA): what a stick formatted on a
# computer has, and what the Karotz mounts.
echo "2048,,c" | sfdisk -q "$img"
mkfs.vfat -F 32 -n LAPINLIBRE --offset 2048 "$img" $(( (MB - 1) * 1024 )) >/dev/null
# CRLF would break the rabbit shell: the files go as built.
mcopy -s -i "$img@@1M" /key/* ::/
mdir -i "$img@@1M" ::/ | tail -n +4
xz -T0 -9 "$img"
cd /out && sha256sum "lapinlibre-$V.img.xz" > "lapinlibre-$V.img.xz.sha256"
ls -l "lapinlibre-$V.img.xz"'
