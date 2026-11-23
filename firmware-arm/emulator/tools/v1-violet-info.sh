#!/bin/sh
# Violet's wait program on the v1 emulator shows the sources it is sent on
# the belly: the same run with no source, the weather "sun" (1 0) and the
# weather "rain" (1 3), and the LED driver's outputs compared (2026-11-23:
# the Terrier fills the weather for a v1 on Violet's program). From
# firmware-arm/emulator on the dev VM, after the two builds of the RUNBOOK's
# "Violet's own v1 programs" (violet-vasm, nabaztag-v1):
#
#   tools/v1-violet-info.sh [seconds]
#
# PASS when the three runs light the belly differently.
set -eu
SECS=${1:-90}
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
HERE=$(cd "$(dirname "$0")" && pwd)
podman run --rm violet-vasm Nabaztag_wait.vasm > "$W/wait.frame"
port=28091
for case in none sun rain; do
  case $case in none) info="";; sun) info="--info 1 0";; rain) info="--info 1 3";; esac
  python3 "$HERE/violet-vasm/violet_warren.py" --port $port --frame "$W/wait.frame" --after 2 $info > "$W/$case.warren" 2>&1 &
  wp=$!
  sleep 1
  podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 $port --seconds "$SECS" > "$W/$case.log" 2>&1 || true
  kill $wp 2>/dev/null || true
  grep -a "leds" "$W/$case.log" | sed "s/.*leds/leds/" | sort | uniq -c | sort -rn > "$W/$case.leds"
  echo "== $case: $(grep -c "" "$W/$case.leds") distinct patterns, $(grep -ac "leds" "$W/$case.log") changes"
  head -4 "$W/$case.leds"
  port=$((port + 1))
done
if ! cmp -s "$W/none.leds" "$W/sun.leds" && ! cmp -s "$W/sun.leds" "$W/rain.leds"; then
  echo "PASS: the belly follows the weather sent"
else
  echo "FAIL: the belly does not change with the source"; exit 1
fi
