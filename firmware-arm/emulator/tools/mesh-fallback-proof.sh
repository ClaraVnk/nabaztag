#!/usr/bin/env bash
# bss_fallback (2026-11-24): a network with two access points whose strongest
# node declines the rabbit (status 37) is joined at the second one.
#
#   tools/mesh-fallback-proof.sh <Nab.elf | boot.bc> [<control Nab.elf | boot.bc>]
#
# Runs scenarios/vm/mesh-decline-wpa2.txt under the bootloader VM, on the
# wpa2 configuration page: the image under test must reach RUN (PASS); the
# control -- a build without the step, naboot-1.2.1.4 -- must NOT (its
# failure is the proof that the scenario tests the step and not the AP).
# The transcript's `AP <bssid>: auth ok` / `declining` lines show which node
# each try went to. Same environment as diag-proof.sh (RUNTIME, STEPS, TAG, NO_BUILD);
# STEPS should come from tools/emu-steps.py for the mode under test.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$HERE/run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}
IN=${1:?usage: mesh-fallback-proof.sh <Nab.elf | boot.bc> [<control>]}
CONTROL=${2:-}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
lift() {   # <elf|bc> <out>
  case "$1" in
    *.bc) cp "$1" "$2" ;;
    *) python3 "$HERE/tools/bootbc.py" "$1" -o "$2" || exit 1 ;;
  esac
}
lift "$IN" "$WORK/boot.bc"
[ -n "$CONTROL" ] && lift "$CONTROL" "$WORK/control.bc"
if [ -z "${NO_BUILD:-}" ]; then
  echo "== building the emulator image (nabgcc mods: $STEPS)"
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . ) >/dev/null || exit 1
fi
python3 "$HERE/mkconf.py" --ssid Terrier --psk nabaztag --security wpa2 \
  --server 10.0.0.1:8080/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 \
  --rng-seed fixed -o "$WORK/conf.eeprom" >/dev/null
cp "$HERE/scenarios/vm/mesh-decline-wpa2.txt" "$WORK/scenario.txt"
run() {   # <boot.bc> <log>
  cp "$1" "$WORK/boot.bc.run"
  cp "$WORK/conf.eeprom" "$WORK/conf.eeprom.run"
  "$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
    "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc.run vm >/dev/null \
     && ./rabbit-vm /work/scenario.txt --eeprom /work/conf.eeprom.run" > "$2" 2>&1
}
ok=1
run "$WORK/boot.bc" "$WORK/fix.log"; rc=$?
echo "image under test: exit $rc, final $(sed -n 's/^final state *//p' "$WORK/fix.log" | tail -1)"
echo "       declined: $(grep -ac 'declining open-system auth' "$WORK/fix.log") time(s); auth tries: $(grep -ac 'AP .*: auth ok\|declining open-system' "$WORK/fix.log")"
if [ $rc = 0 ] && grep -aq 'AP 02:00:5e:30:00:02: auth ok' "$WORK/fix.log"; then
  echo "PASS   joined at the second access point after the first declined"
else echo "FAIL   never joined the second access point"; grep -a 'FAIL\|expected' "$WORK/fix.log" | head -3 | sed 's/^/        /'; ok=0; fi
if [ -n "$CONTROL" ]; then
  run "$WORK/control.bc" "$WORK/control.log"; rc=$?
  echo "control: exit $rc, final $(sed -n 's/^final state *//p' "$WORK/control.log" | tail -1), declined $(grep -ac 'declining open-system auth' "$WORK/control.log") time(s)"
  if [ $rc != 0 ] && ! grep -aq 'AP 02:00:5e:30:00:02: auth ok' "$WORK/control.log"; then
    echo "PASS   the control retried the declining node only and never joined"
  else echo "FAIL   the control joined too: the scenario does not test the step"; ok=0; fi
fi
cp "$WORK"/*.log "${LOGDIR:-/tmp}/" 2>/dev/null
[ $ok = 1 ] && echo "== mesh-fallback-proof: every check passed" || { echo "== mesh-fallback-proof: FAILED"; exit 1; }
