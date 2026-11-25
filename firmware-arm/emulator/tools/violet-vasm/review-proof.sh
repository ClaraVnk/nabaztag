#!/bin/sh
# The two timeouts of REVIEW.md, proven in the v1 emulator on both firmwares
# (2.0b, 1.4b) against Violet's own frame as the control:
#
#   patches/p3-acquisition-timeout.patch: an ear whose motor runs but whose
#   wheel does not turn (Lazare's right ear, 2026-11-24; the emulator's
#   --stall 1) kept Violet's acquisition turn (@motorturn, after a hand)
#   going for ever, that motor on, the program never back in its wait loop.
#   The Terrier's copy gives up after 40 s, reports the ears (that one at 0)
#   and goes on.
#
#   patches/p4-click-timeout.patch: a click with a message waiting sends
#   sd=0003 and Violet's program waits in @sendwaitloop for the message
#   program the server was to answer with -- for ever if none comes (the
#   Terrier has nothing left to play, the sound was dropped): no ear order,
#   no hand, no belly, no breathing until the next program. The Terrier's
#   copy goes back to waiting (@startwait) after 40 s.
#
#   patches/p5-info-hops.patch: eight (service, value) pairs of type 14 make
#   @info hop from pair to pair without a WAIT; the step's instruction limit
#   is all that ends it, every step, and the firmware stops polling: the
#   rabbit is deaf to its server until a reboot. The Terrier's copy gives up
#   after eight hops.
#
#   firmware-arm/emulator/tools/violet-vasm/review-proof.sh   # repo root, dev VM
#   V1_PORT=28102 ...                                          # 28081 taken
#
# Eight runs per firmware. "stall": ear 1 stalled, a hand on ear 0 at 70 s,
# 160 s: PASS when the control's ear 1 motor starts after the hand and never
# stops and no hand is reported, and the patched program's stops after
# 40 s of stall (47 s on the emulator's clock: the firmware's second is
# about 1.18 s there) and the hand is reported (sd=8L00: left ear, right 0).
# "click": one message waiting, a click at 60 s, an ear order L5 R10 from
# poll 8 (about 96 s), 160 s: PASS when both send sd=0003, the control's ears
# never move after the click, and the patched program's go to the order's
# order (5 and 10 holes from the new home) after 100 s. "stall0": ear 0
# stalled from boot, 130 s: Violet's @motorreset keeps its 30 s deadline in
# r10 and writes the right ear's target over it (@reset5), so a left ear
# that never turns, next to a right one that homes, leaves the homing to a
# chance match of the second counter with that target; the patched one
# (p2, the target in r13) stops it after 40 s. PASS when it does and the
# rabbit still polls; the control's log is shown. "all14": eight pairs
# (14, 1), 90 s: PASS when
# the control polls twice and never again, and the patched program keeps
# polling. The 1.4b watchdog must never fire.
set -eu
cd "$(dirname "$0")/../../../.."
OUT=$(mktemp -d)
PORT=${V1_PORT:-28081}
DUMPS=$(cd firmware-arm/v1-icsp/dumps && pwd)
WARREN=firmware-arm/emulator/tools/violet-vasm/violet_warren.py
fail() { echo "FAIL: $*"; exit 1; }

podman build -q -t violet-vasm -f firmware-arm/emulator/tools/violet-vasm/Containerfile . >/dev/null
podman build -q -t nabaztag-v1 -f firmware-arm/emulator/v1/Containerfile firmware-arm >/dev/null
podman run --rm violet-vasm Nabaztag_wait.vasm > "$OUT/violet.frame"
podman run --rm violet-vasm Nabaztag_wait_terrier.vasm > "$OUT/terrier.frame"
[ "$(wc -c < "$OUT/violet.frame")" -eq 33747 ] || fail "Violet's frame is not 33747 bytes"

emu() {
  fw=$1; shift
  if [ "$fw" = 1.4b ]; then
    podman run --rm --network host -v "$DUMPS:/d:ro,z" nabaztag-v1 --warren 127.0.0.1 \
      --port80 "$PORT" --hex /d/violet-v1-1.4b.hex --flash-chip 081 "$@"
  else
    podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 "$PORT" "$@"
  fi
}

# run NAME FW FRAME "WARREN ARGS" EMU ARGS...
run() {
  name=$1; fw=$2; frame=$3; wargs=$4; shift 4
  # shellcheck disable=SC2086
  python3 "$WARREN" --port "$PORT" --frame "$OUT/$frame.frame" $wargs > "$OUT/$name-warren.log" 2>&1 &
  W=$!
  sleep 1
  emu "$fw" "$@" > "$OUT/$name.log" 2>&1 || true
  kill $W
  wait $W 2>/dev/null || true
}

# ms LINE: the timestamp of an emulator log line.
ms() { sed -E 's/^\[ *([0-9]+) ms\].*/\1/'; }

