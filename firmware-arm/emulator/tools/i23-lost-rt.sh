#!/usr/bin/env bash
# Drive issue #23 finding 5 from outside, in step with a realtime run.
#
#   tools/i23-lost-rt.sh 127.0.0.1:8080 &
#   ./rabbit-vm scenarios/vm/i23-lost-rt.txt --eeprom ... --warren ...
#
# The scenario drops the session at 30 s. This asks the add-on to start the
# rabbit's microphone at 20 s, which is before the drop, and then stays out of
# the way: the RT that would stop the capture is the one the drop loses, and
# the next sound is the connect jingle the add-on pushes by itself when the
# rabbit comes back. Both clocks are wall clocks here, which is what
# `realtime` in the scenario is for.
set -u
API=${1:-127.0.0.1:8080}

sleep 20
echo "== $(date +%H:%M:%S) microphone on (RS)"
curl -s -m 5 "http://$API/api/mic?on=1" || echo "   the control API did not answer"
# From here on nothing is sent: at 30 s the session goes away and the RT the
# add-on would send on its own goes with it.
