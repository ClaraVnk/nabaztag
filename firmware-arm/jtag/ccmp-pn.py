#!/usr/bin/env python3
"""Read ccmp_replay_count's fields off the telemetry line and say what they mean.

  ./listen.sh | ./ccmp-pn.py

A firmware built with ccmp_replay_count ends its status line with
`N<seen> p<unicast>/<group>/<retry> V<Eiv>/<Iv>` (hex): protected CCMP data
frames examined, frames whose packet number did not increase (what a replay
check would refuse), and the receive descriptor's two words for the last
frame. Nothing is refused by that firmware. This prints, per line, the
packet number those words would be if the RT2571W hands the CCMP header
over as Linux's rt73usb says (PN = Eiv<<16 | Iv&0xffff, key id = Iv>>30),
and at the end whether ccmp_replay_enforce is safe to turn on:

  yes   frames were examined, none counted as a replay, and the packet
        number of the pairwise key only went up between lines
  no    frames were counted as replays: either someone replays, or -- far
        more likely if it is most of them -- the words are not a packet
        number on this hardware. The V column shows what they are.

Lines without the three fields (an older firmware, OpenOCD's own output)
are passed through untouched.
"""
import re
import sys

FIELDS = re.compile(r" N([0-9a-f]+) p([0-9a-f]+)/([0-9a-f]+)/([0-9a-f]+) V([0-9a-f]+)/([0-9a-f]+)\s*$")


def main() -> int:
    first = last = None
    last_pn = None
    backwards = 0
    for line in sys.stdin:
        line = line.rstrip("\n")
        m = FIELDS.search(line)
        if not m:
            print(line, flush=True)
            continue
        seen, uc, gr, retry, eiv, iv = (int(x, 16) for x in m.groups())
        pn = (eiv << 16) | (iv & 0xFFFF)
        keyid = iv >> 30
        note = ""
        # Only the pairwise key (id 0) is one sequence; a group frame in
        # between has its own numbers.
        if keyid == 0:
            if last_pn is not None and pn < last_pn:
                backwards += 1
                note = "  <- lower than before (a new pairwise key, or not a PN)"
            last_pn = pn
        print(f"{line}\n    ccmp: {seen} examined, would refuse {uc} unicast / {gr} group, "
              f"{retry} retransmitted; last frame key id {keyid}, PN {pn}{note}", flush=True)
        if first is None:
            first = (seen, uc, gr)
        last = (seen, uc, gr, retry)
    if last is None:
        print("no line carried N/p/V: is this firmware built with ccmp_replay_count?")
        return 2
    examined = (last[0] - first[0]) & 0xFFFF
    replays = ((last[1] - first[1]) & 0xFFFF) + ((last[2] - first[2]) & 0xFFFF)
    print(f"\n{examined} frames examined while listening, {replays} counted as replays, "
          f"{last[3]} retransmissions in all, pairwise PN went down {backwards} time(s)")
    if examined == 0:
        print("verdict: nothing examined -- no CCMP traffic, or the radio reports another cipher")
        return 2
    if replays == 0 and last[1] == 0 and last[2] == 0:
        print("verdict: the words behave as a packet number; ccmp_replay_enforce is safe to try")
        return 0
    print("verdict: do NOT enforce -- frames would have been refused; read the V column")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
