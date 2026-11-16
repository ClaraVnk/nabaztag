#!/bin/sh
# The Karotz (Nabaztag v3) in emulation, on a machine with podman (the dev VM).
#
#   tools/karotz-proof.sh                       the released client starts on the
#                                               simulated Karotz and asks its
#                                               server for a key exchange
#   tools/karotz-proof.sh --terrier <checkout>  the Terrier's own scenario
#                                               (karotz/proof/run.sh): adoption,
#                                               every verb, button, tag, update
#
# Run from firmware-arm/emulator. The simulated Karotz is karotz/: OpenKarotz's
# real CGI scripts under busybox httpd, the hardware recorded, the client (built
# for the rabbit's ARM920T) under qemu-arm. RELEASE overrides karotz-0.1.5.
set -eu
cd "$(dirname "$0")/.."
HERE=$(pwd)
if [ "${1:-}" = --terrier ]; then
  NABAZTAG_KAROTZ="$HERE/karotz" exec sh "$2/karotz/proof/run.sh"
fi
RELEASE=${RELEASE:-karotz-0.1.5}
MAC=0019dbdeadbe
OUT=$(mktemp -d)
NET=karotz-emu
fail() { echo "FAIL: $*"; exit 1; }
cleanup() { podman rm -f karotz-emu-sim karotz-emu-cap >/dev/null 2>&1 || true; podman network rm "$NET" >/dev/null 2>&1 || true; }
cleanup
trap cleanup EXIT

for f in terrier-karotz run.sh SHA256SUMS; do
  curl -fsSL -o "$OUT/$f" "https://github.com/ClaraVnk/nabaztag/releases/download/$RELEASE/$f"
done
(cd "$OUT" && grep -E ' (terrier-karotz|run\.sh)$' SHA256SUMS | sha256sum -c -) || fail "checksums"
chmod 755 "$OUT/terrier-karotz"

podman build -q -t nabaztag-karotz karotz >/dev/null
podman network create "$NET" >/dev/null
podman run -d --name karotz-emu-cap --network "$NET" -v "$HERE/v1/capture.py:/capture.py:ro,Z" \
  docker.io/library/python:3.13-alpine python3 /capture.py 8080 0.0.0.0 >/dev/null
podman run -d --name karotz-emu-sim --network "$NET" -e TERRIER=karotz-emu-cap -e TERRIER_HTTP=8080 \
  -e KAROTZ_MAC=$MAC -v "$OUT:/proof:z" nabaztag-karotz >/dev/null
sleep 40
podman logs karotz-emu-cap > "$OUT/capture.log" 2>&1 || true
cat "$OUT/state/client.log" 2>/dev/null | head -20; cat "$OUT/capture.log"
grep -q "terrier-karotz .*, rabbit $MAC" "$OUT/state/client.log" || fail "the client did not start"
grep -q "kx.jsp.*$MAC\|$MAC.*kx.jsp" "$OUT/capture.log" || fail "no key exchange asked"
echo "PROOF karotz: the $RELEASE client runs on the simulated Karotz and asks for its key ($OUT)"
