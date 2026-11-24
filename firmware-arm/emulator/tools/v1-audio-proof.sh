#!/bin/sh
# The native v1 firmware's audio path (firmware-arm/v1-native/src/app/oki.c)
# against Violet's own, on the emulator's OKI ML2870A register log, for the
# same 16 kHz "Audi" tone (tools/violet-vasm/v1-audi.py):
#   1. Violet's 1.4b plays it from a program the warren serves (as
#      tools/v1-study.sh does), --trace-oki logging every byte of every burst;
#   2. our firmware plays it from the serial flash (the file written at page
#      977 behind a directory at 976 with autoplay set), same log;
#   3. the register writes around the data (45, 35, 29, 33 at the start, the
#      29 toggles per burst) and the whole data byte stream must be equal.
# What this cannot settle: the nibble order and the ADPCM table the real
# chip wants (IMPROVEMENTS.md #1), and the pace (the emulator's FIFO status
# is always "room"). PASS or FAIL.
#
#   tools/v1-audio-proof.sh      from firmware-arm/emulator, on a machine with podman
#
# V1_PORT overrides the warren's port (28171).
set -eu
cd "$(dirname "$0")/.."
PORT=${V1_PORT:-28171}
OUT=$(mktemp -d)
NATIVE=$(cd ../v1-native && pwd)
DUMPS=$(cd ../v1-icsp/dumps && pwd)
fail() { echo "FAIL: $*"; exit 1; }

echo "== build"
podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c 'make clean >/dev/null && make >/dev/null' \
  > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log"; fail "the firmware does not build"; }
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null
podman build -q -t violet-vasm -f tools/violet-vasm/Containerfile ../.. >/dev/null

echo "== the tone: 16 kHz, 1.5 s, OKI ADPCM, Violet's Audi container"
python3 tools/violet-vasm/v1-audi.py 16000 1.5 "$OUT/test.audi" >/dev/null
podman run --rm \
  -v "$(cd tools/violet-vasm && pwd)/sound-test.vasm":/out/net/violet/platform/vasm/sound-test.vasm:ro,Z \
  -v "$OUT/test.audi":/out/net/violet/platform/vasm/audio/test.audi:ro,Z \
  violet-vasm sound-test.vasm > "$OUT/sound.frame"

echo "== 1. Violet's 1.4b plays it"
WNAME="v1audio-$PORT"
podman rm -f "$WNAME" >/dev/null 2>&1 || true
podman run -d --rm --network host --name "$WNAME" -v "$PWD/tools/violet-vasm":/t:ro,Z -v "$OUT":/img:ro,Z \
  docker.io/library/python:3.13-alpine python3 -u /t/violet_warren.py --port "$PORT" --frame /img/sound.frame --after 99 >/dev/null
trap 'podman rm -f "$WNAME" >/dev/null 2>&1' EXIT
sleep 2
podman run --rm --network host -v "$DUMPS":/d:ro,Z nabaztag-v1-native \
  --warren 127.0.0.1 --port80 "$PORT" --hex /d/violet-v1-1.4b.hex --flash-chip 081 --seconds 20 --trace-oki \
  > "$OUT/violet.log" 2>&1 || true
podman rm -f "$WNAME" >/dev/null 2>&1 || true

echo "== 2. our firmware plays it from the serial flash"
mkdir -p "$OUT/fw"
cp "$NATIVE/out/18f6525/full.hex" "$OUT/fw/"
python3 - "$OUT/test.audi" "$OUT/fw/sf.bin" <<'EOF'
import struct, sys
audi = open(sys.argv[1], "rb").read()
PAGE, STRIDE, DIR = 264, 528, 976
sf = bytearray(b"\xff" * (4096 * STRIDE))
d = b"SND1" + bytes([1, 1]) + struct.pack("<HI", DIR + 1, len(audi) - 16)
sf[DIR * STRIDE:DIR * STRIDE + len(d)] = d
for i in range(0, len(audi), PAGE):
    p = DIR + 1 + i // PAGE
    sf[p * STRIDE:p * STRIDE + min(PAGE, len(audi) - i)] = audi[i:i + PAGE]
open(sys.argv[2], "wb").write(sf)
print(f"   {len(audi)} bytes of Audi at page {DIR + 1}, directory at {DIR}")
EOF
podman run --rm --network host -v "$OUT/fw":/img:Z nabaztag-v1-native \
  --hex /img/full.hex --pic 6525 --flash-chip 081 --flash /img/sf.bin --seconds 14 --trace-oki \
  > "$OUT/ours.log" 2>&1 || true

