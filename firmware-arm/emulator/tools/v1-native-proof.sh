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
grep -E '^(flash|ram) ' "$OUT/build.log" | tee "$OUT/size.txt"
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null

mkdir -p "$OUT/fw"
cp "$NATIVE/out/nabv1native.hex" "$OUT/fw/"
python3 tools/v1_native_warren.py "$PORT" > "$OUT/warren.log" 2>&1 &
WARREN=$!
trap 'kill $WARREN 2>/dev/null' EXIT
sleep 1

emu() {
  podman run --rm --network host -v "$OUT/fw":/img:Z nabaztag-v1-native \
    --hex /img/nabv1native.hex --flash-chip 081 --flash /img/v1native.flash \
    --warren 127.0.0.1 --port80 "$PORT" "$@"
}
emu --seconds 40 > "$OUT/run1.log" 2>&1 || true
emu --seconds 4 > "$OUT/run2.log" 2>&1 || true
sleep 1
grep -E 'uart:|leds:|card: (enable|link)|gw: (DHCP ack|DNS|the rabbit opened)|ear [01]:|watchdog|halted' "$OUT/run1.log"
cat "$OUT/warren.log"

L="$OUT/run1.log"
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
need "$OUT/warren.log" 'GET /vl/FR/p3.jsp?sn=0019DB00BEEF&v=30&st=00&tc=00000000 HTTP/1.0' "not the poll"
need "$L" 'uart: http: HTTP/1.0 200 OK' "the answer's status line was not read"
need "$L" 'uart: frame 03: poll interval 1' "the answer's frame 03 was not parsed"
need "$L" 'uart: poll 3 done' "fewer than three polls answered"
need "$L" 'uart: ear 0 done in' "ear 0 did not move"
need "$L" 'uart: ear 1 done in' "ear 1 did not move"
need "$L" 'ear 1: motor stopped' "the emulator did not see ear 1 stop"
grep -q 'watchdog:' "$L" && fail "the watchdog fired"
grep -q 'halted' "$L" && fail "the PIC halted"
grep -q 'unknown opcode' "$L" && fail "an opcode the emulator does not know"

FIRST=$(grep -m1 'gw: the rabbit opened' "$L" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/')
echo "PASS v1-native: boot, LEDs, card joined, DHCP, DNS, 3 polls answered, ears moved, watchdog quiet;"
echo "  first poll at ${FIRST} ms; $(tr '\n' ';' < "$OUT/size.txt") ($OUT)"
