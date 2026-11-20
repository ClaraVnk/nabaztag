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
# lapinlibre-<version>.img.xz, its .sha256, and lapinlibre.rpi-imager.json:
# a Raspberry Pi Imager repository listing that one image, so the key shows
# up by name in Imager (App options > Content repository, or
# `rpi-imager --repo <url of the json>`), downloaded and checked there.
# init_format cloudinit: Imager's OS customisation writes network-config and
# user-data on the key, and our LapinLibre's applysettings.sh takes the Wi-Fi
# network (in clear: WPA3 too), the name and the SSH key from them when
# lapinlibre.txt leaves those empty. The image is expected at LL_URL
# (default: the nabaztag release lapinlibre-<version>).
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
URL=${LL_URL:-https://github.com/ClaraVnk/nabaztag/releases/download/lapinlibre-$VERSION}
podman run --rm -v "$KEY:/key:ro,Z" -v "$OUT:/out:Z" -e MB="$MB" -e V="$VERSION" -e URL="$URL" \
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
ext_size=$(stat -c %s "$img"); ext_sha=$(sha256sum "$img" | cut -d" " -f1)
xz -T0 -9 "$img"
cd /out && sha256sum "lapinlibre-$V.img.xz" > "lapinlibre-$V.img.xz.sha256"
cat > lapinlibre.rpi-imager.json <<JSON
{
  "os_list": [
    {
      "name": "LapinLibre $V (Karotz)",
      "description": "USB key for a Karotz (Nabaztag v3), not for a Raspberry Pi. Set the Wi-Fi in the OS customisation (or in lapinlibre.txt on the key), then plug the key into the Karotz.",
      "url": "$URL/lapinlibre-$V.img.xz",
      "init_format": "cloudinit",
      "release_date": "$(date -u +%Y-%m-%d)",
      "extract_size": $ext_size,
      "extract_sha256": "$ext_sha",
      "image_download_size": $(stat -c %s "lapinlibre-$V.img.xz"),
      "image_download_sha256": "$(cut -d" " -f1 "lapinlibre-$V.img.xz.sha256")"
    }
  ]
}
JSON
ls -l "lapinlibre-$V.img.xz" lapinlibre.rpi-imager.json'
