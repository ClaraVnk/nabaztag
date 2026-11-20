#!/usr/bin/env python3
"""The Wi-Fi line, inside the session, each time a session comes up.

Naboot 1.2.1.0's bootloader sends its Wi-Fi story with the boot URL (&dg=,
the wifiDiag opcode), so the Terrier sees it once per boot. A rabbit that
loses its session and gets it back without rebooting sends nothing: that was
every one of Nabazprout's three drops on 2026-11-19. With this patch the
runtime sends, the first time a session reaches its presence after a
connection, the same line inside it:

    <wifi xmlns="violet:nabaztag:wifi">W0_A2_E0_...</wifi>

wifiDiag exists only on Naboot 1.2.1.0 and later, so the warren decides, as
for the other session policies: "xmpp-wifi:0" as compiled, flipped to 1
under the HMAC by a warren that knows the rabbit's firmware has the opcode
(terrier mint.wifi_in_session). Off, nothing calls it. Only over TLS.

Applied after patch_tls.py (build-bytecode.sh does). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* wifi-in-session */"
TLS_MARKER = "/* xmpp-over-tls */"
ANCHOR = "fun XmppSessionIsDisconnected xSession=\n"
FUNS = '''/* wifi-in-session */
// The Wi-Fi line inside the session, once per connection. See
// bytecode/patch_wifi_in_session.py. The warren flips the digit to 1 before
// signing, for a firmware that has wifiDiag.
var XmppWifiPolicy="xmpp-wifi:0";;
var XmppWifiSent=0;;
fun XmppWifiReport=
	if (!XmppWifiSent) && (strget XmppWifiPolicy 10)==49 && XmppGlobalSession!=nil && XmppTlsOn && (tlsState)==2 then
	(
		set XmppWifiSent=1;
		xmpps_sendStanza
			XmppGlobalSession
			nil
			(xmpps_PlatformJID XmppGlobalSession XmppGlobalSession.sClients.clINT)
			stanzaMessage
			(strcatlist "<wifi xmlns=\\"violet:nabaztag:wifi\\">"::wifiDiag::"</wifi>"::nil)
	);
	0;;

'''
DISC_NEW = ANCHOR + "\tset XmppWifiSent=0;\n"
PRESENCE_OLD = ')|(ssPresence ->\n\t\t\t\tif (!strcmp xmlStanza.nameS "presence") then ('
PRESENCE_NEW = PRESENCE_OLD + " XmppWifiReport;"


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: the Wi-Fi line already goes inside the session")
        return False
    if TLS_MARKER not in text or "XmppTlsOn" not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTlsOn)")
    for what, needle in (("XmppSessionIsDisconnected", ANCHOR),
                         ("the presence that frees the session", PRESENCE_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    for name in ("xmpps_sendStanza", "xmpps_PlatformJID"):
        if f"fun {name}" not in text:
            sys.exit(f"FAIL {path}: {name} is missing; this is not the Violet main.mtl")
    text = text.replace(PRESENCE_OLD, PRESENCE_NEW, 1)
    text = text.replace(ANCHOR, FUNS + DISC_NEW, 1)
    assert FUNS.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: the Wi-Fi line goes inside the session once per connection")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
