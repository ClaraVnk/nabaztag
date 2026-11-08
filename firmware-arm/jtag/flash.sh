#!/usr/bin/env bash
# Write a Nab.bin to the rabbit over JTAG, with progress you can watch.
#
#   ./flash.sh Nab.bin            # erase + program + read back + compare
#   tail -f /tmp/flash-progress.log   (from another shell)
#
# The first pass on a healthy rabbit usually dies partway through the erase
# ("not halted" / "failed to write memory"); the second completes. So this
# relaunches up to three times on its own instead of making a person watch
# for the failure, and it keeps every line OpenOCD prints in the log --
# mkfwprog reports every thousand words, which is the progress bar.
# Exit 0 only when the read-back is byte-identical to the image.
set -u
img=${1:?Nab.bin}
log=${FLASH_LOG:-/tmp/flash-progress.log}
cd "$(dirname "$0")"
pgrep -x openocd >/dev/null && { echo "openocd already running, refusing"; exit 2; }
rm -f /tmp/readback.bin
./mkfwprog.py "$img" -o /tmp/fwprog.tcl | tee "$log"
for pass in 1 2 3; do
  echo "=== pass $pass: $(date +%H:%M:%S) ===" | tee -a "$log"
  openocd -f nabaztag-pi.cfg -f oki.tcl -f /tmp/fwprog.tcl -c shutdown 2>&1 \
    | grep --line-buffered -E "erasing sector|programmed|dumped|Error|failed" | tee -a "$log"
  if [ -f /tmp/readback.bin ] && cmp -s /tmp/readback.bin "$img"; then
    echo "=== byte-identical read-back after pass $pass: $(date +%H:%M:%S) ===" | tee -a "$log"
    sha256sum "$img" | tee -a "$log"
    exit 0
  fi
  rm -f /tmp/readback.bin
done
echo "=== three passes, no byte-identical read-back ===" | tee -a "$log"
exit 1
