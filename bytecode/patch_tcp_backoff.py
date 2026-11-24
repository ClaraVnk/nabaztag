#!/usr/bin/env python3
"""A session that survives a few seconds of a bad link.

2026-11-24, Pinkyblack on a TP-Link mesh: its Wi-Fi stayed associated
(no link death), yet its session to the warren was redialled 82 times in a
day, median 9 minutes. The developer probe measured the link: 6 % of the
probes unanswered, answers up to 9.6 s late. The bytecode's own TCP
(tcp.mtl tcptime, run once a second from wifi.mtl) resent an unacknowledged
segment every second and closed the connection after ten: any gap of
eleven seconds killed the session, however healthy the link was otherwise.

Now the resends back off as a TCP's do -- 1, 2, 4, 8, 16, 32, 64 s after
the first -- and the connection is given up after TCP_GIVEUP_S, 90 s:
still before the 150 s with nothing read that declares the session dead
(patch_liveness.py), so a link that is really gone is still noticed. A
lossy link costs a slow answer, not a new session.

Proven first on the emulator (firmware-arm/emulator scenario
vm/xmpp-stall.txt: 15 s with nothing passing). Takes the sources
directory's tcp.mtl. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* tcp-backoff */"
OLD = ("\t\t\tset t.retryT = (1+t.retryT);\n"
       "\t\t\tif (t.retryT>10) then\n"
       "\t\t\t(\n"
       "\t\t\t\tremtcp t;\n"
       "\t\t\t\tTcpw \"TCP > 10 send retries => closing\";\n"
       "\t\t\t\tcall t.cbT [t TCPCLOSE nil];\n"
       "\t\t\t\tnil\n"
       "\t\t\t)\n"
       "\t\t\telse resendtcp t\n")
NEW = ("\t\t\tset t.retryT = (1+t.retryT);\n"
       "\t\t\tif (t.retryT>90) then\t" + MARKER + "\n"
       "\t\t\t(\n"
       "\t\t\t\tremtcp t;\n"
       "\t\t\t\tTcpw \"TCP: 90 s without an ack => closing\";\n"
       "\t\t\t\tcall t.cbT [t TCPCLOSE nil];\n"
       "\t\t\t\tnil\n"
       "\t\t\t)\n"
       "\t\t\t// resent at 1, 2, 4, 8, 16, 32 and 64 s: a power of two\n"
       "\t\t\telse if (t.retryT & (t.retryT-1))==0 then resendtcp t\n"
       "\t\t\telse 0\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    s = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in s else "\n"
    t = s.replace("\r\n", "\n")
    if MARKER in t:
        return False
    if t.count(OLD) != 1:
        sys.exit(f"FAIL {path}: tcptime's retry block is not the shape this patch expects")
    t = t.replace(OLD, NEW, 1)
    path.write_bytes(t.replace("\n", eol).encode("latin-1"))
    return True


if __name__ == "__main__":
    p = Path(sys.argv[1])
    print("patched" if patch(p) else "already patched", p)
