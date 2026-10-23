#!/usr/bin/env bash
# Build and run the rabbit emulator in a container.
#
#   ./run.sh                              every scenario, pass or fail
#   ./run.sh scenarios/open-deauth.txt    one scenario, full transcript
#   ./run.sh --steps wpa2_gtk,mdns        pick which firmware mods to include
#
# Nothing is needed on the host but podman (or docker, with --runtime docker).
# The rabbit stays plugged in.
set -euo pipefail

RUNTIME=podman
STEPS=wpa2_gtk
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --runtime) RUNTIME="$2"; shift 2 ;;
    --steps)   STEPS="$2";   shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *)         ARGS+=("$1"); shift ;;
  esac
done

cd "$(dirname "$0")/.."     # firmware-arm/, which is the build context

echo "== building the emulator image (nabgcc mods: $STEPS)"
"$RUNTIME" build \
  --build-arg "MOD_STEPS=$STEPS" \
  -t nabaztag-emulator \
  -f emulator/Containerfile .

if [ ${#ARGS[@]} -eq 0 ]; then
  exec "$RUNTIME" run --rm nabaztag-emulator
fi

exec "$RUNTIME" run --rm nabaztag-emulator ./rabbit "${ARGS[@]}"
