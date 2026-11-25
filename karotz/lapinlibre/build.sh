#!/bin/sh
# Build the LapinLibre USB key with WPA3 on the dev VM. Hours the first time;
# one heavy job at a time on that VM, hence the lock.
#   sh karotz/lapinlibre/build.sh        # -> ~/lapinlibre-work/usbkey, no tests
#   LL_TESTS=1 sh karotz/lapinlibre/build.sh       # every stage and the whole suite
#   LL_STAGES=test sh karotz/lapinlibre/build.sh   # the tests alone, on the built tree
# No tests by default, as for Naboot: the suite is upstream's, about 40
# minutes on the VM (2026-11-20), and only a change to LapinLibre's own code
# (LL_COMMIT, our fork's branches) calls for it.
set -eu
STAGES="check fetch toolchain system v8 kernel userland image"
[ "${LL_TESTS:-0}" = 1 ] && STAGES="$STAGES test"
HERE=$(cd "$(dirname "$0")" && pwd)
W=${LL_WORK_DIR:-$HOME/lapinlibre-work}
mkdir -p "$W"
podman build -t lapinlibre-build -f "$HERE/Containerfile" "$HERE"
# LL_REUSE_UNCHANGED=1: a commit that leaves the kernel alone no longer recompiles it (our
# fork's kernel-cache branch; 2026-11-19, each bump of LL_COMMIT rebuilt it).
# --privileged: LapinLibre's kernel stage and tests use `unshare --user`.
# LL_BUILD_VERSION: Terrier OS's own number (VERSION, 1.0.0 and up, semver), which the key's
# install.conf, the rabbit's status and make-img.sh's file names carry; the LapinLibre commit
# the build came from stays in /karotz/etc/lapinlibre-base (patches/0002).
VERSION=$(tr -d ' \n' < "$HERE/VERSION")
flock "$HOME/heavy.lock" podman run --rm --privileged -e LL_STAGES="${LL_STAGES:-$STAGES}" -e LL_REUSE_UNCHANGED=1 \
    -e LL_BUILD_VERSION="${LL_BUILD_VERSION:-$VERSION}" --userns=keep-id:uid=1000,gid=1000 -v "$W:/work:Z" lapinlibre-build
ls "$W/usbkey"
