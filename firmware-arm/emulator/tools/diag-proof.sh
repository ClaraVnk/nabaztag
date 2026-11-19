#!/usr/bin/env bash
# The boot's diagnostic line (wifi_diag + boot_diag, 2026-11-19): kept in
# flash when the warren does not answer, sent with the next boot request,
# cleared once a bytecode is accepted.
#
#   tools/diag-proof.sh <Nab.elf | boot.bc>
#
# Two boots on one flash (mkconf.py's config page, a static address):
#   1 noserver-refused: nothing answers at 10.0.0.1:8080. Byte 1024 of the
#     flash must then hold a line "<1|2> t<s> W... g.. b.. u.. D<sent>/<offers>/
#     <acks> I10.0.0.42 G10.0.0.1 N10.0.0.1".
#   then config-pages.sh on that flash: v.js's 14th value is the line, and
#     a.htm has the Diagnostic block;
#   2 noserver-warren, on the flash the first boot left: plain_warren serves
#     the bytecode; its log must show &dg= on bc.jsp, and byte 1024 must be
#     empty afterwards.
# The control: the same on an image without boot_diag shows no line and no
# &dg=. Same environment as noserver-proof.sh (RUNTIME, STEPS, TAG, NO_BUILD);
# STEPS should come from tools/emu-steps.py for the mode under test.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$HERE/run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}
IN=${1:?usage: diag-proof.sh <Nab.elf | boot.bc>}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
case "$IN" in
  *.bc) cp "$IN" "$WORK/boot.bc" ;;
  *) python3 "$HERE/tools/bootbc.py" "$IN" -o "$WORK/boot.bc" || exit 1 ;;
esac
if [ -z "${NO_BUILD:-}" ]; then
  echo "== building the emulator image (nabgcc mods: $STEPS)"
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . ) >/dev/null || exit 1
fi
python3 "$HERE/mkconf.py" --ssid Terrier --security open --server 10.0.0.1:8080/vl \
  --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 --rng-seed fixed -o "$WORK/conf.eeprom" >/dev/null
run() {   # scenario [warren]
  cp "$HERE/scenarios/vm/$1.txt" "$WORK/scenario.txt"
  cp "$WORK/boot.bc" "$WORK/boot.bc.run"
  local warren=""
  [ -n "${2:-}" ] && warren="python3 tools/plain_warren.py --port 8080 --bytecode /build/bootcode.hybrid > /work/warren.log 2>&1 & sleep 1;"
  "$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
    "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc.run vm >/dev/null \
     && $warren ./rabbit-vm /work/scenario.txt --eeprom /work/conf.eeprom $([ -n "$warren" ] && echo --warren 127.0.0.1)" \
    > "$WORK/$1.log" 2>&1
}
line() { python3 - "$WORK/conf.eeprom" <<'PY'
import sys
b = open(sys.argv[1], "rb").read()[1024:1024 + 160]
print(b.split(b"\0")[0].split(b"\xff")[0].decode("latin-1"))
PY
}
ok=1
run noserver-refused
l=$(line)
echo "flash after the failed boot: ${l:-(empty)}"
echo "       flash writes: $(grep -o 'eeprom: the rabbit wrote [0-9]* bytes at [0-9]*' "$WORK/noserver-refused.log" | sort | uniq -c | tr '\n' ';')"
echo "       last lines: $(tail -3 "$WORK/noserver-refused.log" | tr '\n' '|' | cut -c1-200)"
if [[ "$l" =~ ^[12]\ t[0-9]+\ W.*\ g[0-9a-f]+.*\ b[0-9a-f]+\ u[0-9a-f]+\ D[0-9]+/[0-9]+/[0-9]+\ I10\.0\.0\.42\ G10\.0\.0\.1\ N10\.0\.0\.1$ ]]; then
  echo "PASS   the failure left its line in flash"
else echo "FAIL   no diagnostic line, or not its shape"; ok=0; fi
# The setup page shows it (gzip_setup with boot_diag: v.js's 14th value).
cp "$WORK/conf.eeprom" "$WORK/failed.eeprom"
if NO_BUILD=1 TAG="$TAG" EEPROM="$WORK/failed.eeprom" EXPECT_DIAG="$l" "$HERE/tools/config-pages.sh" "$IN" > "$WORK/pages.out" 2>&1; then
  echo "PASS   the setup page shows it"
else echo "FAIL   the setup page: $(grep -E 'FAIL' "$WORK/pages.out" | head -3 | tr '\n' '|')"; ok=0; fi
run noserver-warren warren
if grep "bc.jsp" "$WORK/warren.log" 2>/dev/null | grep -qE '&dg=[12]_t'; then
  echo "PASS   the next boot request carried it: $(grep -o '&dg=[^ &"]*' "$WORK/warren.log" | head -1 | cut -c1-90)"
else echo "FAIL   no &dg= on bc.jsp; the warren saw: $(grep -E 'jsp' "$WORK/warren.log" | cut -c1-160 | tr '\n' '|')"; ok=0; fi
l=$(line)
if [ -z "$l" ]; then echo "PASS   cleared once the bytecode was accepted"
else echo "FAIL   still in flash after a good boot: $l"; ok=0; fi
[ $ok = 1 ] && echo "== diag-proof: every check passed" || { echo "== diag-proof: FAILED"; exit 1; }
