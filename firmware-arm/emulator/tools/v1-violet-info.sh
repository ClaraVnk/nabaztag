#!/bin/sh
# Violet's wait program on the v1 emulator shows the sources it is sent on
# the belly: the same run with no source, the weather "sun" (1 0), the
# weather "rain" (1 3), the air (7 5) and the mail (6 1), and the LED
# driver's outputs compared (2026-11-23:
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
for case in none sun rain air mail; do
  case $case in none) info="";; sun) info="--info 1 0";; rain) info="--info 1 3";;
    air) info="--info 7 5";; mail) info="--info 6 1";; esac
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
# The belly's outputs are 0-11 (12-13 the nose). With no source Violet's
# program still lights the belly in a default colour (@inforst), so a source
# shows as belly patterns the run without one never has.
vectors() { sed 's/.*leds dc: //' "$W/$1.leds" | cut -d' ' -f1-12 | sort -u; }
vectors none > "$W/none.vec"
new() { vectors "$1" | comm -23 - "$W/none.vec" | grep -c .; }
echo "belly patterns not seen without a source: sun $(new sun) rain $(new rain) air $(new air) mail $(new mail)"
if [ "$(new sun)" -gt 0 ] && [ "$(new rain)" -gt 0 ] && [ "$(new air)" -gt 0 ] \
   && [ "$(new mail)" -gt 0 ] && ! cmp -s "$W/sun.leds" "$W/rain.leds"; then
  echo "PASS: the belly shows each source sent"
else
  echo "FAIL: a source left the belly dark"; exit 1
fi
