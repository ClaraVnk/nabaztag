#!/usr/bin/env python3
"""A DNS reply is harder to forge, and a malformed one cannot exhaust the heap.

dns.mtl numbered its queries sequentially (`dnsid+1`), so an off-path host
could predict the id of the rabbit's next lookup and race a forged A or SRV
record back -- and the SRV path feeds the XMPP server the rabbit connects to,
so a forged answer there is a redirect of the whole session. The id is drawn
at random now (0..65535) at each of the three query sites, so an attacker who
did not see the query cannot guess it.

Separately, `parsemsg` walked `nbqu` questions and `nbans` answers straight
from the packet (each up to 65535) by recursion, which a single malformed
reply could turn into deep recursion and unbounded growth of the SRV/cache
lists -- a reboot. Both counts are clamped to DNS_MAX_RR, which is far more
records than any answer the rabbit asks for.

Not fixed here (a structural change left for its own batch): `cbnetdns`
parses the message before it matches the id, so an unsolicited packet on the
query port still appends to the global SRV list even though its callback is
never called. The random id makes a match unlikely; the reorder closes it.

Applied to dns.mtl; independent of every other patch. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* dns-antispoof */"

# A const in front of parsemsg, and the record-count clamps.
PARSE_ANCHOR = "fun parsemsg s=\n"
PARSE_PROTO = ("/* dns-antispoof */\n"
               "const DNS_MAX_RR=16;;\t// most records we will walk in a reply\n")
NBQU_OLD = "\telse let parsequs s 12 nbqu -> i in\n"
NBQU_NEW = "\telse let parsequs s 12 (if nbqu>DNS_MAX_RR then DNS_MAX_RR else nbqu) -> i in\n"
NBANS_OLD = "\t\tparseans s i nbans\n"
NBANS_NEW = "\t\tparseans s i (if nbans>DNS_MAX_RR then DNS_MAX_RR else nbans)\n"

# The three places a query id is chosen: random, not sequential.
ID1_OLD = "\tset dnsid=(if dnsid==nil then time_ms else (dnsid+1));\n"
ID1_NEW = "\tset dnsid=(random 65536);\t// dns-antispoof: unpredictable query id\n"
ID2_OLD = "(dnsChangeIdInTrame x.reqD (set dnsid=(dnsid+1)))"
ID2_NEW = "(dnsChangeIdInTrame x.reqD (set dnsid=(random 65536)))"
ID3_OLD = "\tset dnsid=if dnsid==nil then time_ms%9999 else dnsid+1;\t// ON MET l'ID pour identifier par la suite\n"
ID3_NEW = "\tset dnsid=(random 65536);\t// dns-antispoof: unpredictable query id\n"


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"
    if MARKER in text:
        print(f"[skip] {path}: DNS ids are already random and the counts bounded")
        return False
    for what, needle in (("parsemsg", PARSE_ANCHOR),
                         ("the question walk", NBQU_OLD),
                         ("the answer walk", NBANS_OLD),
                         ("the dnsreq id", ID1_OLD),
                         ("the retry id", ID2_OLD),
                         ("the DnsSrv id", ID3_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(PARSE_ANCHOR, PARSE_PROTO + PARSE_ANCHOR, 1)
    text = text.replace(NBQU_OLD, NBQU_NEW, 1)
    text = text.replace(NBANS_OLD, NBANS_NEW, 1)
    text = text.replace(ID1_OLD, ID1_NEW, 1)
    text = text.replace(ID2_OLD, ID2_NEW, 1)
    text = text.replace(ID3_OLD, ID3_NEW, 1)
    for s in (PARSE_PROTO, NBQU_NEW, NBANS_NEW, ID1_NEW, ID2_NEW, ID3_NEW):
        assert s.isascii(), "what we insert must be ASCII: the sources are latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: DNS query ids are random and reply record counts bounded")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/dns.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
