#!/usr/bin/env bash
# Config mode closes by itself after 600 s with nobody on the page, and stays
# open while a phone is on it (config_timeout, Naboot 1.2.1.4), in the
# emulator.
#
#   tools/config-idle.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Runs scenarios/vm/config-idle.txt (PHONE=1: config-idle-phone.txt) through
# config-pages.sh's VM build; same environment (RUNTIME, STEPS, TAG,
# NO_BUILD). The control is a Naboot 1.2.1.3 image: its own access point to
# the end, every "expect-mode managed" fails.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
SC=config-idle.txt; [ -n "${PHONE:-}" ] && SC=config-idle-phone.txt
SCENARIO="$HERE/scenarios/vm/$SC" "$HERE/tools/config-pages.sh" "$@" > "$LOG" 2>&1 || true
# config-pages.sh checks every page too; this run asks for one at most, so
# only the scenario's own verdicts ("[ ... ms] ok|FAIL") count here.
grep -E "ms\] (ok|FAIL)|processor reset|phone: answer" "$LOG" | head -20 || true
if grep -q "processor reset" "$LOG" && ! grep -qE "ms\] FAIL" "$LOG" \
   && grep -q "ok   the rabbit is a station" "$LOG"; then echo "PASS   $SC"
else echo "FAIL   $SC"; exit 1; fi
