#!/usr/bin/env bash
# Live console from a running rabbit, over the JTAG wires, without halting it.
# Needs a firmware built with the dcc_console step (lean, from 2026-11-08).
cd "$(dirname "$0")"
exec openocd -f nabaztag-pi-listen.cfg "$@"
