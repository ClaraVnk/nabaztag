#!/usr/bin/env bash
# Write a Nab.bin to the rabbit over JTAG, with progress you can watch.
#
#   ./flash.sh Nab.bin            # erase + program + read back + compare
#   tail -f /tmp/flash-progress.log   (from another shell)
#
# The JTAG link does not last a whole image: the first pass on a healthy
# rabbit usually dies partway through the erase, and on some rabbits later
# passes die a few thousand words into the write ("FLACON never reported the
# command complete", "timeout waiting for SYSCOMP & DBGACK"). So after a
# pass this reads the flash back first and does only the work left
# (mkfwprog.py --against): a sector already right is skipped, one with its
# missing words still erased is completed without an erase. Every pass moves
# forward instead of starting again at word 0, and so does running the same
# command again after an interruption. Up to FLASH_PASSES
# (default 8). Every line OpenOCD prints is kept in the log -- mkfwprog
# reports every thousand words, which is the progress bar.
# Exit 0 only when the read-back is byte-identical to the image.
set -u
img=${1:?Nab.bin}
log=${FLASH_LOG:-/tmp/flash-progress.log}
cd "$(dirname "$0")"
pgrep -x openocd >/dev/null && { echo "openocd already running, refusing"; exit 2; }
rm -f /tmp/readback.bin /tmp/fwprog.tcl
# No script, no OpenOCD: attaching halts the core, and a run that then fails
# on a missing script leaves the rabbit frozen for nothing.
./mkfwprog.py "$img" -o /tmp/fwprog.tcl 2>&1 | tee "$log"
if [ "${PIPESTATUS[0]}" -ne 0 ] || [ ! -f /tmp/fwprog.tcl ]; then
  echo "=== nothing written: the image was refused (above); the rabbit is untouched ===" | tee -a "$log"
  exit 1
fi
len=$(wc -c < "$img" | tr -d ' ')
passes=${FLASH_PASSES:-8}
for pass in $(seq 1 "$passes"); do
  # What does the flash hold now? Read it, and plan only what is missing.
  rm -f /tmp/current.bin
  echo "=== reading the flash first: $(date +%H:%M:%S) ===" | tee -a "$log"
  openocd -f nabaztag-pi.cfg -f oki.tcl \
    -c "halt; oki_wd_off; oki_dump /tmp/current.bin 0x08000000 $(printf 0x%x "$len"); shutdown" 2>&1 \
    | grep --line-buffered -E "dumped|Error|failed" | tee -a "$log"
  # A read that fails is a pass spent, never a reason to write everything
  # blind: that would erase what an earlier run already put in place.
  if [ ! -f /tmp/current.bin ] || [ "$(wc -c < /tmp/current.bin | tr -d ' ')" -ne "$len" ]; then
    echo "=== could not read the flash; trying again ===" | tee -a "$log"
    continue
  fi
  if cmp -s /tmp/current.bin "$img"; then
    echo "=== the flash already holds this image: $(date +%H:%M:%S) ===" | tee -a "$log"
    sha256sum "$img" | tee -a "$log"
    exit 0
  fi
  ./mkfwprog.py "$img" --against /tmp/current.bin -o /tmp/fwprog.tcl 2>&1 | tee -a "$log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "=== could not plan the rest ===" | tee -a "$log"; exit 1; }
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
echo "=== $passes passes, no byte-identical read-back: run the same command again, it resumes ===" | tee -a "$log"
exit 1
