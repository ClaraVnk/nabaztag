#!/usr/bin/env bash
# A firmware upload through the config page, in the emulator.
#
#   tools/config-upload.sh <Nab.elf | boot.bc> <file.sim> [rabbit-vm args...]
#
# Builds the emulator image the way run.sh does, builds the VM against the
# bootloader bytecode of the image the rabbit is running (lifted from Nab.elf
# with bootbc.py, or a blob already lifted / compiled), and runs
# scenarios/vm/config-upload.txt with the .sim as what the phone uploads.
#
#   tools/config-upload.sh ../bin/lean/Nab.elf \
#       ../bin/naboot-1.0.0.0/firmware0.0.0.13.signed.sim
#
# Extra arguments go to rabbit-vm (--trace-net, for one). Environment:
#   RUNTIME   podman (default) or docker
#   STEPS     the apply-mods steps the VM is built with; run.sh's default
#   SCENARIO  another scenario file (default scenarios/vm/config-upload.txt)
#   TAG       the image tag (default nabaztag-emulator)
#   NO_BUILD  set to skip rebuilding the image
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
SCENARIO=${SCENARIO:-scenarios/vm/config-upload.txt}
# run.sh's list, read from it: a copy here went stale (2026-11-13: no
# entropy_pool, which adoption_kx requires, and the image would not build).
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$(dirname "$0")/../run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}

if [ $# -lt 2 ]; then sed -n '2,22p' "$0"; exit 2; fi
IMAGE=$1; SIM=$2; shift 2

HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

case "$IMAGE" in
  *.elf|*.ELF|*/Nab.elf)
    python3 "$HERE/tools/bootbc.py" "$IMAGE" -o "$WORK/boot.bc" ;;
  *)
    cp "$IMAGE" "$WORK/boot.bc" ;;
esac
cp "$SIM" "$WORK/upload.sim"
cp "$HERE/$SCENARIO" "$WORK/scenario.txt"

if [ -z "${NO_BUILD:-}" ]; then
  echo "== building the emulator image (nabgcc mods: $STEPS)"
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . )
fi

echo "== $(basename "$IMAGE") in config mode, uploading $(basename "$SIM") ($(wc -c < "$SIM") bytes)"
exec "$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
  "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
   && ./rabbit-vm /work/scenario.txt --no-eeprom --upload /work/upload.sim $*"
