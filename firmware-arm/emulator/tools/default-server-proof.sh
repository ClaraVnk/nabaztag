#!/usr/bin/env bash
# Where does a reset send the rabbit? (factory_server, 2026-11-17)
#
#   tools/default-server-proof.sh <Nab.elf> [<Nab.elf> ...]
#
# For each image: lift its bootloader (bootbc.py), boot it in the VM on an
# empty config page (scenarios/vm/blank-conf.txt), and print the server URL
# confInit wrote from conf0 -- the bytes a reset from the config page writes
# too. Give 1.2.0.1's Nab.elf first: it must say r.nabaztag.com/vl, or the
# proof proves nothing.
#
#   tools/default-server-proof.sh ../bin/naboot-1.2.0.1/Nab.elf ../bin/naboot-1.2.0.3/Nab.elf
#
# Environment: RUNTIME (podman), TAG (nabaztag-emulator), NO_BUILD to reuse
# the image.
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
if [ $# -lt 1 ]; then sed -n '2,15p' "$0"; exit 2; fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${NO_BUILD:-}" ]; then
  STEPS=$(sed -n 's/^STEPS=//p' "$HERE/run.sh")
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . )
fi

for elf in "$@"; do
  WORK="$(mktemp -d)"
  python3 "$HERE/tools/bootbc.py" "$elf" -o "$WORK/boot.bc"
  cp "$HERE/scenarios/vm/blank-conf.txt" "$WORK/scenario.txt"
  : > "$WORK/conf.eeprom"
  "$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
    "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
     && ./rabbit-vm /work/scenario.txt --eeprom /work/conf.eeprom >/dev/null 2>&1" || true
  server=$(head -c 41 "$WORK/conf.eeprom" | tr -d '\000-' )
  printf '%s\t%s\n' "$elf" "${server:-(nothing written)}"
  rm -rf "$WORK"
done
