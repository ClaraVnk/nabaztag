#!/bin/sh
# The Wi-Fi card probe (v1-icsp/wifi-probe.py) on the emulator: the 1.4b
# with the probe keeps the station identity the emulated card holds
# (prism.c: station 1.7.1) in its data EEPROM, 05 20 1F 01 01 07 at
# 0x000-0x005, then joins and polls like the dump as read (the witness, run
# first, writes no EEPROM). The emulator proves
# the probe's code, not the real card's answer: that one needs Lazare.
#
#   firmware-arm/emulator/tools/wifi-probe-proof.sh [OUT]
#
# Why (2026-11-24): WPA2 on the v1 waits on the card's station firmware
# version (IMPROVEMENTS.md section 6); the probe is written to a real PIC
# only after it passes here.
set -eu
cd "$(dirname "$0")/.."
OUT=${1:-$(mktemp -d)}
PORT=${V1_PORT:-28094}
SECS=${V1_SECS:-120}
mkdir -p "$OUT"
DUMPS=$(cd ../v1-icsp/dumps && pwd)

python3 ../v1-icsp/wifi-probe.py "$DUMPS/violet-v1-1.4b.hex" "$OUT/probe.hex"
podman build -q -t nabaztag-v1 -f v1/Containerfile .. >/dev/null
podman build -q -t violet-vasm -f tools/violet-vasm/Containerfile ../.. >/dev/null
podman run --rm violet-vasm Nabaztag_wait.vasm > "$OUT/wait.frame"

python3 tools/violet-vasm/violet_warren.py --port "$PORT" --frame "$OUT/wait.frame" \
  > "$OUT/warren.log" 2>&1 &
WARREN=$!
trap 'kill $WARREN 2>/dev/null' EXIT
sleep 1

run() {   # run NAME HEXDIR HEXFILE
  podman run --rm --network host -v "$2":/d:ro,Z nabaztag-v1 --warren 127.0.0.1 \
    --hex "/d/$3" --flash-chip 081 --port80 "$PORT" --seconds "$SECS" --trace-net \
    > "$OUT/$1.log" 2>&1 || true
}
python3 ../v1-icsp/wifi-probe.py "$DUMPS/violet-v1-1.4b.hex" "$OUT/probe-nic.hex" FD0B
run witness "$DUMPS" violet-v1-1.4b.hex
run probe "$OUT" probe.hex
run nic "$OUT" probe-nic.hex

fail=0
for n in witness probe nic; do
  polls=$(grep -c 'the rabbit opened' "$OUT/$n.log" || true)
  dogs=$(grep -c 'watchdog' "$OUT/$n.log" || true)
  ee=$(grep 'eeprom:' "$OUT/$n.log" | sed -E 's/^\[ *([0-9]+) ms\] eeprom: //' | tr '\n' ' ')
  echo "$n: polls=$polls watchdog=$dogs eeprom: ${ee:-none}"
  [ "$polls" -gt 0 ] || { echo "FAIL: $n never polled"; fail=1; }
  [ "$dogs" -eq 0 ] || { echo "FAIL: $n hit the watchdog"; fail=1; }
done
got=$(grep -oE 'eeprom: \[00[0-5]\] <- [0-9A-F]{2}' "$OUT/probe.log" | awk '{print $NF}' | tr -d '\n')
if [ "$got" = 05201F010107 ]; then
  echo "probe: station identity kept (05 20 1F 01 01 07: station 1.7.1)"
else
  echo "FAIL: the probe kept '$got', not 05201F010107"; fail=1
fi
# The card's own identity (FD0B), as prism.c sets it: 0x8013, 1.1.0.
got=$(grep -oE 'eeprom: \[00[0-5]\] <- [0-9A-F]{2}' "$OUT/nic.log" | awk '{print $NF}' | tr -d '\n')
if [ "$got" = 050B13010100 ]; then
  echo "nic: card identity kept (05 0B 13 01 01 00: component 0x8013, 1.1.0)"
else
  echo "FAIL: the FD0B probe kept '$got', not 050B13010100"; fail=1
fi
if grep -q 'eeprom:' "$OUT/witness.log"; then
  echo "FAIL: the witness wrote its EEPROM"; fail=1
fi
[ $fail -eq 0 ] && echo PASS || { echo "logs in $OUT"; exit 1; }
