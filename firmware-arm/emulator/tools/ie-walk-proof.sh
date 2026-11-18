#!/usr/bin/env bash
# The element walks of ieee80211_input_mgt under ASan (review S-1, 2026-11-18).
#
#   tools/ie-walk-proof.sh [<Nab.elf> | <boot.bc>]
#
# Builds the emulator twice, under AddressSanitizer: with the default step
# list, which carries ie_walk_bounds, and with that step left out as the
# control. Every frame is delivered in a heap block of exactly its length
# (vap_poll), so a parser reading past a frame is a heap-buffer-overflow
# READ in the transcript. Two scenarios: scenarios/forged-beacon-elements.txt
# (./rabbit: a stranger's beacons with SSID, RATES, DSPARMS, vendor and
# RSNXE elements the frame does not hold, beside every beacon round while
# the rabbit scans) and scenarios/vm/probe-ssid-short.txt (./rabbit-vm in
# config mode: a probe request whose SSID element says 32 bytes in a 4-byte
# body). Config mode is the bootloader's, so the second needs a built
# image's bootloader (bootbc.py lifts it from Nab.elf, Nab.bin beside it);
# without one the second half is skipped.
#
# Expected: the control reports ASan READs; the fixed build reports none and
# reaches RUN. Exit status 0 then. Environment: RUNTIME (podman), TAG
# (nabaztag-emulator-asan), NO_BUILD to reuse the two images.
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator-asan}
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEPS=$(sed -n 's/^STEPS=//p' "$HERE/run.sh")
case ",$STEPS," in *,ie_walk_bounds,*) ;; *) echo "run.sh's step list lacks ie_walk_bounds" >&2; exit 2 ;; esac
CONTROL=$(printf '%s' "$STEPS" | sed 's/,ie_walk_bounds//')
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BOOT=""
if [ $# -ge 1 ]; then
  case "$1" in
    *.elf|*.ELF|*/Nab.elf) python3 "$HERE/tools/bootbc.py" "$1" -o "$WORK/boot.bc" ;;
    *) cp "$1" "$WORK/boot.bc" ;;
  esac
  BOOT=/work/boot.bc
fi

build() {  # tag steps
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$2" -t "$1" \
      -f firmware-arm/emulator/Containerfile . >/dev/null )
}
# The image compiles ./rabbit and ./rabbit-vm without sanitizers; rebuild
# them inside the container with SAN set, then run the two scenarios.
run() {  # tag label
  "$RUNTIME" run --rm -e ASAN_OPTIONS=detect_leaks=0 -v "$WORK:/work:Z" "$1" sh -c "
    make -s NABGCC=/build/nabgcc SAN='$SAN' clean all 2>&1 | grep -E 'error|Error'; test -x rabbit || { echo 'the sanitised build failed'; exit 3; }
    echo '-- forged beacons while scanning (./rabbit)'
    ./rabbit scenarios/forged-beacon-elements.txt > /tmp/b.log 2>&1; echo \"exit=\$?\"
    grep -E 'attacker: beacon|scan: \"For|ERROR: AddressSanitizer|#0 |final state|FAIL' /tmp/b.log | sed 's/^\[ *[0-9]* ms\] //' | sort | uniq -c | sort -rn | head -12
    : > /tmp/p.log
    if [ -n '$BOOT' ]; then
      echo '-- a short probe-request SSID in config mode (./rabbit-vm, the bootloader)'
      make -s NABGCC=/build/nabgcc BYTECODE=$BOOT SAN='$SAN' vm 2>&1 | grep -E 'error|Error'; test -x rabbit-vm || { echo 'the sanitised VM build failed'; exit 3; }
      ./rabbit-vm scenarios/vm/probe-ssid-short.txt --no-eeprom > /tmp/p.log 2>&1; echo \"exit=\$?\"
      grep -E 'attacker: probe|Received probe request|ERROR: AddressSanitizer|#0 |FAIL' /tmp/p.log | sed 's/^\[ *[0-9]* ms\] //' | sort | uniq -c | sort -rn | head -8
    else
      echo '-- (no bootloader given: the probe-request half is skipped)'
    fi
    a=\$(grep -c 'ERROR: AddressSanitizer' /tmp/b.log /tmp/p.log | awk -F: '{s+=\$2} END {print s}')
    echo \"asan-reports=\$a\"
  " | tee "/tmp/ie-walk-$2.log"
}

if [ -z "${NO_BUILD:-}" ]; then
  echo "== building $TAG (with ie_walk_bounds) and $TAG-control (without)"
  build "$TAG" "$STEPS"
  build "$TAG-control" "$CONTROL"
fi
echo "== CONTROL: without ie_walk_bounds (the over-reads must show)"
run "$TAG-control" control
echo "== FIXED: with ie_walk_bounds (none may show)"
run "$TAG" fixed

ctl=$(sed -n 's/^asan-reports=//p' /tmp/ie-walk-control.log)
fix=$(sed -n 's/^asan-reports=//p' /tmp/ie-walk-fixed.log)
echo "== ASan reports: control $ctl, fixed $fix"
[ "${ctl:-0}" -gt 0 ] && [ "${fix:-1}" -eq 0 ] && grep -q 'final state *RUN' /tmp/ie-walk-fixed.log
