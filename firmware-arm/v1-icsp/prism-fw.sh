#!/bin/sh
# Intersil's Prism 2.5 firmware for the v1's Wi-Fi card, fetched and checked
# (not kept in this repository: Intersil's, with no licence to redistribute).
#
#   firmware-arm/v1-icsp/prism-fw.sh [DIR]      default ~/.nabaztag/prism-fw
#
# Why (2026-11-24): Lazare's card (a BenQ PCMCIA, Prism 2.5, NIC 0x800C
# variant 0, read by wifi-probe.py FD0B) runs station firmware 1.4.2, below
# the 1.7.0 WPA needs. Primary 1.1.1 and station 1.7.4 go together (never
# the primary alone); both files list NIC 0x800C variant 0 in their own
# compatibility records (address FF200000), which prism2_srec checks.
#   pk010101.hex  primary 1.1.1           (flash, with sf010704.hex)
#   sf010704.hex  station 1.7.4, flash    (prism2_srec -f)
#   rf010704.hex  station 1.7.4, RAM      (prism2_srec -r: gone at power off)
# Two mirrors gave the same bytes on 2026-11-24; each file is checked here
# against those hashes, and a mirror that disagrees is skipped.
set -eu
DIR=${1:-$HOME/.nabaztag/prism-fw}
mkdir -p "$DIR"
MIRRORS="http://junsun.net/linux/intersil-prism/firmware/1.7.4 https://mirror.rqsall.com/misc/intersil-prism"
if command -v sha256sum > /dev/null; then sha() { sha256sum "$1" | cut -d' ' -f1; }
else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi
while read -r name want; do
    [ -n "$name" ] || continue
    if [ -f "$DIR/$name" ] && [ "$(sha "$DIR/$name")" = "$want" ]; then
        echo "ok    $name (already there)"; continue
    fi
    got=
    for m in $MIRRORS; do
        curl -fsS -m 60 -o "$DIR/$name.part" "$m/$name" || continue
        if [ "$(sha "$DIR/$name.part")" = "$want" ]; then
            mv "$DIR/$name.part" "$DIR/$name"; got=$m; break
        fi
        echo "bad   $name from $m (hash differs): skipped"
    done
    rm -f "$DIR/$name.part"
    [ -n "$got" ] || { echo "FAIL  $name: no mirror gave the expected bytes"; exit 1; }
    echo "ok    $name from $got"
done <<'EOF'
pk010101.hex 20638ee6180583488f769d95ed0b240b115bb8b8f6a72e2597dd72d58b4b7791
sf010704.hex 92e3101220003bed0caeebe42d0817e96db67b1541c62c7601e7772f1c6aa3e5
rf010704.hex e4a74e443da5a7dd8919722c901f2a80007191ceb8cc721b0c390119664a1ca6
EOF
echo "in $DIR"
