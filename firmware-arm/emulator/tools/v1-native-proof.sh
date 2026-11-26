#!/bin/sh
# The native v1 firmware (firmware-arm/v1-native), built and run in the v1
# emulator on Lazare's board (PIC18LF6525, AT45DB081B): boot, LEDs, the card
# joined, DHCP, DNS, the Terrier's endpoint polled three times, the ears
# moved, the watchdog never fired; the serial flash's boot counter across
# two runs; the image's flash and RAM use. PASS or FAIL.
#
#   tools/v1-native-proof.sh          from firmware-arm/emulator, on a machine
#                                     with podman (the dev VM, never HAOS)
#
# V1_PORT overrides the warren's port (28160).
set -eu
cd "$(dirname "$0")/.."
PORT=${V1_PORT:-28160}
OUT=$(mktemp -d)
NATIVE=$(cd ../v1-native && pwd)
fail() { echo "FAIL: $*"; exit 1; }
need() { grep -q -- "$2" "$1" || fail "$3"; }

podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c "make clean >/dev/null && make" > "$OUT/build.log" 2>&1 \
  || { cat "$OUT/build.log"; fail "the firmware does not build"; }
grep -E '^(loader|app-30):$|^(flash|ram) ' "$OUT/build.log" | tr '\n' ' ' | tee "$OUT/size.txt"; echo
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null

mkdir -p "$OUT/fw"
# Frame 13's sound: a 16 kHz tone in Violet's Audi container, served by the
# warren at GET /vl/v1/snd (tools/ is mounted at /t).
python3 tools/violet-vasm/v1-audi.py 16000 0.5 tools/proof.audi > /dev/null
trap 'rm -f tools/proof.audi' EXIT
cp "$NATIVE/out/18f6525/full.hex" "$OUT/fw/"
# The warren in a container on the host network, like the emulator (on a
# Mac that network is the podman machine's).
podman run --rm --network host --name "v1warren-$PORT" -v "$PWD/tools":/t:ro,Z \
  docker.io/library/python:3.13-alpine python3 -u /t/v1_native_warren.py "$PORT" --ears 3 5 \
  --leds 1,2,4,3,7,2 --sound /t/proof.audi --asleep 1 --rest 6 --config p.nabaztag.com Terrier \
  > "$OUT/warren.log" 2>&1 &
WARREN=$!
trap 'kill $WARREN 2>/dev/null; podman rm -f "v1warren-$PORT" >/dev/null 2>&1; rm -f tools/proof.audi' EXIT
sleep 2

emu() {
  podman run --rm --network host -v "$OUT/fw":/img:Z nabaztag-v1-native \
    --hex /img/full.hex --pic 6525 --flash-chip 081 --flash /img/v1native.flash \
    --warren 127.0.0.1 --port80 "$PORT" "$@"
}
# Since phase 2 the loader runs first and, with the key mkimage.py put in
# the EEPROM, seeds slot A (about 10 s of HMAC) before the application.
# --bcast-deaf: the access point's broadcasts never reach the card, as
# Lazare's 802.11b card under a UniFi AP (2026-11-25): DHCP must not ask for
# a broadcast reply.
# A click at 60 s, a long press at 75 s, the right ear (0) turned 4 holes
# by hand at 95 s: the button and the ears by hand go to sd=.
emu --seconds 135 --bcast-deaf --an0 0 --sound-press 50 --press 60 0.2 --press 75 2 --hand 0 4 95 > "$OUT/run1.log" 2>&1 || true   # several 40-second network reports
emu --seconds 6 > "$OUT/run2.log" 2>&1 || true
# No access point at all (Lazare at blue, 2026-11-24/25): after 30 s the
# firmware writes what the card says into the EEPROM, for card-report.py.
emu --seconds 75 --link-down 0.1 0 > "$OUT/run3.log" 2>&1 || true
sleep 1
grep -E 'uart:|leds:|card: (enable|link)|gw: (DHCP ack|DNS|the rabbit opened)|ear [01]:|watchdog|halted' "$OUT/run1.log"
cat "$OUT/warren.log"

