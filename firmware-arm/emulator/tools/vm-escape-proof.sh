#!/bin/sh
# Verdict and proof for v1-icsp/VM-ESCAPE.md: can the program a Nabaztag v1
# downloads from its server escape Violet's VM and write or jump outside its
# sandbox? Measured, not argued, on both firmwares (1.4b of Lazare, 2.0b of
# Newbaztag's rabbit), PASS/FAIL.
#
#   firmware-arm/emulator/tools/vm-escape-proof.sh
#
# Three measurements:
#   1. static  - neither firmware has a TBLWT or an EECON access, so no native
#      code writes flash or the data EEPROM: a redirect has no gadget to reach.
#   2. dynamic - the hostile program (violet-vasm/escape.vasm) drives every VM
#      store and load at its largest index; --vm-audit records where each lands
#      and PASSES only if every one stays in the arena 0xA70-0xCFF.
#   3. control - a native stub (selfwrite_stub.py) DOES write a flash row on the
#      same emulator, so the silence in 2 is a measurement, not a blind model.
#
# Heavy (two podman builds, two emulator runs): on the dev VM, under
# flock ~/heavy.lock, ports 28140-28141.
set -eu
cd "$(dirname "$0")/../../.."                 # repo root
DIS=firmware-arm/v1-icsp/pic18dis.py
HEX14=firmware-arm/v1-icsp/dumps/violet-v1-1.4b.hex
HEX20=firmware-arm/vendor/newbaztag/firmware/original/nabaztag.hex
TOOLS=firmware-arm/emulator/tools
DUMPS=$(cd firmware-arm/v1-icsp/dumps && pwd)
PORT=${V1_PORT:-28140}
OUT=$(mktemp -d)
fail() { echo "FAIL: $*"; echo "logs in $OUT"; exit 1; }

echo "== 1. static: no flash/EEPROM-write opcode in either firmware"
for hx in "$HEX14" "$HEX20"; do
  dis=$(python3 "$DIS" dis "$hx")
  t=$(printf '%s' "$dis" | grep -c tblwt || true)
  e=$(printf '%s' "$dis" | grep -ciE 'eecon|0xFA6|0xFA7|0xFA8|0xFA9' || true)
  echo "   $(basename "$hx"): TBLWT=$t  EECON/EEDATA/EEADR=$e"
  { [ "$t" = 0 ] && [ "$e" = 0 ]; } || fail "$(basename "$hx") has a flash/EEPROM-write opcode"
done

echo "== build the assembler and the emulator"
podman build -q -t violet-vasm -f "$TOOLS/violet-vasm/Containerfile" . >/dev/null
podman build -q -t nabaztag-v1 -f firmware-arm/emulator/v1/Containerfile firmware-arm >/dev/null
podman run --rm violet-vasm escape.vasm > "$OUT/escape.frame"
echo "   hostile frame: $(wc -c < "$OUT/escape.frame") bytes"

emu() {                                       # emu FW LO HI
  if [ "$1" = 1.4b ]; then
    podman run --rm --network host -v "$DUMPS:/d:ro,z" nabaztag-v1 --warren 127.0.0.1 \
      --port80 "$PORT" --hex /d/violet-v1-1.4b.hex --flash-chip 081 --vm-audit "$2" "$3" --seconds 60
  else
    podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 "$PORT" \
      --vm-audit "$2" "$3" --seconds 60
  fi
}
run() {                                       # run FW LO HI
  python3 "$TOOLS/violet-vasm/violet_warren.py" --port "$PORT" --frame "$OUT/escape.frame" \
    > "$OUT/$1-warren.log" 2>&1 &
  W=$!; sleep 1
  emu "$1" "$2" "$3" > "$OUT/$1.log" 2>&1 || true
  kill "$W" 2>/dev/null || true; wait "$W" 2>/dev/null || true
}

echo "== 2. dynamic: the hostile program's stores, audited"
for spec in "2.0b 0x7A00 0x9080" "1.4b 0x7200 0x8C00"; do
  # shellcheck disable=SC2086
  set -- $spec
  run "$1" "$2" "$3"
  grep -q 'watchdog' "$OUT/$1.log" && fail "$1: the watchdog fired (the program never ran)"
  line=$(grep 'vm-audit:' "$OUT/$1.log" | tail -1)
  [ -n "$line" ] || fail "$1: no vm-audit line (did the program run? see $OUT/$1.log)"
  echo "   $1: ${line#*] }"
  stores=$(printf '%s' "$line" | sed -E 's/.*vm-audit: ([0-9]+) indirect.*/\1/')
  esc=$(printf '%s' "$line" | sed -E 's/.*arena 0xA70-0xCFF: ([0-9]+).*/\1/')
  { [ "$stores" -gt 0 ]; } 2>/dev/null || fail "$1: the program made no audited store"
  [ "$esc" = 0 ] || fail "$1: $esc store(s) left the arena"
  grep -q 'flash: write' "$OUT/$1.log" && fail "$1: the VM program wrote flash"
  grep -q 'eeprom: \[' "$OUT/$1.log" && fail "$1: the VM program wrote the data EEPROM"
done

echo "== 3. control: native code writes a flash row on the same emulator"
python3 "$TOOLS/selfwrite_stub.py" "$OUT/selfwrite.hex"
podman run --rm -v "$OUT:/o:z" nabaztag-v1 --hex /o/selfwrite.hex --seconds 0.01 \
  > "$OUT/control.log" 2>&1 || true
grep -q 'flash: write row 00100' "$OUT/control.log" ||
  { cat "$OUT/control.log"; fail "the native self-write control did not write flash"; }
echo "   control: $(grep 'flash: write' "$OUT/control.log" | tail -1 | sed -E 's/.*] //')"

echo PASS
rm -rf "$OUT"
