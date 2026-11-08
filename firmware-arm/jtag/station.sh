#!/usr/bin/env bash
# The JTAG station, from a clone of this repository on a Raspberry Pi.
#
#   git clone https://github.com/ClaraVnk/nabaztag && cd nabaztag
#   ./firmware-arm/jtag/station.sh setup        # once: packages, GPIO group, bank check
#   ./firmware-arm/jtag/station.sh check        # wiring: the flash chip answers
#   ./firmware-arm/jtag/station.sh flash Nab.bin
#   ./firmware-arm/jtag/station.sh listen       # live status line, no halt
#   ./firmware-arm/jtag/station.sh dump out.bin # read the whole flash back
#
# Wiring is in wiring.svg next to this file; the rabbit runs on its own
# mains adapter. Every command but `setup` and `listen` halts the core for a
# moment and resumes it; a rabbit halted during association may lose its
# Wi-Fi, so prefer `listen` on a rabbit you are only watching.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cmd=${1:-help}
[ $# -gt 0 ] && shift

need_openocd() {
  command -v openocd >/dev/null || { echo "openocd is missing: run '$0 setup' first"; exit 2; }
  if pgrep -x openocd >/dev/null; then
    echo "an openocd is already running (a 'listen'?) -- stop it first: pkill -x openocd"; exit 2
  fi
}

case "$cmd" in
  setup)
    exec bash "$here/setup-station.sh" ;;
  check)
    need_openocd
    cd "$here"
    # oki_id prints the manufacturer and device; resume leaves the rabbit
    # running (attaching halts it).
    openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_id; resume; shutdown" 2>&1 \
      | grep -vE "^(Info|Warn) |^Open On-Chip|^Licensed|^For bug|^\s*http|^$" || true
    echo "expected: manufacturer 0x98, device 0xcd. If the first try says 'not halted', run it again." ;;
  flash)
    [ $# -eq 1 ] || { echo "usage: $0 flash <Nab.bin>"; exit 2; }
    need_openocd
    img=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
    [ -f "$img" ] || { echo "no such file: $1"; exit 2; }
    echo "writing $img -- about 15 minutes; progress in /tmp/flash-progress.log"
    exec "$here/flash.sh" "$img" ;;
  listen)
    need_openocd
    exec "$here/listen.sh" ;;
  dump)
    [ $# -eq 1 ] || { echo "usage: $0 dump <out.bin>"; exit 2; }
    need_openocd
    out=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
    cd "$here"
    openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump $out; resume; shutdown" 2>&1 | tail -3
    ls -l "$out" ;;
  help|*)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
