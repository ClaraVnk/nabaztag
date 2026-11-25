#!/bin/sh
# The project's own v1 firmware against the Terrier's real code (not
# v1_native_warren.py): the poll answered by nabv1/v1native, frame 10
# offering v31, /vl/v1/fw serving it signed with this rabbit's key, the
# loader checking and installing it, v31 polling.
#
#   firmware-arm/emulator/tools/v1-native-terrier-proof.sh --terrier <checkout>
#
# <checkout> is the (private) Terrier; its tools/v1_warren.py serves the
# shipped handler on SQLite, `--native` sets the rabbit's key and board as
# `python3 -m terrier.v1native set-key` does in production. The key is the
# throwaway keys/test-rabbit.key, never a rabbit's.
#
# Why (2026-11-25): the loader was proven against a stand-in server (#255,
# #257); before Lazare gets this firmware, the Terrier's side must be the
# one the rabbit meets.
set -eu
cd "$(dirname "$0")/.."
[ "${1:-}" = --terrier ] || { echo "usage: $0 --terrier <Terrier checkout>"; exit 2; }
TERRIER=$(cd "$2" && pwd)
PORT=${V1_PORT:-28171}
OUT=$(mktemp -d)
NATIVE=$(cd ../v1-native && pwd)
fail() { echo "FAIL: $*"; echo "logs in $OUT"; exit 1; }
need() { grep -q -- "$2" "$1" || fail "$3 ($1)"; }

echo "== build"
podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c '
  make clean >/dev/null && make >/dev/null && make app image APP_VERSION=31 >/dev/null' \
  > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log"; fail "the firmware does not build"; }
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null

FW="$OUT/fw"; OTA="$OUT/ota/v1native"; mkdir -p "$FW" "$OTA"
cp "$NATIVE/out/18f6525/full.hex" "$FW/"
# The Terrier keeps the application without its signed head and signs it
# per rabbit when it serves it (terrier/v1native.py): the .img minus 64 bytes.
tail -c +65 "$NATIVE/out/18f6525/app-31.img" > "$OTA/app-25-31.bin"
cp "$NATIVE/keys/test-rabbit.key" "$OUT/key"

podman run -d --rm --network host --name "v1terrier-$PORT" \
  -v "$TERRIER":/t:ro,Z -v "$OUT":/w:Z -e OTA_DIR=/w/ota -w /t docker.io/library/python:3.13-slim \
  sh -c "pip install -q -r requirements.txt >/dev/null 2>&1; python3 -u tools/v1_warren.py --port $PORT --native 0019db00beef 25 /w/key" \
  > /dev/null
trap 'podman rm -f "v1terrier-$PORT" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 90); do podman logs "v1terrier-$PORT" 2>&1 | grep -q "serving the Terrier" && break; sleep 2; done

podman run --rm --network host -v "$FW":/img:Z nabaztag-v1-native \
  --hex /img/full.hex --pic 6525 --flash-chip 081 --flash /img/sf.bin --pic-flash /img/pic.bin \
  --warren 127.0.0.1 --port80 "$PORT" --seconds 70 > "$OUT/emu.log" 2>&1 || true
podman logs "v1terrier-$PORT" > "$OUT/terrier.log" 2>&1

L="$OUT/emu.log"; T="$OUT/terrier.log"
need "$L" 'uart: nabv1 native 30' "v30 did not start"
need "$T" 'p3.jsp?sn=0019DB00BEEF&v=30' "the Terrier never saw v30 poll"
need "$L" 'uart: update: v31 offered' "the Terrier's frame 10 was not taken"
need "$T" '/vl/v1/fw?sn=0019DB00BEEF' "the rabbit never asked the Terrier for the image"
need "$T" 'firmware 31 for board 25 served' "the Terrier did not serve v31"
need "$L" 'uart: L: copy B' "the loader did not accept the Terrier's signature"
need "$L" 'uart: L: installed' "the install did not complete"
need "$L" 'uart: nabv1 native 31' "v31 did not start"
need "$T" 'p3.jsp?sn=0019DB00BEEF&v=31' "v31 never polled the Terrier"
grep -q 'watchdog' "$L" && fail "the watchdog fired"
echo "PASS: v30 polled the Terrier, was offered v31, fetched it signed for its key, installed it; v31 polls ($OUT)"
