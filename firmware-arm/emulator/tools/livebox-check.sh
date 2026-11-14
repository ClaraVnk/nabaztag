#!/usr/bin/env bash
# Issue #163: does Naboot's own bootloader join an access point set up the
# way a Livebox may be? One scenario per likely setting (scenarios/vm/
# livebox-*.txt), the first one a control that must pass.
#
# Runs inside the emulator image, with the repository mounted on /src:
#
#   podman build -t nabaztag-emulator -f firmware-arm/emulator/Containerfile .
#   podman run --rm -v "$PWD":/src:Z nabaztag-emulator \
#     bash /src/firmware-arm/emulator/tools/livebox-check.sh [naboot-1.2.0.1]
#
# It applies the release's step list to a fresh copy of nabgcc only to get
# the release's bootloader source, compiles that with tools/mtl_comp.py, and
# links a rabbit-vm with it: the scenarios then run the bytecode that does
# the scan, the association and the handshake on a real rabbit.
set -u
MODE=${1:-naboot-1.2.0.1}
SRC=/src/firmware-arm
WORK=$(mktemp -d)

cp -r "$SRC/vendor/nabgcc" "$WORK/nb"
cd "$SRC"
if ! NAB_SIM=1 NAB_BOOT_ANCHOR="$SRC/keys/boot_anchor.der" \
     python3 apply-mods.py --mode "$MODE" "$WORK/nb" > "$WORK/mods.log" 2>&1; then
  echo "apply-mods --mode $MODE failed:"; tail -5 "$WORK/mods.log"; exit 2
fi
if ! python3 -I tools/mtl_comp.py "$WORK/nb/mtl/boot/boot.0.0.0.13.mtl" \
     -o "$WORK/boot.bin" > "$WORK/comp.log" 2>&1; then
  echo "the $MODE bootloader does not compile:"; tail -3 "$WORK/comp.log"; exit 2
fi

cd /build/emulator
make -s NABGCC=/build/nabgcc BYTECODE="$WORK/boot.bin" vm > "$WORK/make.log" 2>&1 \
  || { echo "rabbit-vm with the $MODE bootloader does not link:"; tail -5 "$WORK/make.log"; exit 2; }
cp rabbit-vm "$WORK/rabbit-vm-boot"
cp "$SRC"/emulator/scenarios/vm/livebox-*.txt scenarios/vm/ 2>/dev/null

python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 \
  --server 127.0.0.1:8080/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 \
  --login rabbit --password secret --xmpp-password nabaztagpass \
  -o "$WORK/wpa2.eeprom" >/dev/null

echo "== $MODE bootloader against a Livebox's likely settings"
fail=0
for s in scenarios/vm/livebox-*.txt; do
  log="$WORK/$(basename "$s" .txt).log"
  if "$WORK/rabbit-vm-boot" "$s" --eeprom "$WORK/wpa2.eeprom" --warren 127.0.0.1 > "$log" 2>&1; then
    printf 'PASS  %-40s %s\n' "$(basename "$s")" "$(sed -n 's/^final state *//p' "$log")"
  else
    printf 'FAIL  %-40s %s\n' "$(basename "$s")" "$(sed -n 's/^final state *//p' "$log")"
    grep -a 'FAIL\|expected state\|status\|refus\|reject' "$log" | head -4 | sed 's/^/        /'
    fail=$((fail + 1))
  fi
done
echo "transcripts in $WORK"
exit $fail
