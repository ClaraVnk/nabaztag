#!/usr/bin/env bash
# Build the ARM firmware on the HAOS host (Docker is always available there)
# and pull the produced .sim back to ./bin/ in this directory.
#
# SECURITY: this script ships ONLY the build context (Dockerfile + patches +
# public key header) to HAOS. The Ed25519 *private* signing key NEVER leaves
# the Mac: it lives at ~/.nabaztag/signing_key.bin and is used locally by
# signing/sign_sim.py to wrap the unsigned .sim after build.
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
ssh "$REMOTE" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"
scp -q "$HERE/Dockerfile" "$HERE/apply-mods.py" "$REMOTE:$REMOTE_DIR/"
scp -q -r "$HERE/upstream" "$HERE/crypto" "$HERE/keys" "$HERE/patches" \
  "$REMOTE:$REMOTE_DIR/"

echo ">> building image on $REMOTE"
# Stream the build log + propagate the docker exit code (so a build failure
# kills the script before we tar+sign stale artifacts from a previous run).
# Capture the full log locally and surface the relevant tail.
LOGFILE="$HERE/bin/.build.log"
mkdir -p "$HERE/bin"
if ! ssh "$REMOTE" "set -o pipefail; cd $REMOTE_DIR && docker build -t $TAG . 2>&1; exit \${PIPESTATUS[0]}" > "$LOGFILE"; then
  echo "!! docker build FAILED on $REMOTE — last 80 lines:" >&2
  tail -80 "$LOGFILE" >&2
  echo "(full log at $LOGFILE)" >&2
  exit 1
fi
tail -30 "$LOGFILE"

echo ">> extracting artifacts"
mkdir -p "$HERE/bin"
ssh "$REMOTE" "docker run --rm $TAG" | tar -xv -C "$HERE/bin/"

# Sign locally on the Mac (private key lives at ~/.nabaztag/signing_key.bin
# and never leaves this machine). Skip silently if the key isn't present yet.
if [ -f "$HOME/.nabaztag/signing_key.bin" ]; then
  echo ">> signing .sim with local Ed25519 key"
  python3 "$HERE/signing/sign_sim.py" "$HERE/bin/firmware0.0.0.13.sim" \
    -o "$HERE/bin/firmware0.0.0.13.signed.sim"
  python3 "$HERE/signing/verify_sim.py" "$HERE/bin/firmware0.0.0.13.signed.sim"
else
  echo ">> SKIP signing: no key at ~/.nabaztag/signing_key.bin (run signing/gen_key.py first)"
fi

ls -la "$HERE/bin/"
