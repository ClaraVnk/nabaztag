#!/bin/sh
# What Violet's v1 firmware does when the network breaks, and what it feeds
# the OKI chip: the measurements behind ../v1-icsp/IMPROVEMENTS.md
# (2026-11-24). On a machine with podman (the dev VM, never HAOS), from
# firmware-arm/emulator:
#
#   tools/v1-study.sh [1.4b|2.0b|both] [OUTDIR] [sound]    (sound: that part only)
#
# Needs Violet's wait program served by tools/violet-vasm/violet_warren.py on
# V1_PORT (default 28090) and V1_PORT+1. Each scenario is one emulator run;
# the summary says what the firmware did, the logs in OUTDIR say when.
set -eu
cd "$(dirname "$0")/.."
WHICH=${1:-both}
OUT=${2:-$(mktemp -d)}
PORT=${V1_PORT:-28090}
SPORT=$((PORT + 1))
mkdir -p "$OUT"

podman build -q -t nabaztag-v1 -f v1/Containerfile .. >/dev/null
podman build -q -t violet-vasm -f tools/violet-vasm/Containerfile ../.. >/dev/null

# Violet's wait program, and one that plays an "Audi" tone at each rate.
podman run --rm violet-vasm Nabaztag_wait.vasm > "$OUT/wait.frame"
for rate in 8000 16000 32000; do
  python3 tools/violet-vasm/v1-audi.py $rate 1.5 "$OUT/test-$rate.audi" >/dev/null
  podman run --rm \
    -v "$(cd tools/violet-vasm && pwd)/sound-test.vasm":/out/net/violet/platform/vasm/sound-test.vasm:ro,Z \
    -v "$OUT/test-$rate.audi":/out/net/violet/platform/vasm/audio/test.audi:ro,Z \
    violet-vasm sound-test.vasm > "$OUT/sound-$rate.frame"
done

python3 tools/violet-vasm/violet_warren.py --port $PORT --frame "$OUT/wait.frame" --ears 5 10 --after 3 \
  > "$OUT/warren.log" 2>&1 &
WARREN=$!
trap 'kill $WARREN 2>/dev/null; kill ${SWARREN:-} 2>/dev/null' EXIT
sleep 1

run() {   # run NAME FW SECONDS ARGS...
  name=$1; fw=$2; secs=$3; shift 3
  if [ "$fw" = 1.4b ]; then
    FWV="-v $(cd ../v1-icsp/dumps && pwd):/d:ro,Z"; FWA="--hex /d/violet-v1-1.4b.hex --flash-chip 081"
  else
    FWV=""; FWA=""
  fi
  podman run --rm --network host $FWV nabaztag-v1 --warren 127.0.0.1 $FWA \
    --seconds "$secs" "$@" > "$OUT/$fw-$name.log" 2>&1 || true
}

summary() {  # the lines that tell the story; a poll is a connection the rabbit opens
  grep -E 'gw: (DNS|the server|nothing answers|DHCP ack|the rabbit opened)|card: (initialize|enable|link status|the access point)|watchdog|halted' "$1" \
    | sed -E 's/^\[ *([0-9]+) ms\]/\1 ms/; s/([0-9]+\.){3}[0-9]+/IP/g' \
    | awk '{ if (/rabbit opened/) { n++; if (n == 1) first = $1 } else { if (n) print "    polls: " n ", the first at " first; n = 0; print } } END { if (n) print "    polls: " n ", the first at " first }'
}

for fw in 1.4b 2.0b; do
  [ "${3:-}" != sound ] || break
  [ "$WHICH" = both ] || [ "$WHICH" = "$fw" ] || continue
  echo "== $fw: the server dies without a FIN at 40 s (--server-vanish)"
  run vanish $fw 160 --port80 $PORT --server-vanish 40 --trace-net; summary "$OUT/$fw-vanish.log"
  echo "== $fw: the server closes at 40 s and stays down (--server-close), nothing listens after"
  run close $fw 160 --port80 $PORT --server-close 40 --trace-net; summary "$OUT/$fw-close.log"
  echo "== $fw: the access point is gone from 30 s for 20 s (--link-down 30 20)"
  run linkdown $fw 160 --port80 $PORT --link-down 30 20 --trace-net; summary "$OUT/$fw-linkdown.log"
  echo "== $fw: the access point is gone for good from 30 s (--link-down 30 0)"
  run linkgone $fw 200 --port80 $PORT --link-down 30 0 --trace-net; summary "$OUT/$fw-linkgone.log"
  echo "== $fw: no DNS answer until 60 s (--dns-mute 60)"
  run dnsmute $fw 160 --port80 $PORT --dns-mute 60 --trace-net; summary "$OUT/$fw-dnsmute.log"
done

# The sound: the registers the firmware writes to the OKI for each rate.
kill $WARREN 2>/dev/null; wait $WARREN 2>/dev/null || true
for rate in 8000 16000 32000; do
  python3 tools/violet-vasm/violet_warren.py --port $SPORT --frame "$OUT/sound-$rate.frame" --after 99 \
    > "$OUT/swarren-$rate.log" 2>&1 &
  SWARREN=$!
  sleep 1
  for fw in 1.4b 2.0b; do
    [ "$WHICH" = both ] || [ "$WHICH" = "$fw" ] || continue
    echo "== $fw: an Audi sound at $rate Hz (header byte 8 = $(printf %02X $((rate >> 8))))"
    run sound-$rate $fw 45 --port80 $SPORT --trace-bus
    # after the boot jingle (the first second): the registers, then the data bursts
    grep -E 'oki: reg (35|33|29|31|01) ' "$OUT/$fw-sound-$rate.log" | awk '$2 + 0 > 1000' \
      | sed -E 's/^\[ *([0-9]+) ms\]/\1 ms/' | awk '/reg 01 </ { b++; bytes += $7; if (b == 1) first = $0; next } { if (b) print "    " b " bursts to reg 01, " bytes " bytes, the first: " first; b = 0; print } END { if (b) print "    " b " bursts to reg 01, " bytes " bytes, the first: " first }' | head -20
    echo "    FIFO status reads (reg 06): $(grep -c 'oki: reg 06 ' "$OUT/$fw-sound-$rate.log")"
  done
  kill $SWARREN 2>/dev/null; wait $SWARREN 2>/dev/null || true
done
echo "logs: $OUT"
