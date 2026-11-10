#!/usr/bin/env python3
"""The sound a rabbit plays travels inside its TLS session.

What the warren has a rabbit say -- a TTS answer, an adoption code, a sound
-- is stored on the warren and named in a program packet as
`ST http://<warren>/res/<token>`; the rabbit fetches it over plain HTTP. The
packet rides inside the XMPP session, which runs TLS 1.3 since patch_tls.py,
but the fetch does not: anyone on the path hears what the rabbit is about to
say. A second TLS connection for the download is not possible -- the
firmware has one TLS context (crypto/nab_tls.c), and XMPP owns it while the
runtime runs -- so the bytes come down the session the rabbit already has.

On the wire, inside the session, the warren writes under one lock (nothing
else can come between):

    <res xmlns='violet:nabaztag:res' id='N' len='L'/>     one record, alone
    L raw bytes                                            records of <= 4096
    <message ...><packet ...>ST res:N</packet></message>   the program, as ever

The raw bytes are not base64: b64.mtl allocates a string per character and
would take minutes over 100 KB. They do not go through the XML parser
either: tlsIn hands the bytecode one decrypted record per call, and the
announce says how many bytes follow, so XmppResSift takes the records it is
owed out of the list XmppTlsDrain returns before anything is concatenated
for the parser. A record that starts with the announce sets id and length;
while bytes are expected, a record is appended to the resource; any other
record is XML as before.

Received resources are kept, newest first, within two bounds: three of them
and XMPP_RES_BUDGET bytes together (200 000). Room is made when the announce
arrives -- oldest first -- so the store never holds more than the budget,
and a resource longer than the budget is read and dropped, not kept: its ST
then finds nothing. 200 000 bytes is 25 s of the warren's 64 kbit/s speech;
with one evicted resource still playing from audiolib's fifo the worst case
is 400 000, which is WAV_BUFFER_MAXSIZE, what the stock HTTP player already
allows itself in an 800 KB heap. The warren sends anything longer by URL.

`ST res:N` plays resource N with wavstartlocal (the list of strings as it
was received, from memory). An N the store does not have stops the player,
and the stream command ends as a failed fetch does. Any other ST is the URL
it always was.

The warren decides, as for the RFID report: the literal XmppResPolicy,
"xmpp-res:0" as compiled, flipped to 1 under the HMAC by a warren that
sends resources this way (the Terrier, for a rabbit on TLS). As compiled,
XmppResSift passes every record through untouched, and nothing changes.

Applied after patch_liveness.py (build-bytecode.sh does), on a main.mtl that
has patch_tls.py's XmppTcpRead. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* res-in-session */"

TLS_MARKER = "/* xmpp-over-tls */"
LIVENESS_MARKER = "/* liveness */"

# The TLS XmppTcpRead (patch_tls.py), before which the sifting goes.
READ_ANCHOR = "fun XmppTcpRead t val msg xSession =\n"
SIFT_FUNS = '''/* res-in-session */
/* ------------------------------------------------------------------------
 * A resource the warren sends inside the TLS session: an announce record,
 * then its raw bytes. See bytecode/patch_res_in_session.py. */

// The warren flips the digit to 1 before signing when it sends resources
// this way; as compiled every record is XML, as before.
var XmppResPolicy="xmpp-res:0";;
fun XmppResInSession= (strget XmppResPolicy 9)==49;;

const XMPP_RES_BUDGET=200000;;	// bytes kept, all resources together
const XMPP_RES_KEEP=3;;	// resources kept
var XmppResHead="<res xmlns='violet:nabaztag:res' ";;
var XmppResStore;;	// [id len records], newest first
var XmppResLeft=0;;	// raw bytes still owed by the warren
var XmppResId=0;;
var XmppResLen=0;;
var XmppResKeep=0;;	// keep what arrives, or read it and drop it
var XmppResParts;;	// the records received so far, newest first

// The number in name='123', or nil.
fun XmppResAttr r name=
	let strstr r name 0 -> i in
	if i==nil then nil else atoi strsub r (i+strlen name) nil;;

// The newest resources that leave room for `room` bytes, k at most.
fun XmppResTrim l room k=
	if l==nil || k<=0 then nil
	else let hd l -> [i n d] in
	if n>room then nil
	else (hd l)::XmppResTrim tl l (room-n) (k-1);;

fun XmppResFind l id=
	if l==nil then nil
	else let hd l -> [i n d] in
	if i==id then d else XmppResFind tl l id;;

fun XmppResBegin r=
	let XmppResAttr r " id='" -> id in
	let XmppResAttr r " len='" -> n in
	(
		set XmppResParts=nil;
		set XmppResId=id;
		set XmppResLen=if n==nil then 0 else n;
		set XmppResLeft=XmppResLen;
		set XmppResKeep=id!=nil && XmppResLen>0 && XmppResLen<=XMPP_RES_BUDGET;
		if XmppResKeep then set XmppResStore=XmppResTrim XmppResStore (XMPP_RES_BUDGET-XmppResLen) (XMPP_RES_KEEP-1);
		Secho "res: announced "; Iecho XmppResLen; Secho " bytes at "; Iecholn time_ms;
		0
	);;

fun XmppResDone=
	if XmppResKeep then
	(
		set XmppResStore=[XmppResId XmppResLen (rev XmppResParts nil)]::XmppResStore;
		Secho "res: received "; Iecho XmppResLen; Secho " bytes at "; Iecholn time_ms;
		0
	);
	set XmppResParts=nil;
	set XmppResLeft=0;
	0;;

// The decrypted records, without the ones that carry a resource.
fun XmppResSift l=
	if l==nil || !XmppResInSession then l
	else let hd l -> r in
	if XmppResLeft>0 then
	(
		if XmppResKeep then set XmppResParts=r::XmppResParts;
		set XmppResLeft=XmppResLeft-(strlen r);
		if XmppResLeft<=0 then XmppResDone;
		XmppResSift tl l
	)
	else if (strlen r)>=(strlen XmppResHead) && !strcmp (strsub r 0 strlen XmppResHead) XmppResHead then
	(
		XmppResBegin r;
		XmppResSift tl l
	)
	else r::XmppResSift tl l;;

'''

DRAIN_OLD = "\t\tlet strcatlist XmppTlsDrain msg -> plain in\n"
DRAIN_NEW = "\t\tlet strcatlist XmppResSift XmppTlsDrain msg -> plain in\n"

SOCK_OLD = "\t\t\tset XmppTlsSock=t;\n"
SOCK_NEW = ("\t\t\tset XmppTlsSock=t;\n"
            "\t\t\t// res-in-session: a new session owes no raw bytes\n"
            "\t\t\tset XmppResLeft=0;\n"
            "\t\t\tset XmppResParts=nil;\n")

PLAY_ANCHOR = ("//********************************************************\n"
               "// Executes the i0'th command in the 'gCurrentProgram' program\n")
PLAY_FUNS = '''// res-in-session: `ST res:N` plays resource N, received inside the session
// (XmppResSift); 1 when the value was such a name, played or not. An N not
// in the store stops the player, which ends the stream as a failed fetch.
fun XmppResPlay val=
	if val==nil || (strlen val)<5 || strcmp (strsub val 0 4) "res:" then 0
	else let XmppResFind XmppResStore (atoi strsub val 4 nil) -> d in
	(
		Secho "res: ST "; Secho val; Secho (if d==nil then " not received, at " else " from the session, at "); Iecholn time_ms;
		if d==nil then (wavstop; 0) else (wavstartlocal d; 0);
		1
	);;

'''

ST_OLD = ("\t\tlet rscfilterurl val -> music in wavstarthttp music #_cbWavError;\n"
          "\t\tset run=streamCmdRun i0;\n")
ST_NEW = ("\t\tif !(XmppResPlay val) then (let rscfilterurl val -> music in wavstarthttp music #_cbWavError; 0);\n"
          "\t\tset run=streamCmdRun i0;\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments

    if MARKER in text:
        print(f"[skip] {path}: resources already travel inside the session")
        return False
    if TLS_MARKER not in text or "fun XmppTlsDrain" not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTlsDrain)")
    if LIVENESS_MARKER not in text:
        sys.exit(f"FAIL {path}: run patch_liveness.py first")
    for what, needle in (("the TLS XmppTcpRead", READ_ANCHOR),
                         ("XmppTlsDrain in XmppTcpRead", DRAIN_OLD),
                         ("the new-socket branch of XmppTcpRead", SOCK_OLD),
                         ("runEvalOneCommand's comment", PLAY_ANCHOR),
                         ("the ST command's fetch", ST_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    for name in ("wavstartlocal", "wavstop", "rev"):
        if f"fun {name} " not in text and f"fun {name}=" not in text:
            # audiolib.mtl and utils.mtl are included, not inlined: check the tree.
            src = path.parent
            if not any(f"fun {name} " in p.read_text("latin-1") or f"fun {name}=" in p.read_text("latin-1")
                       for p in src.glob("*.mtl")):
                sys.exit(f"FAIL {path}: {name} is missing; this is not the Violet tree")

    text = text.replace(READ_ANCHOR, SIFT_FUNS + READ_ANCHOR, 1)
    text = text.replace(DRAIN_OLD, DRAIN_NEW, 1)
    text = text.replace(SOCK_OLD, SOCK_NEW, 1)
    text = text.replace(PLAY_ANCHOR, PLAY_FUNS + PLAY_ANCHOR, 1)
    text = text.replace(ST_OLD, ST_NEW, 1)

    for s in (SIFT_FUNS, DRAIN_NEW, SOCK_NEW, PLAY_FUNS, ST_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: resources announced inside the TLS session are played from it")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
