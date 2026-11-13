#!/usr/bin/env python3
"""The microphone travels inside the TLS session.

When an owner presses "talk to it" on the warren's page, the warren sends
`RS <ip> <port>` and the rabbit streams its microphone -- 8 kHz IMA-ADPCM,
micstream.mtl -- as "snd" UDP datagrams, in the clear: whoever is on the
path hears what is said in the room. The XMPP session already runs TLS 1.3
(patch_tls.py), and XmppTcpSend encrypts whatever it is given, so the audio
can go there instead.

`RS session` starts the recorder with XmppMicChunk as its callback. Each
buffer the recorder hands over is written to the session as

    <mic xmlns='violet:nabaztag:mic' len='N'/>  then  N raw bytes

in one XmppTcpSend: one encrypted record, queued behind whatever the send
queue (sDataToSend) already holds, so it never cuts into a stanza. The
warren takes N raw bytes off the stream after the announce, before its XML
parser sees anything. Nothing is written unless the session runs TLS and
its handshake is over: a plain session never carries audio. `RT` stops as
before (stopmicstream clears g_stream_on and stops the recorder).

The warren decides, as for the other session policies: "xmpp-mic:0" as
compiled, flipped to 1 under the HMAC by a warren that sends `RS session`.
As compiled `RS session` does nothing, and `RS <ip> <port>` is the UDP
stream it always was.

Applied after patch_main.py (micstream.mtl and RS/RT) and patch_tls.py
(build-bytecode.sh does). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* mic-in-session */"

TLS_MARKER = "/* xmpp-over-tls */"
INCLUDE = "#include micstream.mtl\n"
FUNS = '''/* mic-in-session */
// The microphone inside the TLS session: an announce, then the raw ADPCM.
// See bytecode/patch_mic_in_session.py. The warren flips the digit to 1
// before signing when it sends `RS session`.
var XmppMicPolicy="xmpp-mic:0";;
fun XmppMicInSession= (strget XmppMicPolicy 9)==49;;
// The microphone never records for longer than this without a fresh RS, and
// it stops the moment the session it was feeding is gone -- it must not keep
// listening past a lost RT, or resume on its own when a dropped session
// reconnects. A privacy rule, not a convenience: the owner presses "talk"
// again if they meant to.
const XMPP_MIC_MAX_SECONDS=60;;
var g_mic_start=0;;
fun XmppMicChunk s=
	if !g_stream_on then 0
	else if (time - g_mic_start) > XMPP_MIC_MAX_SECONDS then (Secholn "mic: max duration reached, stopping"; stopmicstream; 0)
	else if (XmppGlobalSession==nil) || (!XmppTlsOn) || ((tlsState)!=2) then (Secholn "mic: session gone, stopping"; stopmicstream; 0)
	else if s!=nil then
		(XmppTcpSend XmppGlobalSession strcatlist "<mic xmlns='violet:nabaztag:mic' len='"::(itoa strlen s)::"'/>"::s::nil; 0)
	else 0;;
fun XmppMicStart=
	if XmppMicInSession then
	(
		Secholn "mic: streaming inside the session";
		set g_stream_on=1;
		set g_mic_start=time;
		recStop;
		recStart 8000 0 #XmppMicChunk;
		0
	);
	0;;

'''
RS_OLD = '\t\telse if !strcmp key "RS" then '
RS_NEW = ('\t\telse if (!strcmp key "RS") && (!strcmp val "session") then (XmppMicStart;0)\n'
          '\t\telse if !strcmp key "RS" then ')


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: the microphone already goes inside the session")
        return False
    if TLS_MARKER not in text or "fun XmppTcpSend " not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTcpSend)")
    for what, needle in (("the micstream include", INCLUDE),
                         ("the RS command", RS_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(INCLUDE, INCLUDE + FUNS, 1)
    text = text.replace(RS_OLD, RS_NEW, 1)
    for s in (FUNS, RS_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: `RS session` streams the microphone inside the TLS session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
