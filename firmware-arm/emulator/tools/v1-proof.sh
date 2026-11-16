#!/bin/sh
# The v1 emulator's proofs, on a machine with podman (the dev VM, never HAOS).
#
#   tools/v1-proof.sh                      boot: DHCP, DNS, the poll it sends
#   tools/v1-proof.sh --terrier ../terrier the Terrier's real p3.jsp answering:
#                                          program taken, ears, a hand, a long
#                                          press, two v1 in "amoureux"
#
# Run from firmware-arm/emulator. --terrier is a checkout of the (private)
# Terrier; its tools/v1_warren.py serves the shipped handler on SQLite.
set -eu
cd "$(dirname "$0")/.."
TERRIER=
[ "${1:-}" = --terrier ] && TERRIER=$(cd "$2" && pwd)
OUT=$(mktemp -d)
PORT=${V1_PORT:-28080}
fail() { echo "FAIL: $*"; exit 1; }

podman build -q -t nabaztag-v1 -f v1/Containerfile .. >/dev/null
emu() { podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 $PORT "$@"; }

if [ -z "$TERRIER" ]; then
  python3 v1/capture.py $PORT > "$OUT/capture.log" 2>&1 &
  CAP=$!
  sleep 1
  emu --seconds 8 > "$OUT/emu.log" 2>&1
  kill $CAP
  cat "$OUT/emu.log"; cat "$OUT/capture.log"
  grep -q 'gw: DHCP acknowledge' "$OUT/emu.log" || fail "no DHCP"
  grep -q 'gw: DNS p.nabaztag.com' "$OUT/emu.log" || fail "no DNS for p.nabaztag.com"
  grep -q "GET /vl/FR/p3.jsp?sn=0019DB00BEEF&ex=000000000000&v=20&st=00&tc=00000000 HTTP/1.0" \
    "$OUT/capture.log" || fail "not the poll"
  echo "PROOF boot: Violet's firmware joins, gets an address, polls p3.jsp ($OUT)"
  exit 0
fi

warren() {
  podman run -d --rm --network host -v "$TERRIER":/t:ro,Z -w /t docker.io/library/python:3.13-slim \
    sh -c "pip install -q -r requirements.txt >/dev/null 2>&1; python3 tools/v1_warren.py --port $PORT $*"
}

# 1. One v1: program, ears 5/10 then 2/3, a hand on the right ear at 80 s,
#    the head button held 3 s at 110 s.
W=$(warren --ears 5 10 --ears 2 3 --after 2)
sleep 25
emu --seconds 140 --hand 0 4 80 --press 110 3 > "$OUT/one.log" 2>&1
sleep 2; podman logs "$W" > "$OUT/one-warren.log" 2>&1; podman rm -f "$W" >/dev/null
grep -E 'ear [01]:|button' "$OUT/one.log"; grep -E 'poll|event|queued' "$OUT/one-warren.log"
grep -q 'st=01&tc=[0-9A-F]\{8\}$' "$OUT/one-warren.log" || fail "the program was not taken"
[ "$(grep -c 'motor stopped' "$OUT/one.log")" -ge 5 ] || fail "an ear did not stop"
grep -q 'sd=8603' "$OUT/one-warren.log" || fail "no hand report 8603"
grep -q '"event": "long_press"' "$OUT/one-warren.log" || fail "no long press"

# 2. Two v1 of one owner in "amoureux": a hand on A, B follows.
W=$(warren --amoureux --after 99)
sleep 25
emu --seconds 120 --mac 0019db00bef0 > "$OUT/b.log" 2>&1 &
EB=$!
emu --seconds 120 --mac 0019db00beef --hand 0 4 60 > "$OUT/a.log" 2>&1
wait $EB
sleep 2; podman logs "$W" > "$OUT/pair-warren.log" 2>&1; podman rm -f "$W" >/dev/null
grep -E 'amoureux|sd=' "$OUT/pair-warren.log"; grep -E 'ear 0:' "$OUT/b.log"
grep -q 'amoureux: 0019db00beef ears -> 0019db00bef0' "$OUT/pair-warren.log" || fail "B not told"
grep -q 'ear 0: motor stopped, slot 7' "$OUT/b.log" || fail "B's ear did not follow"
echo "PROOF terrier: program taken, ears stop, hand and long press reported, amoureux follow ($OUT)"
