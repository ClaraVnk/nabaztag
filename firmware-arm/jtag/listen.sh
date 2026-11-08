#!/usr/bin/env bash
# Live console from a running rabbit, over the JTAG wires, without halting it.
# Needs a firmware built with the dcc_telemetry step (mode lean-wpa3-tel or
# lean-wpa3-slim-tel):
#   W5 A0 S0 C4 E2 X0 K1 D0 P0 T258 R3/0 a1 r1/0 h0 q0 L0 U3 Y0 B1 F0/1c
# W ieee80211_state (0 idle 1 scan 2 auth 3 assoc 4 eapol 5 run), A authmode
# (0 open/PSK, 2 SAE), S SAE state, C SAE cache entries, E eapol_state,
# X USB receive errors, K rt2501_connected, D management frames refused by
# 802.11w, P deauth/disassoc carrying the Protected bit, T timeout (hex),
# R reset reason/count. Then, since 2026-11-08 (link_liveness): a open-system
# auth frames sent, r auth frames received to us / the last status word,
# h beacons from the target BSSID heard while in AUTH/ASSOC, q null-data
# probes sent, L associations given up by the watchdog, U seconds since the
# last unicast data frame from the AP, Y bulk-out URBs failed, B bytecode
# loads (0 = the bootloader is still driving), F frames the ASIC gave up on
# unacknowledged / frames acknowledged (STA_CSR4/5). All hex. One line every
# ~85 radio ticks: one character per tick, offered only when the channel is
# free, so the rabbit never waits for this listener and runs the same with
# or without it.
#
# Reading a rabbit stuck in AUTH (W2) with it: a rising, r 0, h rising,
# F failed rising with a = our frames leave and nobody acknowledges them
# (wrong channel or rate, or an AP ignoring this MAC); a rising, r 0,
# h rising, F acked rising = the AP acknowledges and does not answer;
# h 0 = we are deaf or on the wrong channel; Y rising = the stick refuses
# the frames; B 0 = the bootloader is driving (its boot_loop rotates through
# wifiscans every 20 s, which is the "alternating BSSIDs" of 2026-11-08).
cd "$(dirname "$0")"
exec openocd -f nabaztag-pi-listen.cfg "$@"
