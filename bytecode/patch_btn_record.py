#!/usr/bin/env python3
"""A long press records while the head is held, as Violet's did -- inside the
TLS session.

With patch_btn_in_session.py a long press was only reported (<clic>3</clic>
and the ack chime), and the warren then asked for the microphone (`RS
session`) once the head was released and the chime had played out: the
recording started some 5 s after the press began. Clara counted aloud from
the press and the rabbit played her back from "cinq", while the chime and a
bip had told her it was listening (2026-11-20). Violet's rabbit recorded
while the head was held, its nose red (_controlrecord).

With "xmpp-btnrec:1", which the warren sets only for a rabbit whose owner
put a microphone action on the long press (listen and repeat, ask), a long
press inside the TLS session:

- reports the press as before (<clic>3</clic>), then starts the microphone
  inside the session at once (XmppMicStart, patch_mic_in_session.py), with
  no chime: the chip cannot play and record at once;
- lights the nose red while it records;
- stops when the head is released, on the warren's `RT`, or after
  XMPP_MIC_MAX_SECONDS, and says so with an empty announce,
  <mic xmlns='violet:nabaztag:mic' len='0'/>.

Only the long press records; a double long press is reported as before.

Off ("xmpp-btnrec:0", as compiled) a long press is reported as
patch_btn_in_session.py does, and nothing records. Applied after
patch_mic_in_session.py and patch_btn_in_session.py (build-bytecode.sh does).
Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* btn-record */"
NEEDS = ("/* btn-in-session */", "/* mic-in-session */")

PRESS_OLD = ('\t\tSecholn "button: long press sent inside the session";\n'
             '\t\tcontrolsound midi_ack;\n'
             '\t\tXmppSessionSendButtonMsg ev 0 nil\n')
PRESS_NEW = ('\t\tif XmppBtnRec && ev==BUTTON_LCLIC then XmppBtnRecordStart ev\t// btn-record\n'
             '\t\telse (\n'
             '\t\tSecholn "button: long press sent inside the session";\n'
             '\t\tcontrolsound midi_ack;\n'
             '\t\tXmppSessionSendButtonMsg ev 0 nil\n'
             '\t\t)\n')
FUNS_ANCHOR = "fun XmppLongPress ev mode="
FUNS = '''/* btn-record */
// A long press records while the head is held, inside the TLS session. See
// bytecode/patch_btn_record.py. The warren flips the digit to 1 before
// signing, for a rabbit whose long press runs a microphone action.
var XmppBtnRecPolicy="xmpp-btnrec:0";;
fun XmppBtnRec= (strget XmppBtnRecPolicy 12)==49 && XmppMicInSession;;
fun XmppBtnRecordStart ev=
	Secholn "button: long press, recording inside the session while held";
	XmppSessionSendButtonMsg ev 0 nil;
	set g_btn_rec=1;
	led LED_NOSE RGB_RED;
	XmppMicStart;
	0;;
fun XmppBtnRecordEnd=
	set g_btn_rec=0;
	led LED_NOSE RGB_BLACK;
	if (XmppGlobalSession!=nil) && XmppTlsOn && ((tlsState)==2) then
		XmppTcpSend XmppGlobalSession "<mic xmlns='violet:nabaztag:mic' len='0'/>";
	0;;

'''
# The recorder's callback: a released head ends a recording the press began.
# The nose is lit again with each buffer: info.mtl repaints it every frame.
CHUNK_OLD = "fun XmppMicChunk s=\n\tif !g_stream_on then 0\n"
CHUNK_NEW = ("fun XmppMicChunk s=\n"
             "\tif g_btn_rec && !button2 then (Secholn \"mic: head released, stopping\"; stopmicstream; XmppBtnRecordEnd; 0)\n"
             "\telse if g_btn_rec && g_stream_on then (led LED_NOSE RGB_RED; XmppMicChunkSend s)\n"
             "\telse XmppMicChunkSend s;;\n"
             "fun XmppMicChunkSend s=\n"
             "\tif !g_stream_on then 0\n")
# The recorder stopped on its own (max duration, session gone): the nose too.
STOP_OLD = '(Secholn "mic: max duration reached, stopping"; stopmicstream; 0)'
STOP_NEW = '(Secholn "mic: max duration reached, stopping"; stopmicstream; if g_btn_rec then XmppBtnRecordEnd; 0)'
GONE_OLD = '(Secholn "mic: session gone, stopping"; stopmicstream; 0)'
RT_OLD = '\t\telse if !strcmp key "RT" then (stopmicstream;0)'
RT_NEW = '\t\telse if !strcmp key "RT" then (stopmicstream; if g_btn_rec then XmppBtnRecordEnd; 0)'
GONE_NEW = '(Secholn "mic: session gone, stopping"; stopmicstream; if g_btn_rec then XmppBtnRecordEnd; 0)'


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: a long press already records while held")
        return False
    for m in NEEDS:
        if m not in text:
            sys.exit(f"FAIL {path}: {m} missing (patch_btn_in_session.py and patch_mic_in_session.py first)")
    for what, needle in (("the long press report", PRESS_OLD), ("XmppLongPress", FUNS_ANCHOR),
                         ("the recorder callback", CHUNK_OLD), ("the max-duration stop", STOP_OLD),
                         ("the lost-session stop", GONE_OLD), ("the RT command", RT_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    # XmppMicChunk is defined before XmppLongPress, and calls XmppBtnRecordEnd:
    # the record functions go ahead of the callback, the proto keeps the order.
    text = text.replace(CHUNK_OLD, "var g_btn_rec=0;;\nproto XmppBtnRecordEnd 0;;\nproto XmppMicChunkSend 1;;\n"
                        + CHUNK_NEW, 1)
    text = text.replace(STOP_OLD, STOP_NEW, 1)
    text = text.replace(GONE_OLD, GONE_NEW, 1)
    text = text.replace(RT_OLD, RT_NEW, 1)
    text = text.replace(FUNS_ANCHOR, FUNS + FUNS_ANCHOR, 1)
    text = text.replace(PRESS_OLD, PRESS_NEW, 1)
    for s in (FUNS, PRESS_NEW, CHUNK_NEW, STOP_NEW, GONE_NEW, RT_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a long press records while held, inside the TLS session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
