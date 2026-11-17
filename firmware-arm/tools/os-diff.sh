#!/bin/sh
# The two builds' assembly listings, side by side (tools/os-diff.py):
#
#   tools/os-diff.sh [image -O1] [image -Os] [path prefix ...]
#
# Defaults: the build images build.sh leaves for naboot-1.2.0.1 (-O1) and
# naboot-1.2.0.2 (the same at -Os). Run where they were built (the dev VM).
set -eu
A=${1:-localhost/nab-firmware-build:naboot-1.2.0.1}
B=${2:-localhost/nab-firmware-build:naboot-1.2.0.2}
[ $# -ge 2 ] && shift 2 || set --
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
for side in a b; do
    img=$A; [ $side = b ] && img=$B
    mkdir -p "$W/$side"
    podman run --rm --entrypoint tar "$img" -C /build/nabgcc/obj/lst -cf - . | tar -xf - -C "$W/$side"
done
python3 "$(dirname "$0")/os-diff.py" "$W/a" "$W/b" "$@"
