#!/usr/bin/env bash
# The JTAG station, from a clone of this repository on a Raspberry Pi.
#
#   git clone https://github.com/ClaraVnk/nabaztag && cd nabaztag
#   ./firmware-arm/jtag/station.sh setup        # once: packages, GPIO group, bank check
#   ./firmware-arm/jtag/station.sh check        # wiring: JTAG answers, the flash reads back
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
    # Not oki_id: autoselect never engages on this part and returns the
    # array's own bytes (see oki.tcl). What proves the wiring is that OpenOCD
    # finds the ARM7TDMI's IDCODE (it refuses otherwise) and that the word at
    # the reset vector reads back as an instruction, or as erased flash.
    out=$(openocd -f nabaztag-pi.cfg -c "init; halt 500; echo \"reset vector: [read_memory 0x08000000 32 1]\"; resume; shutdown" 2>&1 || true)
    echo "$out" | grep -E "tap/device found|reset vector|Error" | sed 's/^Info : //'
    case "$out" in
      *"reset vector: 0xe59ff018"*) echo "OK: JTAG answers and the flash holds a firmware (LDR PC at the reset vector)." ;;
      *"reset vector: 0xffffffff"*) echo "OK: JTAG answers; the flash is blank at the reset vector (a rabbit to rescue)." ;;
      *"reset vector: "*)           echo "JTAG answers, but the reset vector is unexpected: compare with a dump before writing." ;;
      *) echo "No answer: check the five jumpers (wiring.svg), the rabbit's own power, then run again --"
         echo "the first attach to a running rabbit sometimes times out." ;;
    esac ;;
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