for fw in 2.0b 1.4b; do
  for frame in violet terrier; do
    n="$fw-$frame"
    run "$n-stall" "$fw" "$frame" "" --seconds 160 --stall 1 --hand 0 9 70
    echo "== $n, ear 1 stalled, a hand on ear 0 at 70 s"
    grep -E 'ear [01]: (motor|a hand)|watchdog' "$OUT/$n-stall.log" | awk '{ if ($1 ~ /\[/ && $2+0 > 60000) print }' || true
    grep -E 'sd=' "$OUT/$n-stall-warren.log" || true
    grep -q 'watchdog' "$OUT/$n-stall.log" && fail "$n stall: the watchdog fired"
    # the last line of ear 1 after the hand: forward for ever, or stopped
    last=$(grep -E 'ear 1: motor' "$OUT/$n-stall.log" | awk '{ if ($2+0 > 70000) print }' | tail -1)
    if [ "$frame" = violet ]; then
      echo "$last" | grep -q 'forward' || fail "$n control: ear 1 did not stay on after the hand"
      grep -q 'sd=[89]' "$OUT/$n-stall-warren.log" && fail "$n control: the hand was reported"
    else
      echo "$last" | grep -q 'stopped' || fail "$n terrier: the stalled ear 1 was not stopped"
      # the deadline runs from the start of the turn (ear 0 first, then ear 1),
      # in the firmware's seconds, which the emulator's clock shows as about
      # 1.18 s each (the 03 = 1 poll interval is 11.8 s on the same clock):
      # 40 of them are 47 s here
      t0=$(grep -E 'ear 0: motor forward' "$OUT/$n-stall.log" | awk '{ if ($2+0 > 72000) print $2 }' | head -1)
      t1=$(echo "$last" | awk '{ print $2 }')
      d=$((t1 - t0))
      [ "$d" -ge 39000 ] && [ "$d" -le 49000 ] || fail "$n terrier: the turn ended $d ms after it started, not 40 s"
      grep -q 'sd=[89][0-9A-F]00' "$OUT/$n-stall-warren.log" || fail "$n terrier: the hand was not reported with the stalled ear at 0"
    fi

    run "$n-click" "$fw" "$frame" "--messages 1 --ears 5 10 --after 7" --seconds 160 --press 60 0.3
    echo "== $n, a message waiting, a click at 60 s, then an order L5 R10"
    grep -E 'ear [01]: motor|button|watchdog' "$OUT/$n-click.log" | awk '{ if ($2+0 > 55000) print }' || true
    grep -E 'sd=' "$OUT/$n-click-warren.log" || true
    grep -q 'watchdog' "$OUT/$n-click.log" && fail "$n click: the watchdog fired"
    grep -q 'sd=0003' "$OUT/$n-click-warren.log" || fail "$n click: no sd=0003"
    after=$(grep -E 'ear [01]: motor' "$OUT/$n-click.log" | awk '{ if ($2+0 > 61000) print }')
    if [ "$frame" = violet ]; then
      [ -z "$after" ] || fail "$n control: the ears moved after the click"
    else
      # after the 40 s: the homing turn (one movement per ear, where the gap
      # detection stops varies by a hole or two from one run to the next),
      # then the order from the new home: 5 holes left (2.2 s), 10 right
      # (4.3 s, plus 1.4 s when they cross the wide gap)
      for e in 0 1; do
        [ "$(echo "$after" | grep -c "ear $e: motor stopped")" -ge 2 ] || fail "$n terrier: ear $e did not home and then take the order after the click"
      done
      l=$(echo "$after" | grep 'ear 0: motor stopped' | tail -1 | sed -E 's/.*after ([0-9]+) ms.*/\1/')
      r=$(echo "$after" | grep 'ear 1: motor stopped' | tail -1 | sed -E 's/.*after ([0-9]+) ms.*/\1/')
      [ "$l" -ge 1500 ] && [ "$l" -le 3900 ] || fail "$n terrier: the left ear's order took $l ms, not about 5 holes"
      [ "$r" -ge 3800 ] && [ "$r" -le 6500 ] || fail "$n terrier: the right ear's order took $r ms, not about 10 holes"
    fi

    run "$n-stall0" "$fw" "$frame" "" --seconds 130 --stall 0
    echo "== $n, ear 0 stalled from boot (the homing turn's deadline)"
    grep -E 'ear [01]: motor|watchdog' "$OUT/$n-stall0.log" | awk '{ if ($2+0 > 1000) print }' || true
    grep -q 'watchdog' "$OUT/$n-stall0.log" && fail "$n stall0: the watchdog fired"
    if [ "$frame" = terrier ]; then
      t0=$(grep -E 'ear 0: motor forward' "$OUT/$n-stall0.log" | awk '{ if ($2+0 > 1000) print $2 }' | head -1)
      t1=$(grep -E 'ear 0: motor stopped' "$OUT/$n-stall0.log" | awk '{ if ($2+0 > 1000) print $2 }' | head -1)
      [ -n "$t1" ] || fail "$n terrier: the stalled left ear was never stopped"
      d=$((t1 - t0))
      [ "$d" -ge 39000 ] && [ "$d" -le 49000 ] || fail "$n terrier: the homing ended $d ms after it started, not 40 s"
      [ "$(grep -c '^poll' "$OUT/$n-stall0-warren.log")" -ge 6 ] || fail "$n terrier: the rabbit stopped polling"
    fi

    run "$n-all14" "$fw" "$frame" "--source 14 1 --source 14 1 --source 14 1 --source 14 1 --source 14 1 --source 14 1 --source 14 1 --source 14 1" --seconds 90
    polls=$(grep -c '^poll' "$OUT/$n-all14-warren.log")
    echo "== $n, eight sources of type 14: $polls polls in 90 s"
    grep -q 'watchdog' "$OUT/$n-all14.log" && fail "$n all14: the watchdog fired"
    if [ "$frame" = violet ]; then
      [ "$polls" -le 3 ] || fail "$n control: still polling with eight sources of type 14"
    else
      [ "$polls" -ge 6 ] || fail "$n terrier: stopped polling with eight sources of type 14"
    fi
  done
done
echo "PASS review: a stalled ear ends the acquisition turn after 40 s, a click with no message program goes back to waiting after 40 s, eight sources of type 14 leave the rabbit polling, on 2.0b and 1.4b; Violet's frame hangs in all three cases ($OUT)"
