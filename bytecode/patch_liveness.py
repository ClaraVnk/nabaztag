#!/usr/bin/env python3
"""A rabbit that notices its session is dead, and never goes to BOSH.

2026-11-10: a rabbit's XMPP-over-TLS session broke. It redialled three times,
each attempt failed, and the gate then fell back to BOSH -- XMPP over HTTP,
port 80 -- which no warren serves. It stayed there for two hours and ten
minutes, about 1 900 posts to /http-bind, until it was unplugged. Two
things in main.mtl kept it there, and a third would have kept it on a dead
session even without them:

  * XmppGateReconnect, after three failed TCP attempts, switches the gate to
    BOSH, and leaves BOSH only when a BOSH attempt *fails* -- an HTTP answer,
    even a redirect, is not a failure. Now: no BOSH. After three failed TCP
    attempts it waits 5 s plus up to 30 s (the same spacing the stock code
    uses after a disconnect, so a warren restart is not met by every rabbit
    at once) and tries three more, for ever. The wait goes through the run
    state, not XmppSessionIsDisconnected, which would also stop everything
    and recalibrate the ears on every round of an outage.

  * XmppTcpActuallySendData counts a successful write as hearing from the
    server (`sLastReadTime = time`, "succès de l'écriture"). The keepalive
    the rabbit itself writes every 8 s is accepted by the TCP stack whether
    or not anyone is at the other end, so XmppSessionReadTimeout never fired
    on a dead session. Now only a read moves sLastReadTime.

  * XmppSessionReadTimeout goes from 60 to 150 s: the warren now sends every
    bound rabbit a space after 45 s of silence (terrier xmpp.KEEPALIVE_S),
    so 150 s with nothing read means the warren is gone, not merely quiet.
    A warren that does not keepalive -- the add-on, older Terriers -- costs
    its rabbits a reconnect every 150 s of silence, which is harmless.

Run after patch_tls.py (build-bytecode.sh does). Idempotent: re-running on a
patched tree is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* liveness */"

TIMEOUT_OLD = "const XmppSessionReadTimeout = 60;;"
TIMEOUT_NEW = ("const XmppSessionReadTimeout = 150;; " + MARKER +
               " /* nothing READ for this long: the warren keepalives every 45 s */")

WRITE_OLD = "\t\t\t\t\t\tset xSession.sLastReadTime = time;\t// Succès de l'écriture.\n"
WRITE_NEW = ("\t\t\t\t\t\t// liveness: a write accepted by the TCP stack says nothing\n"
             "\t\t\t\t\t\t// about the server; only a read moves sLastReadTime.\n")

BOSH_START = '\t\t\t//Xw "Now we try in BOSH";\n'
BOSH_END = '\t\t)else(\n\t\t\t//Xw "Now we try in TCP";\n'
BOSH_NEW = '''\t\t\t// liveness: no BOSH -- no warren serves it, and a rabbit that went
\t\t\t// there stayed there. Three TCP tries failed: wait 5 s plus up to 30 s,
\t\t\t// then three more, for ever, without stopping everything each round.
\t\t\tset xSession.sGate.pActualMode = 1;
\t\t\tset xSession.sGate.pTry = 3;
\t\t\tset xSession.sGate.pMode = pTcp;
\t\t\tset xSession.sGate.pBoshV = nil;
\t\t\tset xSession.sStatus = sClosed;
\t\t\tset xSession.sGate.pStatus = pClose;
\t\t\tmatch getRunState with
\t\t\t(xmppReconnectRun _ -> 0)
\t\t\t|(_ -> let random 30 -> w in
\t\t\t\tsetRunState (xmppReconnectRun [(time_ms + 5000 + 1000 * w) xSession getRunState]); 0);
\t\t\t0
'''


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments

    if MARKER in text:
        print(f"[skip] {path}: liveness already applied")
        return False
    for what, needle in (("XmppSessionReadTimeout", TIMEOUT_OLD),
                         ("the write that counts as a read", WRITE_OLD),
                         ("the BOSH fallback", BOSH_START),
                         ("the TCP branch after it", BOSH_END)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    if "fun XmppTlsSelect" not in text:
        sys.exit(f"FAIL {path}: run patch_tls.py first")

    text = text.replace(TIMEOUT_OLD, TIMEOUT_NEW, 1)
    text = text.replace(WRITE_OLD, WRITE_NEW, 1)
    i = text.index(BOSH_START)
    j = text.index(BOSH_END, i)
    text = text[:i] + BOSH_NEW + text[j:]

    for s in (TIMEOUT_NEW, WRITE_NEW, BOSH_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: no BOSH, a write is not a read, 150 s read timeout")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
