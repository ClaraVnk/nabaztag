#!/usr/bin/env python3
"""A long press of the head button goes to the warren, inside the session.

As shipped by Violet, holding the head button records the room and POSTs the
WAV to `record.jsp` over plain HTTP (_controlrecord). Le Terrier serves no
record.jsp -- the room would cross the internet in the clear -- so on a
rabbit there a long press ended in a 404 and a rabbit that seemed to sulk
(2026-11-12, an owner's first try). The warren already has what a long press
should start: the owner's own action for `long_click` (say, "listen and
answer"), whose microphone runs inside the TLS session
(patch_mic_in_session.py).

With the policy on and the session running TLS, a long click (and a double
long click) is reported like a click,

    <button xmlns='violet:nabaztag:button'><clic>3</clic></button>

with the acknowledgement chime, and nothing is recorded. Off, or on a plain
session, it records to record.jsp as it always did.

The warren decides, as for the other session policies: "xmpp-btn:0" as
compiled, flipped to 1 under the HMAC by a warren that handles long_click.

Applied after patch_main.py and patch_tls.py (build-bytecode.sh does).
Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* btn-in-session */"

TLS_MARKER = "/* xmpp-over-tls */"
ANCHOR = "fun controlwait="
FUNS = '''/* btn-in-session */
// A long press of the head goes to the warren inside the TLS session instead
// of being recorded to record.jsp. See bytecode/patch_btn_in_session.py. The
// warren flips the digit to 1 before signing.
var XmppBtnPolicy="xmpp-btn:0";;
fun XmppBtnInSession= (strget XmppBtnPolicy 9)==49 && XmppGlobalSession!=nil && XmppTlsOn && (tlsState)==2;;
fun XmppLongPress ev mode=
	if XmppBtnInSession then
	(
		Secholn "button: long press sent inside the session";
		controlsound midi_ack;
		XmppSessionSendButtonMsg ev 0 nil
	)
	else _controlrecord mode;
	0;;

'''
LCLIC_OLD = "\t\telse if ev==BUTTON_LCLIC then _controlrecord 0\t//  //back ou record\n"
LCLIC_NEW = "\t\telse if ev==BUTTON_LCLIC then XmppLongPress ev 0\t// btn-in-session\n"
DLCLIC_OLD = "\t\telse if ev==BUTTON_DLCLIC then _controlrecord 1\t// //back ou record\n"
DLCLIC_NEW = "\t\telse if ev==BUTTON_DLCLIC then XmppLongPress ev 1\t// btn-in-session\n"


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: a long press already goes inside the session")
        return False
    if TLS_MARKER not in text or "XmppTlsOn" not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTlsOn)")
    for what, needle in (("controlwait", ANCHOR),
                         ("the long click in controlwait", LCLIC_OLD),
                         ("the double long click in controlwait", DLCLIC_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(ANCHOR, FUNS + ANCHOR, 1)
    text = text.replace(LCLIC_OLD, LCLIC_NEW, 1)
    text = text.replace(DLCLIC_OLD, DLCLIC_NEW, 1)
    for s in (FUNS, LCLIC_NEW, DLCLIC_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a long press goes to the warren inside the TLS session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
