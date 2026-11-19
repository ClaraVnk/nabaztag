#!/bin/sh
# Put the measuring key together on a FAT32 key mounted at $1.
#   sh karotz/measure-key/make-key.sh /Volumes/KAROTZ   (from the nabaztag repo)
# autorun.sig is Violet's signed file; it is not kept here but fetched from
# LapinLibre at a pinned commit and checked by MD5 (the one LapinLibre's
# config.toml pins too).
set -eu
KEY=${1:?usage: make-key.sh <mounted FAT32 key>}
HERE=$(cd "$(dirname "$0")" && pwd)
LL=e26916806a8c7876559c61e07b3a1d586787a752
SIG_MD5=03076decb9e153b552d13196f5971ca4
curl -fsSL -o "$KEY/autorun.sig" \
  "https://raw.githubusercontent.com/sweetlilmre/LapinLibre/$LL/image/usbkey/autorun.sig"
got=$( (md5sum "$KEY/autorun.sig" 2>/dev/null || md5 -r "$KEY/autorun.sig") | cut -d' ' -f1)
[ "$got" = "$SIG_MD5" ] || { echo "autorun.sig: md5 $got, expected $SIG_MD5" >&2; exit 1; }
# LF line ends: the rabbit's shell would choke on CRLF.
tr -d '\r' < "$HERE/autorun" > "$KEY/autorun"
chmod 755 "$KEY/autorun" 2>/dev/null || true
rm -rf "$KEY/measure"
sync
echo "key ready: $(ls "$KEY")"
