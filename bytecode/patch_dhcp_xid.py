#!/usr/bin/env python3
"""A DHCP reply is trusted only when we asked for one, and it echoes our id.

dhcp.mtl's cbnetdhcp accepted any DHCP reply (OFFER or ACK) whose chaddr held
the rabbit's MAC, at any time: it checked neither the transaction id nor
whether a request was in flight, and port 68 stays registered for the whole
run. So any host on the segment -- the MAC travels in clear in every frame --
could send one unsolicited ACK and rewrite the rabbit's IP, gateway and DNS,
pointing it at itself, hours after boot. The client also sent a transaction
id of zero in every request (mkdhcp never filled offset 4), so even a reply
that did echo it told nothing apart.

Now:
  - every request (DISCOVER, and the renewal REQUEST) draws a fresh random
    4-byte transaction id and stamps the time it was sent (dhcp_newreq);
  - mkdhcp writes that id at offset 4, so OFFER/ACK echo it;
  - cbnetdhcp accepts a reply only within DHCP_ACCEPT_WINDOW seconds of a
    request and only if it echoes the id. The OFFER->REQUEST step keeps the
    id and window of the DISCOVER it answers, so a normal exchange is
    untouched; an unsolicited ACK outside a window, or with the wrong id, is
    dropped.

A random id is timer-seeded (random), which is enough here: an off-path host
that did not see the broadcast DISCOVER cannot guess it, and the window keeps
even a host that did from injecting at an arbitrary later moment.

Applied to dhcp.mtl; independent of every other patch. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* dhcp-antispoof */"

PROTO_ANCHOR = "fun mkdhcp op netip hostip newip =\n"
PROTO = ("/* dhcp-antispoof */\n"
         "// A DHCP reply is accepted only inside a short window after we asked,\n"
         "// and only if it echoes the random transaction id we sent. See\n"
         "// bytecode/patch_dhcp_xid.py.\n"
         "const DHCP_ACCEPT_WINDOW=10;;\t// seconds a reply is accepted after a request\n"
         "var dhcp_xid=\"\\0\\0\\0\\0\";;\n"
         "var dhcp_reqtime=-1000000;;\t// far past: nothing accepted before the first request\n"
         "fun dhcp_newreq=\n"
         "\tlet strnew 4 -> x in (\n"
         "\t\tstrset x 0 (random 256); strset x 1 (random 256);\n"
         "\t\tstrset x 2 (random 256); strset x 3 (random 256);\n"
         "\t\tset dhcp_xid=x\n"
         "\t);\n"
         "\tset dhcp_reqtime=time;\n"
         "\t0;;\n\n")

# mkdhcp writes the id at offset 4 (op/htype/hlen/hops then xid).
XID_OLD = "\t\tstrcpy b 12 netip 0 4;\n"
XID_NEW = "\t\tstrcpy b 12 netip 0 4;\n\t\tstrcpy b 4 dhcp_xid 0 4;\t// dhcp-antispoof\n"

# cbnetdhcp only trusts a reply in the window and with our id.
CB_OLD = "\tif x==2 && !strcmp mac mymac then\n"
CB_NEW = ("\tif x==2 && (!strcmp mac mymac) && ((time-dhcp_reqtime)<DHCP_ACCEPT_WINDOW)"
          " && (!vstrcmp src 4 dhcp_xid 0 4) then\t// dhcp-antispoof\n")

# A fresh id and window before the DISCOVER (startdhcp)...
DISCOVER_OLD = "\t\t\tudpsend netip 68 ipbroadcast 67 (mkdhcp DHCP_DISCOVER netip nil nil) macbroadcast\n"
DISCOVER_NEW = "\t\t\t(dhcp_newreq; udpsend netip 68 ipbroadcast 67 (mkdhcp DHCP_DISCOVER netip nil nil) macbroadcast)\n"

# ...and before the renewal REQUEST (checkdhcp). The OFFER->REQUEST step in
# cbnetdhcp keeps the DISCOVER's id and window and is left alone.
RENEW_OLD = "\t\tudpsend netip 68 ipbroadcast 67 (mkdhcp DHCP_REQUEST netip nil nil) macbroadcast;\n"
RENEW_NEW = "\t\tdhcp_newreq;\n\t\tudpsend netip 68 ipbroadcast 67 (mkdhcp DHCP_REQUEST netip nil nil) macbroadcast;\n"


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"
    if MARKER in text:
        print(f"[skip] {path}: DHCP replies are already checked")
        return False
    for what, needle in (("mkdhcp", PROTO_ANCHOR),
                         ("mkdhcp's netip copy", XID_OLD),
                         ("cbnetdhcp's accept test", CB_OLD),
                         ("the DISCOVER send", DISCOVER_OLD),
                         ("the renewal REQUEST send", RENEW_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(PROTO_ANCHOR, PROTO + PROTO_ANCHOR, 1)
    text = text.replace(XID_OLD, XID_NEW, 1)
    text = text.replace(CB_OLD, CB_NEW, 1)
    text = text.replace(DISCOVER_OLD, DISCOVER_NEW, 1)
    text = text.replace(RENEW_OLD, RENEW_NEW, 1)
    for s in (PROTO, XID_NEW, CB_NEW, DISCOVER_NEW, RENEW_NEW):
        assert s.isascii(), "what we insert must be ASCII: the sources are latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a DHCP reply is trusted only in-window and with our id")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/dhcp.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
