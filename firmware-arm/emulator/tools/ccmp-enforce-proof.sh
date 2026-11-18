#!/usr/bin/env bash
# The CCMP replay refusal with and without its probation (review S-2).
#
#   tools/ccmp-enforce-proof.sh
#
# Three runs of scenarios/enforce/* on ./rabbit:
#   1. control  -- the default step list plus ccmp_replay_enforce, as the
#                  refusal shipped in 1.2.0.0..1.2.0.2: ccmp-replay.txt
#                  passes, ccmp-probation.txt FAILS (200 replays in the first
#                  seconds turn the refusal off: 65 frames handed up);
#   2. fixed    -- the same plus ccmp_no_probation (Naboot 1.2.0.3): both pass.
# Exit status 0 when that is what happened. Environment: RUNTIME (podman),
# TAG (nabaztag-emulator-enforce), NO_BUILD to reuse the two images.
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator-enforce}
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEPS=$(sed -n 's/^STEPS=//p' "$HERE/run.sh")

build() {  # tag steps
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$2" -t "$1" \
      -f firmware-arm/emulator/Containerfile . >/dev/null )
}
one() {  # tag scenario -> prints PASS/FAIL, returns the run's status
  if "$RUNTIME" run --rm "$1" ./rabbit "$2" > "/tmp/$(basename "$2" .txt)-$3.log" 2>&1; then
    echo "  PASS  $2"; return 0
  else
    echo "  FAIL  $2"; grep -E 'FAIL|handed up' "/tmp/$(basename "$2" .txt)-$3.log" | tail -3 | sed 's/^/        /'; return 1
  fi
}

if [ -z "${NO_BUILD:-}" ]; then
  echo "== building $TAG-control (ccmp_replay_enforce) and $TAG (plus ccmp_no_probation)"
  build "$TAG-control" "$STEPS,ccmp_replay_enforce"
  build "$TAG" "$STEPS,ccmp_replay_enforce,ccmp_no_probation"
fi
ok=1
echo "== CONTROL: the refusal with its probation"
one "$TAG-control" scenarios/enforce/ccmp-replay.txt control || ok=0
if one "$TAG-control" scenarios/enforce/ccmp-probation.txt control; then
  echo "  (the probation scenario passed on the control: it proves nothing)"; ok=0
fi
echo "== FIXED: no probation"
one "$TAG" scenarios/enforce/ccmp-replay.txt fixed || ok=0
one "$TAG" scenarios/enforce/ccmp-probation.txt fixed || ok=0
[ "$ok" = 1 ]
