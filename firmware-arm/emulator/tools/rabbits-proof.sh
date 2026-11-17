#!/bin/sh
# The three rabbits against one Terrier, in emulation (Clara, 2026-11-16: a
# new feature is proven on the v1, the v2 and the v3 before it ships).
#
#   tools/rabbits-proof.sh --terrier <terrier checkout> \
#       [--v2 "<xmpp-tls-proof.sh arguments>"]
#
#   v1  Violet's own 2005 firmware (v1/, tools/v1-proof.sh --terrier)
#   v2  the tag's whole-rabbit VM (tools/xmpp-tls-proof.sh); its arguments
#       name the Terrier image, the bytecode, the emulator image and the ELF,
#       e.g. --v2 "--terrier terrier-proof --bytecode <rt.hybrid> --emulator
#       nabaztag-emulator --elf <Nab.elf> --out /tmp/v2 --scenario
#       scenarios/vm/xmpp-ears.txt"
#   v3  the Karotz (karotz/, tools/karotz-proof.sh --terrier)
#
# From firmware-arm/emulator, on the dev VM. Prints one verdict per rabbit;
# the exit status is the number that failed. A rabbit without its inputs is
# reported as NOT RUN, never as passed.
set -u
cd "$(dirname "$0")/.."
TERRIER= ; V2=
while [ $# -gt 0 ]; do
  case "$1" in
    --terrier) TERRIER=$2; shift 2 ;;
    --v2) V2=$2; shift 2 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done
[ -n "$TERRIER" ] || { echo "--terrier <checkout> is required"; exit 2; }
OUT=$(mktemp -d)
failed=0
verdict() { printf '%-3s %s\n' "$1" "$2" >> "$OUT/verdicts"; }

if tools/v1-proof.sh --terrier "$TERRIER" > "$OUT/v1.log" 2>&1; then verdict v1 PASS
else verdict v1 "FAIL ($OUT/v1.log)"; failed=$((failed + 1)); fi

if [ -n "$V2" ]; then
  # shellcheck disable=SC2086
  if tools/xmpp-tls-proof.sh $V2 > "$OUT/v2.log" 2>&1; then verdict v2 PASS
  else verdict v2 "FAIL ($OUT/v2.log)"; failed=$((failed + 1)); fi
else
  verdict v2 "NOT RUN: give --v2 with xmpp-tls-proof.sh's inputs"
fi

if WORK="$OUT/v3" tools/karotz-proof.sh --terrier "$TERRIER" > "$OUT/v3.log" 2>&1; then verdict v3 PASS
else verdict v3 "FAIL ($OUT/v3.log)"; failed=$((failed + 1)); fi

cat "$OUT/verdicts"
exit "$failed"
