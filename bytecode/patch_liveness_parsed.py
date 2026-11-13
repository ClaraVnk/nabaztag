#!/usr/bin/env python3
"""Liveness counts what the rabbit could read, not the bytes that arrived.

patch_liveness.py made the session reopen when nothing has been READ for
XmppSessionReadTimeout (150 s), and the warren sends a space every 45 s so
that an idle rabbit is never mistaken for a dead one. But "read" meant any
TCP read: a rabbit whose input buffer had jammed -- bytes it could not parse
as a stanza sitting at its head, everything after them queued behind -- still
received the warren's spaces, still moved sLastReadTime, and never reopened.
Seen on 2026-11-11 on two rabbits on 1.0.0.3: they kept sending (head
presses reached the warren), acknowledged every TCP segment, and acted on
nothing they were sent until they were unplugged, for hours.

Now a read only counts when it leaves the input buffer empty or blank: a
stanza was taken whole, or a keepalive space arrived. A jammed buffer stops
the clock, and 150 s later the session reopens as it always could.

Before it does, the rabbit tells the warren what was jammed: one <diag/>
stanza with the buffer's length and its first 48 bytes in hex, the bytes
still owed for a resource in the session, and the streaming and processing
flags. Sending still works when reading does not, and the warren logs every
stanza it receives -- the next jam explains itself. Then the buffer and the
resource state are cleared, so the new session starts clean.

Applied after the session patches (it names XmppResLeft). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* liveness-parsed */"

HELPER_ANCHOR = "fun XmppTcpReadPlain t val msg xSession =\n"
HELPER = ("/* liveness-parsed */\n"
          "// Nothing in the input buffer but blanks: what arrived was taken whole.\n"
          "// See bytecode/patch_liveness_parsed.py.\n"
          "fun XmppBufBlank b i =\n"
          "\tif b==nil then 1\n"
          "\telse if i>=strlen b then 1\n"
          "\telse let strget b i -> c in\n"
          "\tif c==32 || c==10 || c==13 || c==9 then XmppBufBlank b (i+1) else 0;;\n\n")

READ_OLD = ("\t\t\tset xSession.sLastReadTime = time;\n\t\t\t\n\t\t\t\n"
            "\t\t\t/* DEUX MODE")
READ_NEW = ("\t\t\t// liveness-parsed: sLastReadTime moves below, once the buffer is clean\n"
            "\t\t\t\n\t\t\t\n"
            "\t\t\t/* DEUX MODE")

LOOP_OLD = "\t\t\t) do nil;\n\n\t\t\t0\n\t\t)else if val == TCPCLOSE then ("
LOOP_NEW = ("\t\t\t) do nil;\n"
            "\t\t\tif XmppBufBlank xSession.sGate.pBuffer 0 then set xSession.sLastReadTime = time;\n"
            "\n\t\t\t0\n\t\t)else if val == TCPCLOSE then (")

DIAG_ANCHOR = "fun XmppSessionIdle=\n"
DIAG = ("/* liveness-parsed */\n"
        "// Tell the warren what jammed, then start the next session clean.\n"
        "fun XmppLivenessDiag xSession =\n"
        "\tlet xSession.sGate.pBuffer -> b in\n"
        "\tlet if b==nil then 0 else strlen b -> n in\n"
        "\t(\n"
        "\t\tXmppGateSend xSession strcatlist \"<message><diag xmlns='violet:nabaztag:diag' buf='\"::(itoa n)::\n"
        "\t\t\t\"' resleft='\"::(itoa XmppResLeft)::\"' streaming='\"::(itoa gStreamingState)::\n"
        "\t\t\t\"' processing='\"::(itoa gProcessingState)::\"'>\"::\n"
        "\t\t\t(if n==0 then \"\" else dataToHexStr strsub b 0 (min 48 n))::\"</diag></message>\"::nil;\n"
        "\t\tset xSession.sGate.pBuffer = nil;\n"
        "\t\tset XmppResLeft = 0;\n"
        "\t\tset XmppResParts = nil;\n"
        "\t\t0\n"
        "\t);;\n\n")

IDLE_OLD = ("\t\t\t\t\t\t\t// On n'a pas eu de nouvelle du serveur depuis XmppSessionReadTimeout \n"
            "\t\t\t\t\t\t\tXmppSessionIsDisconnected xSession\n")
IDLE_NEW = ("\t\t\t\t\t\t\t// On n'a pas eu de nouvelle du serveur depuis XmppSessionReadTimeout \n"
            "\t\t\t\t\t\t\tXmppLivenessDiag xSession;\t// liveness-parsed\n"
            "\t\t\t\t\t\t\tXmppSessionIsDisconnected xSession\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: liveness already counts what was read")
        return False
    if "var XmppResLeft" not in text:
        sys.exit(f"FAIL {path}: apply the res-in-session patch first")
    for what, needle in (("XmppTcpReadPlain", HELPER_ANCHOR),
                         ("the TCPREAD clock", READ_OLD),
                         ("the stanza loop's end", LOOP_OLD),
                         ("XmppSessionIdle", DIAG_ANCHOR),
                         ("the liveness check", IDLE_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)
    text = text.replace(READ_OLD, READ_NEW, 1)
    text = text.replace(LOOP_OLD, LOOP_NEW, 1)
    text = text.replace(DIAG_ANCHOR, DIAG + DIAG_ANCHOR, 1)
    text = text.replace(IDLE_OLD, IDLE_NEW, 1)
    for s in (HELPER, READ_NEW, LOOP_NEW, DIAG, IDLE_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a jammed input buffer no longer looks alive")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
