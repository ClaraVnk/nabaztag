#!/bin/sh
# The Terrier's wait program without Antoine Schmitt's clip (still.awk), on
# both firmwares, against the full Terrier frame as the witness: the frame
# size, the time from the program's download to the next poll, an order of
# the ears (L5 R10), a click with a message waiting (sd=0003) and a hand on
# an ear (sd=8xxx/9xxx). The clip only plays when "moves by itself" is on;
# none of these touches it, so both frames must behave the same, the still
# one sooner.
#
#   firmware-arm/emulator/tools/violet-vasm/still-proof.sh
#
# Why (2026-11-24): the clip is 26 KB of the 33.8 KB every v1 downloads at
# each boot, and the Terrier leaves "moves by itself" off by default.
set -eu
cd "$(dirname "$0")/../../../.."
OUT=$(mktemp -d)
PORT=${V1_PORT:-28083}
DUMPS=$(cd firmware-arm/v1-icsp/dumps && pwd)
WARREN=firmware-arm/emulator/tools/violet-vasm/violet_warren.py
fail() { echo "FAIL: $*"; echo "logs in $OUT"; exit 1; }

podman build -q -t violet-vasm -f firmware-arm/emulator/tools/violet-vasm/Containerfile . >/dev/null
podman build -q -t nabaztag-v1 -f firmware-arm/emulator/v1/Containerfile firmware-arm >/dev/null
podman run --rm violet-vasm Nabaztag_wait_terrier.vasm > "$OUT/terrier.frame"
podman run --rm violet-vasm Nabaztag_wait_terrier_still.vasm > "$OUT/still.frame"
full=$(wc -c < "$OUT/terrier.frame"); still=$(wc -c < "$OUT/still.frame")
echo "frames: terrier $full bytes, still $still bytes"
[ "$still" -lt 10000 ] || fail "the still frame is $still bytes"

emu() {
  fw=$1; shift
  if [ "$fw" = 1.4b ]; then
    podman run --rm --network host -v "$DUMPS:/d:ro,z" nabaztag-v1 --warren 127.0.0.1 \
      --port80 "$PORT" --hex /d/violet-v1-1.4b.hex --flash-chip 081 --trace-net "$@"
  else
    podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 "$PORT" --trace-net "$@"
  fi
}

run() {   # run NAME FW FRAME "WARREN ARGS" EMULATOR ARGS...
  name=$1; fw=$2; frame=$3; wargs=$4; shift 4
  # shellcheck disable=SC2086
  python3 "$WARREN" --port "$PORT" --frame "$OUT/$frame.frame" $wargs > "$OUT/$name-warren.log" 2>&1 &
  W=$!
  sleep 1
  emu "$fw" "$@" > "$OUT/$name.log" 2>&1 || true
  kill $W
  wait $W 2>/dev/null || true
}

polls() { grep 'the rabbit opened' "$1" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/'; }

for fw in 2.0b 1.4b; do
  for frame in terrier still; do
    n="$fw-$frame"
    run "$n-ears" "$fw" "$frame" "--ears 5 10 --after 3" --seconds 120
    grep -q 'watchdog' "$OUT/$n-ears.log" && fail "$n: the watchdog fired"
    p1=$(polls "$OUT/$n-ears.log" | sed -n 1p); p2=$(polls "$OUT/$n-ears.log" | sed -n 2p)
    [ -n "$p2" ] || fail "$n: no second poll after the program"
    l=$(grep 'ear 0: motor stopped' "$OUT/$n-ears.log" | tail -1 | sed -E 's/.*after ([0-9]+) ms.*/\1/')
    r=$(grep 'ear 1: motor stopped' "$OUT/$n-ears.log" | tail -1 | sed -E 's/.*after ([0-9]+) ms.*/\1/')
    echo "$n: program to next poll $((p2 - p1)) ms; order L5 R10: left ${l:-none} ms, right ${r:-none} ms"
    [ -n "$l" ] && [ -n "$r" ] || fail "$n: the ears did not take the order"

    run "$n-click" "$fw" "$frame" "--messages 1" --seconds 100 --press 60 0.3
    grep -q 'sd=0003' "$OUT/$n-click-warren.log" || fail "$n: a click with a message waiting sent no sd=0003"
    echo "$n: click -> sd=0003"

    run "$n-hand" "$fw" "$frame" "" --seconds 130 --hand 0 9 70
    sd=$(grep -oE 'sd=[89][0-9A-F]{3}' "$OUT/$n-hand-warren.log" | head -1)
    [ -n "$sd" ] || fail "$n: the hand was not reported"
    echo "$n: hand -> $sd"
  done
done
echo PASS
