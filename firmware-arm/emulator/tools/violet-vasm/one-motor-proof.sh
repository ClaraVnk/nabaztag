#!/bin/sh
# One ear motor at a time: patches/one-motor-at-a-time.patch (an order) and
# patches/p2-acquisition-one-motor.patch (the homing turn at boot, the
# acquisition turn after a hand) proven in the v1 emulator, on both firmwares
# a real v1 runs (Newbaztag's 2.0b, Lazare's 1.4b), with Violet's own frame
# as the control. The two other patches ride along (review-proof.sh proves
# them) and must not break any of this.
#
#   firmware-arm/emulator/tools/violet-vasm/one-motor-proof.sh   # repo root, dev VM
#   V1_PORT=28082 ...                                             # 28081 taken
#
# Why (2026-11-24): the 1.4b firmware keeps the PIC's brown-out reset on, and
# on Lazare two ear motors started in the same step restarted the rabbit.
# Violet's Nabaztag_wait starts both motors in the same millisecond on an
# order; the Terrier's copy starts the right one only once the left one has
# stopped. Each run, 170 s: the program taken (st=01), a message waiting, the
# order L5 R10 then L2 R6 (both ears turn each time; R6 keeps the right ear
# away from the wide gap of its wheel, where a hand of 4 slots counts fewer
# holes than the program's threshold and goes unseen: R3 did on 1.4b), a hand
# on the right ear at 120 s, a click at 150 s. The rest of the program must still work: the
# hand is reported (sd=8xxx/9xxx: bit 7 of the left ear, then the right one)
# and the click sends sd=0003. The reported value is not compared with the
# control's: Violet's own program answers the same hand with 8204, 9004 or
# 9010 from one second to the next (2026-11-24, 2.0b, a hand at 118 to 122 s),
# its acquisition turn rounding to another hole. PASS when the control starts
# both motors together on each order and the patched program never does, on
# both firmwares, and the 1.4b watchdog never fires; and (2026-11-24, p2)
# when the control's homing turn at boot and acquisition turn after the hand
# start both motors together, the patched program's start the right one
# once the left one has stopped.
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
[ "$(wc -c < "$OUT/terrier.frame")" -eq 33860 ] || fail "the Terrier's frame is not 33860 bytes"

# emu FW ARGS...: the emulator on firmware 2.0b or 1.4b (its own flash chip).
emu() {
  fw=$1; shift
  if [ "$fw" = 1.4b ]; then
    podman run --rm --network host -v "$DUMPS:/d:ro,z" nabaztag-v1 --warren 127.0.0.1 \
      --port80 "$PORT" --hex /d/violet-v1-1.4b.hex --flash-chip 081 "$@"
  else
    podman run --rm --network host nabaztag-v1 --warren 127.0.0.1 --port80 "$PORT" "$@"
  fi
}

# run FW FRAME: one rabbit; the orders at polls 4 and 8 (about 48 s and 96 s).
run() {
  python3 "$WARREN" --port "$PORT" --frame "$OUT/$2.frame" --ears 5 10 --ears 2 6 \
    --after 3 --every 4 --messages 1 > "$OUT/$1-$2-warren.log" 2>&1 &
  W=$!
  sleep 1
  emu "$1" --seconds 170 --hand 1 4 120 --press 150 0.3 > "$OUT/$1-$2.log" 2>&1 || true
  kill $W
  wait $W 2>/dev/null || true
}

# starts LOG: one line per start of an ear motor, from the emulator's
# "ear N: motor forward/stopped" lines: the window it falls in (the orders
# come at polls 4 and 8, so a start between 30 s and the hand at 120 s
# answers one; before and after it is the homing or acquisition turn), the
# ear, the ms, and what the other motor was doing: "alone" (stopped),
# "together" (started within 1 ms), "overlap" (running). 2026-11-24: the
# earlier grouping of starts into movements broke when the ears' return
# after the acquisition came less than 3 s after it (it waits for a poll).
starts() {
  awk '
    /ear [01]: motor (forward|backward|stopped)/ {
      match($0, /^\[ *[0-9]+/); t = substr($0, RSTART + 1, RLENGTH - 1) + 0
      match($0, /ear [01]:/); e = substr($0, RSTART + 4, 1) + 0
      if ($0 ~ /stopped/) { run[e] = 0; next }
      o = 1 - e
      v = run[o] ? (t - start[o] <= 1 ? "together" : "overlap") : "alone"
      run[e] = 1; start[e] = t
      w = t > 30000 && t < 120000 ? "order" : "acquisition"
      printf "%s: ear %d starts at %d ms, other motor %s: %s\n", w, e, t, run[o] ? "running" : "stopped", v
    }' "$1" | awk '
    # the first of two motors started in the same ms is "alone" at its own
    # start: give it the verdict of the second
    { line[NR] = $0 }
    END {
      for (i = 1; i <= NR; i++) {
        if (i < NR && line[i] ~ /alone$/ && line[i + 1] ~ /together$/) {
          split(line[i], a, " at "); split(line[i + 1], b, " at ")
          ta = a[2] + 0; tb = b[2] + 0
          if (tb - ta <= 1) sub(/stopped: alone$/, "starting too: together", line[i])
        }
        print line[i]
      }
    }'
}

for fw in 2.0b 1.4b; do
  for frame in violet terrier; do
    run "$fw" "$frame"
    echo "== $fw, $frame's frame"
    grep -E 'ear [01]:|button|watchdog' "$OUT/$fw-$frame.log" || true
    grep -E 'poll' "$OUT/$fw-$frame-warren.log" || true
    starts "$OUT/$fw-$frame.log" | tee "$OUT/$fw-$frame.starts"
    grep -q 'st=01&tc=7FFFFFFF' "$OUT/$fw-$frame-warren.log" || fail "$fw $frame: the program was not taken"
    grep -q 'sd=0003' "$OUT/$fw-$frame-warren.log" || fail "$fw $frame: the click sent no sd=0003"
    grep -q 'sd=[89][0-9A-F]\{3\}' "$OUT/$fw-$frame-warren.log" || fail "$fw $frame: the hand was not reported"
    [ "$(grep -c '^order: ear 1' "$OUT/$fw-$frame.starts")" -eq 2 ] || fail "$fw $frame: the right ear did not start twice for the two orders"
    [ "$(grep -c '^acquisition: ear 1' "$OUT/$fw-$frame.starts")" -ge 2 ] || fail "$fw $frame: the right ear did not start in the homing and acquisition turns"
    grep -q 'watchdog' "$OUT/$fw-$frame.log" && fail "$fw $frame: the watchdog fired"
    if [ "$frame" = violet ]; then
      [ "$(grep -c '^order: ear 1.*together$' "$OUT/$fw-$frame.starts")" -eq 2 ] || fail "$fw control: an order did not start both motors together"
      grep -q '^acquisition: ear 1.*together$' "$OUT/$fw-$frame.starts" || fail "$fw control: no homing or acquisition turn started both motors together"
    else
      grep -qv 'alone$' "$OUT/$fw-$frame.starts" && fail "$fw terrier: a motor started while the other one ran"
    fi
  done
done
echo "PASS one motor at a time: Violet's frame starts both ears together on an order and in its homing and acquisition turns, the Terrier's one after the other, on 2.0b and 1.4b ($OUT)"
