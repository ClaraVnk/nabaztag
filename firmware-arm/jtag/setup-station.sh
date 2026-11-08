#!/usr/bin/env bash
# Provision a Raspberry Pi as the Nabaztag JTAG station.
#
#   scp -r jtag pi-host:~/nabaztag-flash && ssh -t pi-host 'bash ~/nabaztag-flash/setup-station.sh'
#
# Everything this needs is in Debian. The station is deliberately boring: no
# probe, no patched OpenOCD, no vendor driver. The previous station was a Pi 4
# whose SD card carried the only copy of the flash sequence, and when the card
# went, so did the ability to recover a rabbit at all. Hence this script, and
# hence oki.tcl living in the repository next to it.
set -euo pipefail

echo "== packages"
sudo apt-get update
sudo apt-get -y install openocd gpiod

echo
echo "== versions"
openocd --version 2>&1 | head -1
echo "gpiod: $(gpiodetect | head -1)"

echo
echo "== GPIO access"
if ! id -nG | tr ' ' '\n' | grep -qx gpio; then
  sudo usermod -aG gpio "$USER"
  echo "added $USER to the gpio group — log out and back in before flashing"
else
  echo "$USER is already in the gpio group"
fi

# The config says `-chip 0`, which is the SoC's own bank on every Pi that has
# a 40-pin header. A Pi 5 puts it elsewhere; say so rather than let OpenOCD
# fail with something unhelpful about a gpio line.
chip0=$(cat /sys/bus/gpio/devices/gpiochip0/label 2>/dev/null || echo unknown)
echo "gpiochip0 is $chip0"
case "$chip0" in
  pinctrl-bcm*) echo "  good — nabaztag-pi.cfg targets chip 0 as written" ;;
  *) echo "  !! not a BCM bank: find the right one with gpiodetect and change"
     echo "     the four 'adapter gpio' lines in nabaztag-pi.cfg" ;;
esac

echo
echo "== next"
echo "  wire it up (see wiring.svg), power the rabbit from its own adapter, then:"
echo "    ./station.sh check   (from this folder)"
echo "  'reset vector: 0xe59ff018' means the five jumpers are right."
