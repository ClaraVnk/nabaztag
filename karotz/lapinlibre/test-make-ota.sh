#!/bin/sh
# make-ota.py against a released Terrier OS image, with a throwaway key
# (never the real one): the package is made and signed, --check accepts it,
# and refuses it once one byte of the kernel changed.
#
#   podman run --rm -v "$PWD":/w:ro,Z -v <terrier-os-*.img.xz>:/t.img.xz:ro,Z \
#       docker.io/library/alpine:3.22 sh /w/karotz/lapinlibre/test-make-ota.sh
set -eu
apk add -q python3 mtools > /dev/null
W=${W:-/w}
python3 -c "import os; open('/tmp/seed', 'wb').write(os.urandom(32))"
python3 - <<'PY'
import sys
sys.path.insert(0, "/w/firmware-arm/signing")
import ed25519
pub = ed25519.secret_to_public(open("/tmp/seed", "rb").read())
open("/tmp/pub.h", "w").write("static const uint8_t k[32] = {" + ", ".join("0x%02x" % b for b in pub) + "};")
PY
python3 "$W/karotz/lapinlibre/make-ota.py" /t.img.xz /tmp/out --priv /tmp/seed
P=$(ls -d /tmp/out/*.ota)
python3 "$W/karotz/lapinlibre/make-ota.py" --check "$P" --pub /tmp/pub.h
printf 'X' | dd of="$P/zImage" bs=1 seek=100 conv=notrunc 2> /dev/null
if python3 "$W/karotz/lapinlibre/make-ota.py" --check "$P" --pub /tmp/pub.h; then
    echo "FAIL: a changed kernel was accepted"; exit 1
fi
echo "PASS: made, signed, checked, and refused once changed"
