#!/usr/bin/env python3
"""Jingles and choreographies come down the TLS session too.

patch_res_in_session.py has the warren send a sound inside the session and
name it `ST res:N`. A program's other resources -- MU and MC (a jingle, a
sound played whole) and CH (a choreography) -- are not streamed: the
runtime loads each one over plain HTTP before the program starts
(rscFromProgram, runRscLoadNext), so what the rabbit is about to play or
dance is still readable on the path.

The warren now sends those the same way: one announce and the raw bytes per
resource, then the program, whose MU/MC/CH name `res:N`. runRscLoadNext
takes a `res:N` from the store XmppResSift fills -- the body, as the HTTP
load keeps it (without the headers) -- instead of fetching it. An N the
store does not have loads as nothing, as a failed fetch does.

Its own literal, "xmpp-rsc:0", flipped to 1 under the HMAC by a warren that
names resources this way: a rabbit still running a runtime with only the
`ST res:N` patch would try to fetch "res:N" over HTTP. As compiled every
MU/MC/CH is the URL it always was.

Applied after patch_res_in_session.py (build-bytecode.sh does). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* rsc-in-session */"

RES_MARKER = "/* res-in-session */"
FUNS_ANCHOR = "// ********** loading resources\n"
FUNS = '''/* rsc-in-session */
// A program resource (MU, MC, CH) the warren sent inside the session: it is
// named res:N and taken from XmppResStore instead of fetched. See
// bytecode/patch_rsc_in_session.py. The warren flips the digit to 1 before
// signing when it names resources this way.
var XmppRscPolicy="xmpp-rsc:0";;
fun XmppRscTake url=
	if (strget XmppRscPolicy 9)!=49 || url==nil || (strlen url)<5 || strcmp (strsub url 0 4) "res:" then 0
	else let XmppResFind XmppResStore (atoi strsub url 4 nil) -> d in
	(
		Secho "res: "; Secho url; Secho (if d==nil then " not received, at " else " from the session, at "); Iecholn time_ms;
		set rscloaded=[url d]::rscloaded;
		1
	);;

'''
LOAD_OLD = "\telse if nil!=listswitchstr rscloaded url then\n"
LOAD_NEW = ("\telse if XmppRscTake url then\n"
            "\t(\n"
            "\t\tset rsctoget=tl rsctoget;\n"
            "\t\trunRscLoadNext\n"
            "\t)\n"
            "\telse if nil!=listswitchstr rscloaded url then\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: program resources already come down the session")
        return False
    if RES_MARKER not in text or "fun XmppResFind" not in text:
        sys.exit(f"FAIL {path}: run patch_res_in_session.py first")
    for what, needle in (("the loading-resources comment", FUNS_ANCHOR),
                         ("runRscLoadNext's cache test", LOAD_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(FUNS_ANCHOR, FUNS + FUNS_ANCHOR, 1)
    text = text.replace(LOAD_OLD, LOAD_NEW, 1)
    for s in (FUNS, LOAD_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: MU/MC/CH named res:N are taken from the session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
