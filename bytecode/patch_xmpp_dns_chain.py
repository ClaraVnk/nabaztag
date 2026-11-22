#!/usr/bin/env python3
"""A failed direct DNS lookup for the XMPP domain ends its chain.

Linotte (Naboot 1.2.1.2) stayed "orange fixe" after its owner's box turned
the Wi-Fi off from 23:45 to 04:30 (2026-11-21), until unplugged. The whole
rabbit in the emulator, 4 h 45 without Wi-Fi (scenarios/vm/
xmpp-night-wifi-off.txt, `fast`), ran out of memory after 3 h 12 and reset
in a loop; fifteen minutes off did not.

Why: when its SRV answers run out, XmppTcpOpen asks DNS for the domain
itself with XmppTcpOpen as the callback, and a lookup that times out calls
it back with nil -- which asks again, for ever, every DNS timeout. Every
reconnect attempt (three tries, a wait, three more: patch_liveness.py)
starts one more of those chains and none ever stops: the log grew by
260,000 lines more every quarter of an hour, and the heap with it.

Now the direct lookup's callback is XmppTcpOpenDirect: an address goes on
to XmppTcpOpen as before, a nil ends the chain, and the reconnect that
started it tries again on its own schedule. Both places that do the direct
lookup (XmppTcpOpen's end of SRV answers, XmppTcpOpenStream). Idempotent.
Usage: patch_xmpp_dns_chain.py <sources/main.mtl>
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "XmppTcpOpenDirect"
CALL_OLD = "dnsreq xSession.sDomain fixarg2 fixarg3 #XmppTcpOpen xSession"
CALL_NEW = "dnsreq xSession.sDomain fixarg2 fixarg3 #XmppTcpOpenDirect xSession"
PROTO_OLD = "proto XmppTcpOpen 3;;"
PROTO_NEW = ("proto XmppTcpOpen 3;;\n"
             "proto XmppTcpOpenDirect 3;;\t// patch_xmpp_dns_chain.py")
DEF_ANCHOR = "\n/*\n * Rouvre un port TCP pour une session \n */\n"
DEF = ("\n// patch_xmpp_dns_chain.py: the direct lookup's callback. An address\n"
       "// goes on; a nil ends the chain (it used to ask again, for ever).\n"
       "fun XmppTcpOpenDirect ip port xSession =\n"
       "\tif ip != nil then XmppTcpOpen ip port xSession else 0;;\n")


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    p = Path(sys.argv[1])
    raw = p.read_bytes()
    try:
        text, enc = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, enc = raw.decode("latin-1"), "latin-1"
    if MARKER in text:
        print(f"[skip] {p}: the direct lookup already ends its chain")
        return 0
    for what, old, n in (("the direct lookups", CALL_OLD, 2), ("XmppTcpOpen's proto", PROTO_OLD, 1),
                         ("XmppTcpReOpen's comment", DEF_ANCHOR, 1)):
        if text.count(old) != n:
            sys.exit(f"FAIL {p}: {what}: {text.count(old)} found, {n} expected")
    text = text.replace(CALL_OLD, CALL_NEW).replace(PROTO_OLD, PROTO_NEW, 1)
    text = text.replace(DEF_ANCHOR, DEF + DEF_ANCHOR, 1)
    p.write_bytes(text.encode(enc))
    print(f"[ok]   {p}: a failed direct XMPP lookup ends its chain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
