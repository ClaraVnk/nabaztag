#!/usr/bin/env python3
"""The RFID report travels inside the XMPP session when that session runs TLS.

A tag on the reader is the one request of its own the runtime sends over
HTTP that makes the warren act: `GET /vl/rfid.jsp?sn=<mac>&...&t=<tag>`
fires the owner's mapped action -- a webhook, a sound, a light. It carries
no credential; whoever knows the MAC can send it, and the warren can only
check where it came from. The head button and the ears never had this
problem: main.mtl sends them as XMPP stanzas (violet:nabaztag:button,
violet:nabaztag:ears), inside TLS since patch_tls.py.

So the tag goes the same way. controlrfid calls XmppRfidReport, which sends

    <rfid xmlns="violet:nabaztag:rfid"><tag>0123456789abcdef</tag></rfid>

as a <message> through xmpps_sendStanza, exactly as the button does, to
the platform's rfid resource -- when, and only when, all three hold:

  - the socket the XMPP gate uses runs TLS (XmppTlsOn, patch_tls.py), so the
    warren knows the rabbit from the key that opened the session;
  - there is a session (XmppGlobalSession);
  - the warren said it handles the stanza: the literal XmppRfidPolicy,
    "xmpp-rfid:0" as compiled, flipped to "xmpp-rfid:1" by the warren before
    the HMAC covers the bytecode, as it does "xmpp-tls:N". A warren that
    does not know the stanza never flips it, so this runtime served by such
    a warren keeps sending rfid.jsp, which that warren understands.

Otherwise rfid.jsp goes out exactly as before. The tag string is the one
rfid.jsp carries (webmac of the reader's bytes), so the owner's mappings
match either way. The HTTP reply used to be run as a command frame
(_cbrfidhttp -> evalTrame); the warren answers an empty body and pushes
what it wants over XMPP, so the stanza needs no reply: the two flags the
callback cleared are cleared on send.

The interactive-mode report (interactiveSendRfid, itmode.jsp) is left alone:
the Terrier serves no interactive application.

Applied after patch_tls.py: XmppTlsOn must exist. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* rfid-over-xmpp */"

TLS_MARKER = "/* xmpp-over-tls */"

CALL_OLD = "\t\t\t\t(httprequest \"GET\" rfidurl rfid nil #_cbrfidhttp HTTP_NORMAL ; 0)\n"
CALL_NEW = "\t\t\t\t(XmppRfidReport rfid; 0)\n"

ANCHOR = "fun controlrfid=\n"
FUNS = '''/* rfid-over-xmpp */
// The warren flips the digit to 1 before signing when it handles the
// violet:nabaztag:rfid stanza; as compiled, and from a warren that does
// not, the tag goes over rfid.jsp as it always did.
var XmppRfidPolicy="xmpp-rfid:0";;
fun XmppRfidInSession= (strget XmppRfidPolicy 10)==49;;

// A tag on the reader, to the warren: inside the XMPP session when its
// socket runs TLS and the warren asked for it (the warren then knows the
// rabbit from the key that opened the session), rfid.jsp otherwise. The
// stanza is the button's shape (XmppSessionSendButtonMsg); the warren
// answers over the session, so the flags the HTTP callback cleared are
// cleared here.
fun XmppRfidReport rfid=
	if XmppTlsOn && XmppRfidInSession && XmppGlobalSession!=nil then
	(
		xmpps_sendStanza
			XmppGlobalSession
			nil
			(xmpps_PlatformJID XmppGlobalSession XmppGlobalSession.sClients.clRFID)
			stanzaMessage
			(strcatlist "<rfid xmlns=\\"violet:nabaztag:rfid\\"><tag>"::(webmac rfid)::"</tag></rfid>"::nil);
		set gProcessingState = 0;
		set RFIDchek = 0;
		0
	)
	else (httprequest "GET" rfidurl rfid nil #_cbrfidhttp HTTP_NORMAL; 0);;

'''


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")      # main.mtl carries 8-bit comments
        encoding = "latin-1"

    if MARKER in text:
        print(f"[skip] {path}: the RFID report already travels over the XMPP session")
        return False
    if TLS_MARKER not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTlsOn)")
    for what, needle, count in (("the rfid.jsp request in controlrfid", CALL_OLD, 1),
                                ("controlrfid", ANCHOR, 1)):
        if text.count(needle) != count:
            sys.exit(f"FAIL {path}: expected {count} of {what}, found {text.count(needle)}")
    for name in ("xmpps_sendStanza", "xmpps_PlatformJID", "_cbrfidhttp"):
        if f"fun {name}" not in text:
            sys.exit(f"FAIL {path}: {name} is missing; this is not the Violet main.mtl")

    text = text.replace(CALL_OLD, CALL_NEW, 1)
    text = text.replace(ANCHOR, FUNS + ANCHOR, 1)

    assert FUNS.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: the RFID report goes over the XMPP session when it runs TLS")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
