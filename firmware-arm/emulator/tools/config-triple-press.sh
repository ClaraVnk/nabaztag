#!/usr/bin/env bash
# Three presses of the head open config mode, in the emulator.
#
#   tools/config-triple-press.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Runs scenarios/vm/config-triple-press.txt through config-pages.sh's VM
# build: a rabbit with no network it knows, three presses at 20 s, then the
# radio must be in master mode and the setup page must answer. Same
# environment as config-pages.sh: RUNTIME, STEPS (add triple_press), TAG,
# NO_BUILD. The control is a build without triple_press: still managed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
SCENARIO="$HERE/scenarios/vm/config-triple-press.txt" "$HERE/tools/config-pages.sh" "$@" > "$LOG" 2>&1 || true
grep -E "button|mode|phone: GET|phone: answer|expect" "$LOG" | head -20
fail=0
if grep -q "expect-mode master: ok\|mode master.*ok\|PASS.*expect-mode master" "$LOG"; then echo "PASS   config mode after three presses"
else echo "FAIL   not in config mode after three presses"; fail=1; fi
if grep -q "phone: answer \"HTTP/1.[01] 200" "$LOG"; then echo "PASS   the setup page answers"
else echo "FAIL   the setup page did not answer"; fail=1; fi
exit $fail
