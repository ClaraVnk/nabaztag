#!/usr/bin/env bash
# Live console from a running rabbit, over the JTAG wires, without halting it.
# Needs a firmware built with the dcc_telemetry step (mode lean-wpa3-tel):
#   W5 A0 S0 C4 E2 X0 K1 D0 P0 T258 R3/0
# W ieee80211_state (0 idle 1 scan 2 auth 3 assoc 4 eapol 5 run), A authmode
# (0 open/PSK, 2 SAE), S SAE state, C SAE cache entries, E eapol_state,
# X USB receive errors, K rt2501_connected, D management frames refused by
# 802.11w, P deauth/disassoc carrying the Protected bit, T timeout (hex),
# R reset reason/count. One line every few seconds: one character per
# radio tick, offered only when the channel is free, so the rabbit never
# waits for this listener and runs the same with or without it.
cd "$(dirname "$0")"
exec openocd -f nabaztag-pi-listen.cfg "$@"
