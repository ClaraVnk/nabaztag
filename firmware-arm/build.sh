#!/usr/bin/env bash
# Build the ARM firmware on the HAOS host (Docker is always available there)
# and pull the produced .sim back to ./bin/ in this directory.
#
# Usage:
#   ./build.sh                  # full build (vanilla nabgcc wpa2 HEAD + patches)
#   ./build.sh --remote HOST    # override remote (default root@192.168.1.15)

set -euo pipefail
REMOTE="root@192.168.1.15"
if [ "${1:-}" = "--remote" ]; then REMOTE="$2"; shift 2; fi

HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="nab-firmware-build"
REMOTE_DIR="/tmp/nab-firmware-build"

echo ">> shipping build context to $REMOTE:$REMOTE_DIR"
ssh "$REMOTE" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR/patches"
scp -q "$HERE/Dockerfile" "$REMOTE:$REMOTE_DIR/Dockerfile"
# Ship patches dir (always — possibly empty except for .keep)
scp -q -r "$HERE/patches/." "$REMOTE:$REMOTE_DIR/patches/"

echo ">> building image on $REMOTE"
ssh "$REMOTE" "cd $REMOTE_DIR && docker build -t $TAG . 2>&1 | tail -40"

echo ">> extracting artifacts"
mkdir -p "$HERE/bin"
ssh "$REMOTE" "docker run --rm $TAG" | tar -xv -C "$HERE/bin/"
ls -la "$HERE/bin/"
