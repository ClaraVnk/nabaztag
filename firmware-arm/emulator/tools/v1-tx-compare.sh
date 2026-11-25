#!/bin/sh
# Violet's 1.4b and our native firmware transmit side by side: the TX
# descriptor (status, tx_control at 0x0C, the 802.11 header, data_len at
# 0x2C, the 802.3 header at 0x2E) and the frame of their first DHCP
# discover. Lazare's native firmware associated, sent 39 frames and never
# received one on an open network (2026-11-25); the emulator takes any
# descriptor, so the difference is read here.
#
#   firmware-arm/emulator/tools/v1-tx-compare.sh
set -eu
cd "$(dirname "$0")/.."
NATIVE=$(cd ../v1-native && pwd)
OUT=$(mktemp -d)
podman build -q -t nabaztag-v1 -f v1/Containerfile .. >/dev/null
podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c "make clean >/dev/null && make" > "$OUT/build.log" 2>&1
mkdir -p "$OUT/fw" && cp "$NATIVE/out/18f6525/full.hex" "$OUT/fw/"
podman run --rm --network host -v "$(cd ../v1-icsp/dumps && pwd)":/d:ro,z nabaztag-v1 \
  --hex /d/violet-v1-1.4b.hex --flash-chip 081 --trace-net --trace-bus --seconds 8 > "$OUT/violet.log" 2>&1 || true
podman run --rm --network host -v "$OUT/fw":/img:z nabaztag-v1 \
  --hex /img/full.hex --pic 6525 --flash-chip 081 --flash /img/n.flash --trace-net --trace-bus --seconds 30 > "$OUT/native.log" 2>&1 || true
for who in violet native; do
  echo "== $who"
  grep -m1 -B1 'card: tx descriptor' "$OUT/$who.log" | sed -E 's/^\[ *[0-9]+ ms\] //' | cut -c1-260
  echo "   registers written besides commands and BAPs, and attribute writes:"
  grep -E 'card: (reg |attr)|COR' "$OUT/$who.log" | sed -E 's/^\[ *[0-9]+ ms\] //' | sort | uniq -c | head -12
  echo "   commands, in order (RIDs read and written, transmits counted apart):"
  grep -E 'card: command' "$OUT/$who.log" | grep -v '010B' | sed -E 's/^\[ *[0-9]+ ms\] card: command //' | head -24 | tr '\n' ' '; echo
done
echo "($OUT)"
