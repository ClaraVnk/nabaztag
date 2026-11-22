#!/usr/bin/env python3
"""A sleeping rabbit still tells the warren about its head and its tags.

Violet's main loop runs one control function per state, chosen by the
number each state returns: controlwait for idleRun, controlplay while a
program plays, and so on. sleepRun returned 0, so nothing ran: no
buttongetevent, no controlrfid. A sleeping rabbit was deaf and blind to
its owner; only the server could wake it. Measured on a real rabbit on
2026-11-22 (a user asked to wake his with a double click or a tag): asleep,
a double click, a long press and a tag, and nothing on the wire.

Here sleepRun returns CONTROL_SLEEP, and controlsleep reports, in the dark
-- no sound, no lights, no choreography, nothing played -- what the
warren needs to decide: the head (XmppSessionSendButtonMsg, the stanza
idle sends) and a tag (XmppRfidReport, the session or rfid.jsp as awake).
Waking is still the warren's word (the sleep packet), so an owner can map
a double click to "wake" and keep a bumped rabbit asleep. An ear turned by
hand is not read: the ears lie down in sleep and are not a gesture there.

Needs patch_rfid_xmpp.py (XmppRfidReport) before it; build-bytecode.sh
applies it after patch_sleep_after_program.py. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* head-in-sleep */"

RUN_OLD = "\t( sleepRun -> earRun;runIdle;0) |\n"
RUN_NEW = "\t( sleepRun -> earRun;runIdle;CONTROL_SLEEP) |\t/* head-in-sleep */\n"
CONST_OLD = "const CONTROL_PROGRAMWAIT=4;;\n"
CONST_NEW = CONST_OLD + "const CONTROL_SLEEP=5;;\t/* head-in-sleep */\n"
DISPATCH_OLD = "\telse if keymanager==CONTROL_PROGRAMWAIT then\n\t\tcontrolprogramwait\n"
DISPATCH_NEW = DISPATCH_OLD + "\telse if keymanager==CONTROL_SLEEP then\n\t\tcontrolsleep\n"
FUN_ANCHOR = "fun dumpStatus=\n"
FUN = '''/* head-in-sleep */
// A sleeping rabbit reports its head and its tags to the warren, in the
// dark: no sound, no lights. Stock Violet read neither while asleep.
// See bytecode/patch_head_in_sleep.py.
fun controlsleep=
	let buttongetevent -> ev in
	if ev!=nil then (
		Secho "controlsleep ev="; Iecholn ev;
		if ev==BUTTON_CLIC || ev==BUTTON_DCLIC || ev==BUTTON_LCLIC || ev==BUTTON_DLCLIC then
			XmppSessionSendButtonMsg ev 0 nil
	);
	// One report per tag shown: controlrfid forgets the last tag after a
	// second because its choreography holds the loop meanwhile; asleep
	// nothing does, and a tag resting 4 s went out 4 times (emulator,
	// 2026-11-22). Here it is forgotten only once the reader is empty.
	let rfidGet -> rfid in
	if rfid==nil then (
		set lastrfid = "0000000"; 0
	) else if (ParseRfid rfid) == 0 then (
		set lastrfid = "0000000"; 0
	) else if (strcmp rfid lastrfid) then (
		set lastrfid=rfid;
		set RFIDlast = time_ms;
		XmppRfidReport rfid; 0
	) else 0;
	0;;

'''


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: a sleeping rabbit already reports its head")
        return False
    for what, needle in (("sleepRun's control mode", RUN_OLD),
                         ("CONTROL_PROGRAMWAIT", CONST_OLD),
                         ("the control dispatch", DISPATCH_OLD),
                         ("dumpStatus", FUN_ANCHOR)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    for name in ("XmppRfidReport", "XmppSessionSendButtonMsg", "checkRfidPresence", "ParseRfid"):
        if f"fun {name}" not in text:
            sys.exit(f"FAIL {path}: {name} is missing (patch_rfid_xmpp.py first?)")
    text = text.replace(RUN_OLD, RUN_NEW, 1)
    text = text.replace(CONST_OLD, CONST_NEW, 1)
    text = text.replace(DISPATCH_OLD, DISPATCH_NEW, 1)
    text = text.replace(FUN_ANCHOR, FUN + FUN_ANCHOR, 1)
    for s in (RUN_NEW, CONST_NEW, DISPATCH_NEW, FUN):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a sleeping rabbit reports its head and its tags")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
