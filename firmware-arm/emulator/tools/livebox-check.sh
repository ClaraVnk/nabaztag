#!/usr/bin/env bash
# Issue #163: does a rabbit join an access point set up the way a Livebox
# may be? One scenario per likely setting (scenarios/vm/livebox-*.txt), the
# first one a control that must pass.
#
# Runs inside the emulator image built with the Wi-Fi steps of run.sh (they
# carry beacon_ie_bounds, the parser a crowded beacon walks through), with
# the repository mounted on /src:
#
#   STEPS=$(sed -n 's/^STEPS=//p' firmware-arm/emulator/run.sh)
#   podman build --build-arg MOD_STEPS="$STEPS" -t nabaztag-emulator \
#     -f firmware-arm/emulator/Containerfile .
#   podman run --rm -v "$PWD":/src:Z nabaztag-emulator \
#     bash /src/firmware-arm/emulator/tools/livebox-check.sh
#
# The radio, the 802.11 parser and the handshake are the firmware's C, as on
# the rabbit; the bytecode driving them is the runtime's (.hybrid), whose
# wifiAuth is Violet's as in the bootloader. A WPA3 case is only meaningful
# under the bootloader VM (see wpa3-sae.txt); the transition one here
# exercises WPA2 against a mixed AP, which is what a WPA2-configured rabbit
# does with it.
set -u
WORK=$(mktemp -d)
cd /build/emulator
cp /src/firmware-arm/emulator/scenarios/vm/livebox-*.txt scenarios/vm/ 2>/dev/null
python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 \
  --server 127.0.0.1:8080/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 \
  --login rabbit --password secret --xmpp-password nabaztagpass \
  -o "$WORK/wpa2.eeprom" >/dev/null

echo "== a rabbit against a Livebox's likely settings"
fail=0
for s in scenarios/vm/livebox-*.txt; do
  log="$WORK/$(basename "$s" .txt).log"
  if ./rabbit-vm "$s" --eeprom "$WORK/wpa2.eeprom" --warren 127.0.0.1 > "$log" 2>&1; then
    printf 'PASS  %-40s %s\n' "$(basename "$s")" "$(sed -n 's/^final state *//p' "$log")"
  else
    printf 'FAIL  %-40s %s\n' "$(basename "$s")" "$(sed -n 's/^final state *//p' "$log")"
    grep -a 'FAIL\|expected state\|status\|refus\|reject\|OPnetAuth' "$log" | head -5 | sed 's/^/        /'
    fail=$((fail + 1))
  fi
done
echo "transcripts in $WORK"
exit $fail
