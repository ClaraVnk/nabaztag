#!/usr/bin/env bash
# A stopped USB host restarts the rabbit, in the emulator.
#
#   tools/usb-watch-proof.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Runs scenarios/vm/usb-host-stop.txt through config-pages.sh's VM build:
# no reset while the controller runs (12 s), then one with reason 5 within
# 9.5 to 10.5 s of the stop: the watch counts 25 rt2501_timer ticks, 200 ms
# each on the rabbit (main.c's 50 ms loop, every fourth) -- 5 s -- but 400 ms
# in vm_main.c, whose loop steps 100 ms. Same environment as config-pages.sh; STEPS
# defaults to naboot-1.2.1.0's (tools/emu-steps.py), which carries usb_watch.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
STEPS="${STEPS:-$(python3 "$HERE/tools/emu-steps.py" naboot-1.2.1.0)}" SCENARIO="$HERE/scenarios/vm/usb-host-stop.txt" "$HERE/tools/config-pages.sh" "$@" > "$LOG" 2>&1 || true
grep -E "USB host|processor reset|never asked" "$LOG" | head -6   # config-pages.sh's own page checks do not apply here
fail=0
grep -q "ok   the firmware never asked for a reset" "$LOG" && echo "PASS   no reset while the controller runs" ||
  { echo "FAIL   a reset before the controller stopped"; fail=1; }
t=$(grep -m1 "processor reset (reason 5" "$LOG" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/' || true)
if [ -n "$t" ] && [ "$t" -ge 21500 ] && [ "$t" -le 22500 ]; then echo "PASS   reason 5 at $t ms, $((t - 12000)) ms after the stop"
else echo "FAIL   no reset with reason 5 9.5 to 10.5 s after the stop (5 s on the rabbit) (${t:-none})"; fail=1; fi
exit $fail
