#!/usr/bin/env bash
# Build the ARM firmware on a remote with Docker or Podman, then pull the
# produced .sim back to ./bin/ in this directory.
#
# SECURITY: this script ships ONLY the build context (Dockerfile + patches +
# public key header) to the remote. The Ed25519 *private* signing key NEVER
# leaves the Mac: it lives at ~/.nabaztag/signing_key.bin and is used locally
# by signing/sign_sim.py to wrap the unsigned .sim after build.
#
# Usage:
#   ./build.sh                          # full build, default remote (HAOS)
#   ./build.sh --mode minimal           # rescue / parachute build
#   ./build.sh --mode signed-stock      # bisect: bootloader patch only
#   ./build.sh --mode pages-only        # bisect: page rewrite only
#   ./build.sh --remote rocky@vps:10022 --runtime podman   # build on the VPS

set -euo pipefail
REMOTE="root@192.168.1.15"
MODE="full"
RUNTIME="docker"
while [ $# -gt 0 ]; do
  case "$1" in
    --remote)  REMOTE="$2";  shift 2 ;;
    --mode)    MODE="$2";    shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Split host[:port] form into ssh/scp friendly args (`-p PORT` for ssh,
# `-P PORT` for scp). When no `:port`, ssh/scp use their defaults.
REMOTE_HOST="${REMOTE%:*}"
REMOTE_PORT=""
if [[ "$REMOTE" == *":"* ]]; then
  REMOTE_PORT="${REMOTE##*:}"
fi
SSH=(ssh)
SCP=(scp -q)
if [ -n "$REMOTE_PORT" ]; then
  SSH+=(-p "$REMOTE_PORT")
  SCP+=(-P "$REMOTE_PORT")
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="nab-firmware-build:$MODE"
REMOTE_DIR="/tmp/nab-firmware-build-$MODE"

echo ">> shipping build context to $REMOTE_HOST:$REMOTE_DIR"
"${SSH[@]}" "$REMOTE_HOST" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"
"${SCP[@]}" "$HERE/Dockerfile" "$HERE/apply-mods.py" "$REMOTE_HOST:$REMOTE_DIR/"
"${SCP[@]}" -r "$HERE/upstream" "$HERE/crypto" "$HERE/keys" "$HERE/patches" "$HERE/pages" "$HERE/boot-mods" \
  "$REMOTE_HOST:$REMOTE_DIR/"

echo ">> building image on $REMOTE_HOST ($RUNTIME)"
# Stream the build log + propagate the build exit code (so a build failure
# kills the script before we tar+sign stale artifacts from a previous run).
# Capture the full log locally and surface the relevant tail.
LOGFILE="$HERE/bin/.build.log"
mkdir -p "$HERE/bin"
if ! "${SSH[@]}" "$REMOTE_HOST" "set -o pipefail; cd $REMOTE_DIR && $RUNTIME build --build-arg MODE=$MODE -t $TAG . 2>&1; exit \${PIPESTATUS[0]}" > "$LOGFILE"; then
  echo "!! $RUNTIME build FAILED on $REMOTE_HOST — last 80 lines:" >&2
  tail -80 "$LOGFILE" >&2
  echo "(full log at $LOGFILE)" >&2
  exit 1
fi
tail -30 "$LOGFILE"

echo ">> extracting artifacts (mode=$MODE)"
OUTDIR="$HERE/bin/$MODE"
mkdir -p "$OUTDIR"
"${SSH[@]}" "$REMOTE_HOST" "$RUNTIME run --rm $TAG" | tar -xv -C "$OUTDIR/"

# Sign locally on the Mac (private key lives at ~/.nabaztag/signing_key.bin
# and never leaves this machine). Skip silently if the key isn't present yet.
# We sign into both the mode-scoped path AND a top-level self-identifying
# name (naboot-<mode>.signed.sim) so any file is recognizable out of context.
if [ -f "$HOME/.nabaztag/signing_key.bin" ]; then
  echo ">> signing .sim with local Ed25519 key"
  python3 "$HERE/signing/sign_sim.py" "$OUTDIR/firmware0.0.0.13.sim" \
    -o "$OUTDIR/firmware0.0.0.13.signed.sim"
  python3 "$HERE/signing/verify_sim.py" "$OUTDIR/firmware0.0.0.13.signed.sim"
  cp "$OUTDIR/firmware0.0.0.13.signed.sim" "$HERE/bin/naboot-${MODE}.signed.sim"
  echo ">> also available as bin/naboot-${MODE}.signed.sim"
else
  echo ">> SKIP signing: no key at ~/.nabaztag/signing_key.bin (run signing/gen_key.py first)"
fi

ls -la "$OUTDIR/" "$HERE/bin/"naboot-*.signed.sim 2>/dev/null
