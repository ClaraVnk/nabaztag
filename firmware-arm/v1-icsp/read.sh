#!/bin/sh
# Read a Nabaztag v1's PIC over ICSP (J2) into an Intel hex file: program
# memory, configuration, EEPROM and user IDs. Read only: no -M (program),
# no -E (erase), no -W (the rabbit keeps its own 8 V adapter).
#
#   firmware-arm/v1-icsp/read.sh <out.hex> [device]
#
# device: PIC18LF6525 (Clara's 1.4b rabbit, device ID 0x0AE0, 48 KB) or
# PIC18LF6627 (Newbaztag's 2.0b, 96 KB). A wrong one is refused with the ID
# actually read ("Target Device ID (0xae0) is an Invalid Device ID"):
# that ID names the chip, in the device pack's edc:DeviceIDSector.
#
# MPLAB X 6.20 (macOS): 6.30 carries no PICkit 3 support, its tool list
# stays empty. The device pack (PIC18Fxxxx_DFP) is found only once MPLAB
# IPE has been opened once. 2026-11-24, Lazare: "Read successfully".
set -eu
OUT=${1:?usage: read.sh <out.hex> [device]}
DEV=${2:-18LF6525}
DEV=${DEV#PIC}   # ipecmd wants 18LF6525: "PIC18LF6525" became PICPIC18LF6525 (2026-11-24)
IPECMD=${IPECMD:-/Applications/microchip/mplabx/v6.20/mplab_platform/mplab_ipe/bin/ipecmd.sh}
case "$OUT" in /*) ;; *) OUT=$(pwd)/$OUT ;; esac
"$IPECMD" -P"$DEV" -TPPK3 -GF"$OUT"
