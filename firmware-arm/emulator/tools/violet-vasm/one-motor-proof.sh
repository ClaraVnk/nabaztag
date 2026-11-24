#!/bin/sh
# One ear motor at a time: patches/one-motor-at-a-time.patch proven in the v1
# emulator, on both firmwares a real v1 runs (Newbaztag's 2.0b, Lazare's
# 1.4b), with Violet's own frame as the control.
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
# both firmwares, and the 1.4b watchdog never fires. Not covered: that
# acquisition turn (@motorturn, at boot and after a hand) still starts both
# motors at once, in both programs.
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
[ "$(wc -c < "$OUT/terrier.frame")" -eq 33754 ] || fail "the Terrier's frame is not 33754 bytes"

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

# starts LOG: one line per movement of both ears, from the emulator's
# "ear N: motor forward/stopped" lines: the ms each motor ran and the slot it
# stopped at, what it is (the orders come at polls 4 and 8, so a movement
# between 30 s and the hand at 120 s answers one; before and after it is the
# acquisition turn, whose stop slots differ between the two firmwares), then
# "together" (both started within 1 ms), "serial" (the right one started
# after the left one stopped) or "overlap". Two movements are 3 s apart at
# least.
starts() {
  awk '
    /ear [01]: motor (forward|backward|stopped)/ {
      match($0, /^\[ *[0-9]+/); t = substr($0, RSTART + 1, RLENGTH - 1) + 0
      match($0, /ear [01]:/); e = substr($0, RSTART + 4, 1) + 0
      if ($0 ~ /stopped/) {
        match($0, /slot [0-9]+/); slot[m, e] = substr($0, RSTART + 5, RLENGTH - 5) + 0
        run[e] = 0; stop[m, e] = t; last = t; next
      }
      if (!run[0] && !run[1] && t - last > 3000) m++
      if (!((m, e) in start)) start[m, e] = t
      run[e] = 1
    }
    END {
      for (i = 1; i <= m; i++) if ((i, 0) in start && (i, 1) in start) {
        d = start[i, 1] - start[i, 0]; if (d < 0) d = -d
        v = d <= 1 ? "together" : (start[i, 1] >= stop[i, 0] ? "serial" : "overlap")
        o = start[i, 0] > 30000 && start[i, 0] < 120000 ? "order" : "acquisition"
        printf "%s: left %d-%d ms (slot %d), right %d-%d ms (slot %d): %s\n", \
          o, start[i, 0], stop[i, 0], slot[i, 0], start[i, 1], stop[i, 1], slot[i, 1], v
      }
    }' "$1"
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
    [ "$(grep -c '^order' "$OUT/$fw-$frame.starts")" -eq 2 ] || fail "$fw $frame: not two movements of both ears for the two orders"
    grep -q 'watchdog' "$OUT/$fw-$frame.log" && fail "$fw $frame: the watchdog fired"
    if [ "$frame" = violet ]; then
      [ "$(grep -c '^order.*together$' "$OUT/$fw-$frame.starts")" -eq 2 ] || fail "$fw control: an order did not start both motors together"
    else
      [ "$(grep -c '^order.*serial$' "$OUT/$fw-$frame.starts")" -eq 2 ] || fail "$fw terrier: an order started the two motors at once"
    fi
  done
done
echo "PASS one motor at a time: Violet's frame starts both ears together on an order, the Terrier's one after the other, on 2.0b and 1.4b ($OUT)"
