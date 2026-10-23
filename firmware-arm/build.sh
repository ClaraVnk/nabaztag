#!/usr/bin/env bash
# Build the ARM firmware on a remote with Docker or Podman, then pull the
# produced .sim back to ./bin/ in this directory.
#
# SECURITY: this script ships ONLY the build context (Dockerfile + patches +
# public key header) to the remote. The Ed25519 *private* signing key NEVER
# leaves the Mac: it lives at ~/.nabaztag/signing_key.bin and is used locally
# by signing/sign_sim.py to wrap the unsigned .sim after build.
#
# Usage (--remote is REQUIRED — there is deliberately no default):
#   ./build.sh --mode lean    --remote user@dev-vm --runtime podman   # the shipped stable
#   ./build.sh --mode minimal --remote user@dev-vm --runtime podman   # rescue / parachute
#   ./build.sh --mode signed-stock … / --mode pages-only …           # bisection builds
#
# NEVER point --remote at the Home Assistant host: a firmware build there starves
# the add-on and drops the rabbit mid-session. Use a dev VM or the VPS.

set -euo pipefail
REMOTE=""
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
if [ -z "$REMOTE" ]; then
  echo "!! --remote <user@dev-vm> is required (never build on the Home Assistant host)" >&2
  exit 2
fi

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
# Drop the previous run's SIGNED outputs first. If anything below fails, a
# stale .signed.sim must not survive next to a fresh unsigned .sim — that
# pair looks current and is exactly how an old signature gets published.
rm -f "$OUTDIR/firmware0.0.0.13.signed.sim" "$HERE/bin/naboot-${MODE}.signed.sim"
if ! "${SSH[@]}" "$REMOTE_HOST" "$RUNTIME run --rm $TAG" | tar -xv -C "$OUTDIR/"; then
  echo "!! extracting the artifact tar FAILED — nothing signed, nothing stale left" >&2
  exit 1
fi

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
  echo ">> SKIP signing: no key at ~/.nabaztag/signing_key.bin (run signing/gen_key.py first)" >&2
  echo ">> bin/$MODE/ holds the UNSIGNED .sim only — do not flash or publish it" >&2
fi

ls -la "$OUTDIR/" "$HERE/bin/"naboot-*.signed.sim 2>/dev/null

# Static checks on the image, before anybody flashes it. A Nabaztag has no
# bootloader to fall back on: an image that faults early dead-loops in the
# Data Abort vector and the only way back is a probe on the JTAG pads. See
# BRICK_FORENSICS.md, which exists because that happened, and preflight.py,
# whose first check is the global that caused it.
#
# This fails the build rather than warning. A warning at the end of a long
# build is a warning nobody reads.
echo ">> preflight checks on bin/$MODE"
if ! python3 "$HERE/preflight.py" "$OUTDIR"; then
  echo ">> NOT FLASHABLE: see the failures above. Nothing has been written to" >&2
  echo "   a rabbit; fix the build and run this again." >&2
  exit 1
fi
