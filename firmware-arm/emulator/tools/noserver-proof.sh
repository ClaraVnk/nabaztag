#!/usr/bin/env bash
# What the belly says when the Wi-Fi is up and the warren is not
# (boot_noserver, 2026-11-19).
#
#   tools/noserver-proof.sh <Nab.elf | boot.bc>
#
# Lifts the bootloader out of the image (bootbc.py) and boots it three times
# on a config page that names a warren at 10.0.0.1:8080, a static address:
#   noserver-refused  no warren behind the gateway: every connection is
#                     refused. After kx.jsp and bc.jsp fail, the nose must be
#                     steady green and the four belly LEDs breathe purple
#                     (#vv00vv, many shades) for most of the time from 40 s on;
#   noserver-warren   tools/plain_warren.py answers: bc.jsp is served, the
#                     bootloader hands over, no purple shade ever shows;
#   noserver-noap     no access point: the Wi-Fi steps as before (amber),
#                     nothing green on the nose, nothing purple.
# The verdict reads the `led <name> = #rrggbb` lines of each transcript.
# Give the image before the step first, as the control: its refused run
# shows amber and green only and FAILs here. Same environment as
# config-pages.sh: RUNTIME, STEPS, TAG, NO_BUILD.
set -euo pipefail
RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$(dirname "$0")/../run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}
if [ $# -lt 1 ]; then sed -n '2,20p' "$0"; exit 2; fi
IMAGE=$1; shift
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
case "$IMAGE" in
  *.elf|*.ELF|*/Nab.elf) python3 "$HERE/tools/bootbc.py" "$IMAGE" -o "$WORK/boot.bc" ;;
  *) cp "$IMAGE" "$WORK/boot.bc" ;;
esac
if [ -z "${NO_BUILD:-}" ]; then
  echo "== building the emulator image (nabgcc mods: $STEPS)"
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . )
fi
# A rabbit set up once: the pool's seed stored, so adoption's kx.jsp is
# tried at once rather than after KX_WAIT.
python3 "$HERE/mkconf.py" --ssid Terrier --security open --server 10.0.0.1:8080/vl \
  --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 --rng-seed fixed -o "$WORK/base.eeprom" >/dev/null
ok=1
for s in noserver-refused noserver-warren noserver-noap; do
  mkdir -p "$WORK/$s"
  cp "$HERE/scenarios/vm/$s.txt" "$WORK/$s/scenario.txt"
  cp "$WORK/base.eeprom" "$WORK/$s/conf.eeprom"
  cp "$WORK/boot.bc" "$WORK/$s/boot.bc"
  warren=""
  if [ "$s" = noserver-warren ]; then
    warren="python3 tools/plain_warren.py --port 8080 --bytecode /build/bootcode.hybrid > /work/warren.log 2>&1 & sleep 1;"
  fi
  "$RUNTIME" run --rm -v "$WORK/$s:/work:Z" "$TAG" sh -c \
    "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
     && $warren ./rabbit-vm /work/scenario.txt --eeprom /work/conf.eeprom $([ -n "$warren" ] && echo --warren 127.0.0.1)" \
    > "$WORK/$s/run.log" 2>&1 && status=PASS || status=FAIL
  verdict=$(python3 - "$s" "$WORK/$s/run.log" "$WORK/$s/warren.log" <<'PY'
import re, sys
name, log, wlog = sys.argv[1:4]
T0 = 40000
last = {}; since = {}; purple_ms = {}; green_nose_ms = 0; shades = {}; nose_last = None; nose_since = 0
end = 0
for line in open(log, "rb"):
    m = re.match(rb"\[\s*(\d+) ms\] led (\w+) = #([0-9a-f]{6})", line)
    if not m:
        continue
    t, led, col = int(m.group(1)), m.group(2).decode(), m.group(3).decode()
    end = max(end, t)
    r, g, b = int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)
    breathing = g == 0 and r == b and 1 <= r <= 254
    if led != "top":
        if led in last and last[led] and t > T0:
            purple_ms[led] = purple_ms.get(led, 0) + t - max(since[led], T0)
        last[led] = breathing; since[led] = t
        if breathing:
            shades.setdefault(led, set()).add(col)
    else:
        if nose_last == "00ff00" and t > T0:
            green_nose_ms += t - max(nose_since, T0)
        nose_last = col; nose_since = t
for led in last:
    if last[led] and end > T0:
        purple_ms[led] = purple_ms.get(led, 0) + end - max(since[led], T0)
if nose_last == "00ff00" and end > T0:
    green_nose_ms += end - max(nose_since, T0)
span = max(end - T0, 1)
belly = ["bottom", "left", "middle", "right"]
pct = {l: 100 * purple_ms.get(l, 0) // span for l in belly}
nsh = {l: len(shades.get(l, ())) for l in belly}
served = 0
try:
    served = sum(1 for l in open(wlog) if "bc.jsp: 200" in l)
except OSError:
    pass
if name == "noserver-refused":
    good = all(pct[l] >= 50 and nsh[l] >= 8 for l in belly) and 100 * green_nose_ms // span >= 50
    why = f"purple {pct} % of the time from {T0 // 1000} s, {nsh} shades, nose green {100 * green_nose_ms // span} %"
elif name == "noserver-warren":
    good = served >= 1 and not any(shades.values()) and green_nose_ms == 0
    why = f"bc.jsp served {served}, purple shades {sum(nsh.values())}, nose green {green_nose_ms} ms"
else:
    good = not any(shades.values()) and green_nose_ms == 0 and nose_last is not None
    why = f"purple shades {sum(nsh.values())}, nose green {green_nose_ms} ms, nose last #{nose_last}"
print(("ok   " if good else "FAIL ") + why)
PY
)
  printf '%-6s %-18s %s\n' "$status" "$s" "$verdict"
  grep -E '^\[.*FAIL' "$WORK/$s/run.log" | head -3 | sed 's/^/       /'
  [ "$status" = PASS ] && [ "${verdict#ok}" != "$verdict" ] || ok=0
done
[ "$ok" = 1 ]
