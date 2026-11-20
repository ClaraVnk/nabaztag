#!/usr/bin/env bash
# Config mode as a captive portal, in the emulator.
#
#   tools/config-captive.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Runs scenarios/vm/config-captive.txt through config-pages.sh's VM build
# and checks the log: both names a phone checks resolve to 192.168.0.1
# without echoing the EDNS record, and the phone's probe page gets the
# setup page (200), which is what makes the phone open it by itself.
# Same environment as config-pages.sh: RUNTIME, STEPS, TAG, NO_BUILD.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
SCENARIO="$HERE/scenarios/vm/config-captive.txt" "$HERE/tools/config-pages.sh" "$@" > "$LOG" 2>&1 || true
grep -E "phone: DNS|phone: GET|phone: answer" "$LOG" | head -20   # config-pages.sh's own page checks do not apply here
fail=0
n=$(grep -c "phone: DNS answer 192.168.0.1 (ttl 60, additional 0)" "$LOG" || true)
if [ "$n" -eq 2 ]; then echo "PASS   both names resolve to the rabbit, no EDNS echoed"
else echo "FAIL   $n of 2 names resolved to the rabbit"; fail=1; fi
if grep -q "phone: answer \"HTTP/1.[01] 200" "$LOG"; then echo "PASS   the phone's probe page gets the setup page"
else echo "FAIL   the probe page did not get the setup page"; fail=1; fi
exit $fail
