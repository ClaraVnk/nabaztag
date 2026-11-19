#!/bin/sh
# Build the LapinLibre USB key with WPA3 on the dev VM. Hours the first time;
# one heavy job at a time on that VM, hence the lock.
#   sh karotz/lapinlibre/build.sh        # -> ~/lapinlibre-work/usbkey
#   LL_STAGES=test sh karotz/lapinlibre/build.sh   # the tests alone, on the built tree
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
W=${LL_WORK_DIR:-$HOME/lapinlibre-work}
mkdir -p "$W"
podman build -t lapinlibre-build -f "$HERE/Containerfile" "$HERE"
# --privileged: LapinLibre's kernel stage and tests use `unshare --user`.
flock "$HOME/heavy.lock" podman run --rm --privileged -e LL_STAGES="${LL_STAGES:-}" --userns=keep-id:uid=1000,gid=1000 -v "$W:/work:Z" lapinlibre-build
ls "$W/usbkey"