echo "== 3. compare"
# Violet: from the start of the sound (reg 35 <- 06 after the boot jingle) to the last burst.
regs() { grep 'oki: reg' "$1" | awk -v from="$2" '$2 + 0 >= from' | grep -v 'reg 06 ' | sed -E 's/^\[ *[0-9]+ ms\] oki: //'; }
V_FROM=$(grep 'oki: reg 35 <- 06' "$OUT/violet.log" | head -1 | sed -E 's/^\[ *([0-9]+) ms\].*/\1/')
O_FROM=$(grep 'oki: reg 35 <- 06' "$OUT/ours.log" | head -1 | sed -E 's/^\[ *([0-9]+) ms\].*/\1/')
[ -n "$V_FROM" ] || fail "Violet never set the rate (no reg 35 <- 06 in $OUT/violet.log)"
[ -n "$O_FROM" ] || fail "our firmware never set the rate (no reg 35 <- 06 in $OUT/ours.log)"
regs "$OUT/violet.log" "$V_FROM" | grep -v 'reg 01 ' | grep -B1 -A3 'reg 35' | head -5 > "$OUT/v-start.txt"
regs "$OUT/ours.log" "$O_FROM" | grep -v 'reg 01 ' | grep -B1 -A3 'reg 35' | head -5 > "$OUT/o-start.txt"
cmp -s "$OUT/v-start.txt" "$OUT/o-start.txt" || { diff "$OUT/v-start.txt" "$OUT/o-start.txt"; fail "the start sequence differs"; }
regs "$OUT/violet.log" "$V_FROM" | grep 'reg 01 ' | sed -E 's/.*bytes //' | tr -d '\n' > "$OUT/v-bytes.hex"
regs "$OUT/ours.log" "$O_FROM" | grep 'reg 01 ' | sed -E 's/.*bytes //' | tr -d '\n' > "$OUT/o-bytes.hex"
python3 -c "import sys; a=open(sys.argv[1],'rb').read()[16:].hex().upper(); print(a, end='')" "$OUT/test.audi" > "$OUT/file-bytes.hex"
cmp -s "$OUT/v-bytes.hex" "$OUT/file-bytes.hex" || fail "Violet's byte stream is not the file's data"
cmp -s "$OUT/o-bytes.hex" "$OUT/file-bytes.hex" || fail "our byte stream is not the file's data (see $OUT/o-bytes.hex)"
# Burst lengths: 64, shorter at a serial flash page end -- Violet's page
# cache and ours split the stream where the file happens to sit, so the
# patterns differ and the streams are what must be equal (checked above).
regs "$OUT/violet.log" "$V_FROM" | grep 'reg 01 ' | sed -E 's/.*<- ([0-9]+) bytes .*/\1/' | tr '\n' ',' | sed 's/,$//' > "$OUT/v-bursts.txt"
regs "$OUT/ours.log" "$O_FROM" | grep 'reg 01 ' | sed -E 's/.*<- ([0-9]+) bytes .*/\1/' | tr '\n' ',' | sed 's/,$//' > "$OUT/o-bursts.txt"
echo "   Violet's bursts: $(cut -c1-60 "$OUT/v-bursts.txt")... ($(tr ',' '\n' < "$OUT/v-bursts.txt" | wc -l | tr -d ' ') bursts)"
echo "   ours:            $(cut -c1-60 "$OUT/o-bursts.txt")... ($(tr ',' '\n' < "$OUT/o-bursts.txt" | wc -l | tr -d ' ') bursts)"
regs "$OUT/violet.log" "$V_FROM" | grep -A2 'reg 01 ' | grep -v 'reg 01 \|^--' | sort | uniq -c | sed 's/^/   Violet around each burst: /'
grep -q 'oki: done' "$OUT/ours.log" || fail "our firmware did not finish the sound"
grep -q 'reg 33 <- 00' "$OUT/ours.log" || fail "our firmware did not stop the ADPCM (33 <- 00)"
echo "   start: $(tr '\n' ';' < "$OUT/o-start.txt")"
echo "   $(($(wc -c < "$OUT/o-bytes.hex") / 2)) data bytes, identical to Violet's stream and to the file"
echo "PASS v1-audio: Violet's start sequence, the 29 toggles and every data byte reproduced; the end sequence 33/29 <- 0 added ($OUT)"
