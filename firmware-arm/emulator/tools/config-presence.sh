#!/usr/bin/env bash
# Who may change the rabbit's server from the setup page (review S-6).
#
#   tools/config-presence.sh <Nab.elf | boot.bc>
#
# Lifts the bootloader out of the image (bootbc.py) and boots it in config
# mode three times, each on a copy of a config page that names the factory
# server, a phone joined:
#   config-server-nohead  a stranger sends a new server on /s.css, then on
#                         the save: no button -> error page, nothing saved,
#                         the rabbit stays in config mode;
#   config-server-head    the owner sends a new server while holding the
#                         head: saved, the rabbit joins the network;
#   config-wifi-nohead    the ordinary save, the server the page offered,
#                         no button: saved, the rabbit joins the network.
# The server field of each page is printed afterwards. Give 1.2.0.3's base
# image (before config_presence) first as the control: its nohead run takes
# the server and fails `expect-not RUN`. Same environment as config-pages.sh:
# RUNTIME, STEPS, TAG, NO_BUILD; EXPECT_SERVER is the server the page offers
# (terrier.cyberloutre.fr/vl unless the build says otherwise).
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$(dirname "$0")/../run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}
OFFERED=${EXPECT_SERVER:-terrier.cyberloutre.fr/vl}

if [ $# -lt 1 ]; then sed -n '2,22p' "$0"; exit 2; fi
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

# A page that already names the offered server: what a rabbit set up once
# holds, and what /v.js shows a phone.
python3 "$HERE/mkconf.py" --ssid Terrier --security open --server "$OFFERED" -o "$WORK/base.eeprom" 2>/dev/null \
  || python3 "$HERE/mkconf.py" --ssid Terrier --security open -o "$WORK/base.eeprom"

ok=1
for s in config-server-nohead config-server-head config-wifi-nohead; do
  mkdir -p "$WORK/$s"
  sed "s|<OFFERED>|$OFFERED|" "$HERE/scenarios/vm/$s.txt" > "$WORK/$s/scenario.txt"
  cp "$WORK/base.eeprom" "$WORK/$s/conf.eeprom"
  cp "$WORK/boot.bc" "$WORK/$s/boot.bc"
  mkdir -p "$WORK/$s/out"
  "$RUNTIME" run --rm -v "$WORK/$s:/work:Z" "$TAG" sh -c \
    "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
     && ./rabbit-vm /work/scenario.txt --eeprom /work/conf.eeprom --save-dir /work/out" \
    > "$WORK/$s/run.log" 2>&1 && status=PASS || status=FAIL
  server=$(head -c 41 "$WORK/$s/conf.eeprom" | tr -d '\000-')
  # The phone keeps each answer under the path it asked for, query included.
  answer=$(cat "$WORK/$s"/out/b.htm* 2>/dev/null | grep -a -o -E 'Settings saved|Something went wrong' | head -1 || true)
  printf '%-6s %-22s server=%-28s save answered: %s\n' "$status" "$s" "${server:-(blank)}" "${answer:-(nothing)}"
  grep -E 'FAIL|the head button|button 2' "$WORK/$s/run.log" | head -4 | sed 's/^/       /'
  [ "$status" = PASS ] || ok=0
done
[ "$ok" = 1 ]
