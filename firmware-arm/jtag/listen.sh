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
# A firmware with ccmp_replay_count (mode lean-reclaim, 1.1.0.0) adds three
# fields at the end of the line, so everything before them reads as before
# and the line is ~110 characters:
#   ... F0/1c N1a2 p0/0/0 V0/2000012f
# N protected CCMP data frames examined; p frames a replay check would have
# refused, unicast / group / retransmitted (Retry bit); V the receive
# descriptor's Eiv / Iv words for the last frame examined. If the radio
# hands the CCMP header over in those words as Linux's rt73usb says, the
# packet number is Eiv<<16 | Iv&0xffff and the top two bits of Iv are the
# key id. Nothing is refused: this is the measurement that says whether
# ccmp_replay_enforce may be turned on. `./listen.sh | ./ccmp-pn.py` reads
# it: N rising, p staying 0/0/0 and the packet number rising = safe; p
# rising as fast as N = the words are not a packet number, do not enforce.
#
# Reading a rabbit stuck in AUTH (W2) with it: a rising, r 0, h rising,
# F failed rising with a = our frames leave and nobody acknowledges them
# (wrong channel or rate, or an AP ignoring this MAC); a rising, r 0,
# h rising, F acked rising = the AP acknowledges and does not answer;
# h 0 = we are deaf or on the wrong channel; Y rising = the stick refuses
# the frames; B 0 = the bootloader is driving (its boot_loop rotates through
# wifiscans every 20 s, which is the "alternating BSSIDs" of 2026-11-08).
cd "$(dirname "$0")"
# Stopping this script -- Ctrl-C, `timeout`, a dropped ssh -- has to stop
# openocd as well, and only SIGKILL does: openocd acts on TERM and INT in its
# server loop, and dcc-listen.tcl never hands control back to it. Left
# behind, openocd keeps the GPIO lines with nobody reading its output,
# blocks on the full pipe, and the next listen.sh fails with "Error
# requesting gpio line tdo" (2026-11-10: one had sat there for five hours,
# and `timeout` could not stop another). The listener never halts the core,
# so killing it leaves the rabbit running.
openocd -f nabaztag-pi-listen.cfg -f dcc-listen.tcl "$@" > >(grep --line-buffered -v "^Info :") 2>&1 &
ocd=$!
trap 'trap - INT TERM HUP EXIT; kill -KILL $ocd 2>/dev/null' INT TERM HUP EXIT
wait $ocd