L="$OUT/run1.log"
need "$L" 'uart: L: run' "the loader did not hand over"
need "$L" 'uart: nabv1 native 30' "no boot banner"
need "$L" 'uart: flash: AT45DB081B' "the serial flash was not recognised"
need "$L" 'uart: config: new page; config: boot #1' "the configuration page was not written"
need "$OUT/run2.log" 'uart: config: boot #2' "the boot counter did not survive the run"
need "$L" 'leds: ' "the LEDs were never set"
need "$L" 'uart: card: mac 0019db00beef' "the card's MAC was not read"
need "$L" 'card: enable, port type 1 (BSS), SSID "Terrier"' "the card was not enabled on the network"
need "$L" 'uart: card: link 1 up' "the link was not seen"
need "$L" 'gw: DHCP acknowledge' "no DHCP lease"
need "$L" 'uart: dhcp: 10.0.0.42 mask 255.255.255.0 gw 10.0.0.1 dns 10.0.0.1' "the lease was not taken"
need "$L" 'gw: DNS p.nabaztag.com' "no DNS query"
need "$L" 'uart: dns: p.nabaztag.com -> 10.0.0.1' "the DNS answer was not taken"
need "$OUT/warren.log" 'GET /vl/FR/p3.jsp?sn=0019DB00BEEF&v=30&st=00&tc=00000000&vl=' "not the poll"
need "$OUT/warren.log" 'GET /vl/FR/p3.jsp?sn=0019DB00BEEF&v=30&st=01&tc=00000000&vl=' "no st=01 after the first poll"
[ "$(grep -c 'st=00' "$OUT/warren.log")" -le 2 ] || fail "st=00 on more than the first poll of each start"
need "$L" 'uart: http: HTTP/1.0 200 OK' "the answer's status line was not read"
need "$L" 'uart: poll interval 1' "the answer's frame 03 was not parsed"
need "$L" 'uart: poll 3 done' "fewer than three polls answered"
need "$L" 'uart: ear 0 done at' "ear 0 did not move"
need "$L" 'uart: ear 1 done at' "ear 1 did not move"
# Frame 11 with every answer, left 3 and right 5 (ear 1 left, ear 0 right):
# after the demo's two holes each, both end where the Terrier keeps them.
need "$L" 'uart: ear 0 done at 5' "frame 11 did not take the right ear (0) to 5"
need "$L" 'uart: ear 1 done at 3' "frame 11 did not take the left ear (1) to 3"
# Frame 12: the LEDs as ordered (top R, then G, B, R+G, R+G+B), then back.
need "$L" 'uart: leds: ordered 855e' "frame 12 did not light the LEDs as ordered"
# Frame 13: the sound fetched over HTTP into the flash, then played.
need "$OUT/warren.log" 'GET /vl/v1/snd?sn=0019DB00BEEF&i=01 HTTP/1.0' "frame 13 did not fetch the sound"
need "$L" 'uart: sound: in, playing' "the fetched sound was not played"
need "$L" 'uart: oki: done' "the OKI did not finish the fetched sound"
# Frame 14: asleep, the LEDs off; frame 15: the settings, taken at the next boot.
need "$L" 'uart: leds: ordered 0000' "frame 14 did not put the LEDs out"
need "$L" 'uart: config: set, network Terrier, server p.nabaztag.com (next boot)' "frame 15 was not written"
need "$OUT/run2.log" 'uart: config: network Terrier, server p.nabaztag.com' "the next boot did not take frame 15's settings"
# The button and the ears by hand, as Violet's sd=.
need "$OUT/warren.log" '&sd=0003&vl=' "a click was not reported (sd=0003)"
need "$OUT/warren.log" '&sd=0004&vl=' "a long press was not reported (sd=0004)"
need "$OUT/warren.log" '&sd=8903&vl=' "the ears turned by hand were not reported (sd=8903: right 9, left 3)"
# At rest: the bottom LED alone, breathing (its dot correction moving), the
# rest dark (Lazare, 2026-11-25: the network's green stayed lit).
[ "$(grep -c 'leds dc:' "$L")" -gt 20 ] || fail "the bottom LED does not breathe at rest"
# Connected: the four belly and base LEDs green, the left one too (Lazare,
# 2026-11-26: it stayed dark), before the rest.
need "$L" 'leds: .....#..#..#..#.' "the connection's progress does not light the four lower LEDs"
# Frame 16 (--rest 6): the base breathes cyan, the owner's colour.
need "$L" 'uart: leds: rest 06' "frame 16 did not set the base's colour"
need "$L" 'leds: .............##.' "at rest, not the bottom LED alone in the colour of frame 16"
# The sound button (a push button on AN0, 0 at rest): normal at first
# (vl=01), loud after one press at 50 s (vl=02), as Lazare's reads.
need "$OUT/warren.log" '&vl=01' "the sound state is not reported as normal at first (vl=01)"
need "$L" 'uart: input: sound 2' "a press of the sound button did not move to loud"
need "$OUT/warren.log" '&vl=02' "the sound state is not reported as loud after the press (vl=02)"
need "$L" 'ear 1: motor stopped' "the emulator did not see ear 1 stop"
grep -q 'watchdog:' "$L" && fail "the watchdog fired"
grep -q 'halted' "$L" && fail "the PIC halted"
grep -q 'unknown opcode' "$L" && fail "an opcode the emulator does not know"

FIRST=$(grep -m1 'gw: the rabbit opened' "$L" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/')
echo "PASS v1-native: boot, LEDs, card joined, DHCP, DNS, 3 polls answered, ears moved, watchdog quiet;"
echo "  first poll at ${FIRST} ms; $(tr '\n' ';' < "$OUT/size.txt") ($OUT)"

need "$OUT/run3.log" 'uart: card: report in EEPROM 0x40' "no card report without an access point"
python3 ../v1-icsp/card-report.py --log "$OUT/run3.log" | tee "$OUT/card-report.txt"
grep -q "0 info frame(s)" "$OUT/card-report.txt" || fail "the card report is not the one of a card that never heard a link"
! grep -q 'card: report' "$L" || fail "a card report on a run that associated"
# The card ignores a strobe held too short (Lazare, 2026-11-25): none here.
! grep -q 'strobe too short' "$L" "$OUT/run3.log" || fail "a card strobe too short for the real card"
python3 ../v1-icsp/card-report.py --log "$L" | tee "$OUT/net-report.txt"
grep -q "furthest ready (HTTP)" "$OUT/net-report.txt" && grep -q "DHCP: address 10.0.0.42" "$OUT/net-report.txt" \
  || fail "the network report does not show the way to the Terrier"
echo "PASS v1-native card report"
