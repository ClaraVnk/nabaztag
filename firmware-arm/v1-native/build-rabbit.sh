#!/bin/sh
# The image a real v1 is written with: its Wi-Fi network and its server built
# in, and its own key in the EEPROM. A plain `make` builds neither (the proof's
# defaults): written to Lazare, such an image looks for no network and stays
# blue -- "main does not associate on hardware" (2026-11-26) was that, not the
# code: main built this way is byte for byte the image that works (2026-11-27).
#
#   sh firmware-arm/v1-native/build-rabbit.sh <ssid> <server> <rabbit key> [out.hex]
#
# On the dev VM's toolchain image (v1native-toolchain, Containerfile here) for
# the build, then mkimage.py on this machine with the key, which never leaves
# it. Then: ipecmd -E, ipecmd -M -Y -F<out.hex> (firmware-arm/v1-icsp).
set -eu
SSID=${1:?the rabbit\'s Wi-Fi network}
SERVER=${2:?the Terrier, e.g. terrier.cyberloutre.fr}
KEY=${3:?the rabbit\'s own key file}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${4:-$HERE/out/18f6525/full-rabbit.hex}
case "$SSID$SERVER" in *[\"\\\']*) echo "no quote or backslash in the network or the server" >&2; exit 2 ;; esac
cd "$HERE"
podman run --rm -v "$HERE:/src:Z" -w /src v1native-toolchain sh -c \
    "make clean >/dev/null && make loader app APP_DEFS='-DSSID=\\\"$SSID\\\" -DSERVER_NAME=\\\"$SERVER\\\" -DNO_EAR_DEMO' >/dev/null && make size"
python3 "$HERE/mkimage.py" full --loader out/18f6525/loader.hex --app out/18f6525/app-30.hex \
    --key "$KEY" --out "$OUT"
echo "$OUT"
