#!/usr/bin/env python3
"""Over-the-air updates come down the TLS session.

`FW <url>` (fwota.mtl) downloads the firmware image over plain HTTP. The
image is signed, so nobody on the path can make a rabbit install anything
else; what the path still learns is which firmware the rabbit is about to
run, and when. The session the rabbit already has runs TLS 1.3
(patch_tls.py) and already carries raw resources (patch_res_in_session.py),
so the image goes there too.

The warren announces it like a sound, with one more attribute:

    <res xmlns='violet:nabaztag:res' id='N' len='L' fw='1'/>   then L bytes
    `FW res:N <128 hex digits of the Ed25519 signature>`

An image is not a sound, and is not kept like one. XmppResBegin, on fw='1',
drops the stored sounds (an update is about to reboot the rabbit; they will
not be played) and allocates one buffer of L bytes; XmppResSift copies each
record into it, as fwota.mtl's binary path copies each HTTP segment, and
leaves the sound store alone. Live at the signature gate: the image once and
verifySig's scratch once, as on the binary path -- not a list of records
plus a copy of them. An image longer than the flash's 126 976 bytes, or
shorter than announced, is dropped.

`FW res:N <sig>` hands that buffer and signature to fwota.mtl's ota_flash,
the one signature gate there is: verifySig, then flashFirmware, the belly
lights saying how far it got. A res:N the rabbit did not receive whole
lights the first one red and installs nothing. Any other FW is the URL it
always was.

Its own literal, "xmpp-ota:0", flipped to 1 under the HMAC by a warren that
sends updates this way; as compiled every announce is a sound and every FW
a URL.

Applied after patch_res_in_session.py and patch_main.py (fwota.mtl and the
FW command); build-bytecode.sh does. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ota-in-session */"

RES_MARKER = "/* res-in-session */"
BEGIN_ANCHOR = "fun XmppResBegin r=\n"
FW_FUNS = '''/* ota-in-session */
// A firmware image the warren sends inside the session: one buffer, filled
// record by record. See bytecode/patch_ota_in_session.py. The warren flips
// the digit to 1 before signing when it sends updates this way.
var XmppOtaPolicy="xmpp-ota:0";;
fun XmppOtaInSession= (strget XmppOtaPolicy 9)==49;;
const XMPP_FW_MAX=126976;;	// the flash the firmware may take
var XmppFwOn=0;;	// the resource being received is an image
var XmppFwBuf;;	// the image, filling up
var XmppFwOff=0;;	// bytes of it received
var XmppFwRid;;	// its id
var XmppFwId;;	// the id of the image XmppFwBuf holds whole, or nil
fun XmppFwBegin id n=
	set XmppFwId=nil;
	set XmppFwRid=id;
	set XmppFwOff=0;
	set XmppResStore=nil;	// sounds: an update is about to reboot the rabbit
	set XmppFwBuf=nil;
	if id!=nil && n>0 && n<=XMPP_FW_MAX then (set XmppFwBuf=strnew n; 0);
	Secho "ota: announced "; Iecho n; Secho " bytes at "; Iecholn time_ms;
	0;;
fun XmppFwChunk r=
	if XmppFwBuf!=nil then
	(
		let strlen r -> n in
		if XmppFwOff+n > strlen XmppFwBuf then (set XmppFwBuf=nil; 0)
		else (strcpy XmppFwBuf XmppFwOff r 0 n; set XmppFwOff=XmppFwOff+n; 0)
	);
	0;;
fun XmppFwDone=
	set XmppFwOn=0;
	if XmppFwBuf!=nil && XmppFwOff==strlen XmppFwBuf then
		(set XmppFwId=XmppFwRid; Secho "ota: received "; Iecho XmppFwOff; Secho " bytes at "; Iecholn time_ms; 0)
	else
		(set XmppFwBuf=nil; Secholn "ota: the image did not arrive whole, dropped"; 0);
	0;;
'''
KEEP_OLD = ("\t\tset XmppResKeep=id!=nil && XmppResLen>0 && XmppResLen<=XMPP_RES_BUDGET;\n"
            "\t\tif XmppResKeep then set XmppResStore=XmppResTrim XmppResStore (XMPP_RES_BUDGET-XmppResLen) (XMPP_RES_KEEP-1);\n")
KEEP_NEW = ("\t\tset XmppFwOn=XmppOtaInSession && (strstr r \" fw='1'\" 0)!=nil;\n"
            "\t\tset XmppResKeep=!XmppFwOn && id!=nil && XmppResLen>0 && XmppResLen<=XMPP_RES_BUDGET;\n"
            "\t\tif XmppFwOn then XmppFwBegin id XmppResLen;\n"
            "\t\tif XmppResKeep then set XmppResStore=XmppResTrim XmppResStore (XMPP_RES_BUDGET-XmppResLen) (XMPP_RES_KEEP-1);\n")
DONE_OLD = "fun XmppResDone=\n\tif XmppResKeep then\n"
DONE_NEW = "fun XmppResDone=\n\tif XmppFwOn then XmppFwDone;\n\tif XmppResKeep then\n"
PART_OLD = "\t\tif XmppResKeep then set XmppResParts=r::XmppResParts;\n"
PART_NEW = ("\t\tif XmppFwOn then XmppFwChunk r\n"
            "\t\telse if XmppResKeep then (set XmppResParts=r::XmppResParts; 0);\n")

INCLUDE = "#include fwota.mtl\n"
TAKE_FUNS = '''/* ota-in-session: the FW command */
// `FW res:N <128 hex>`: the image the session carried as N, and its
// signature, to fwota.mtl's one signature gate. 1 when the value was such a
// name, installed or not; 0 for a URL.
fun XmppFwTake val=
	if val==nil || (strlen val)<5 || strcmp (strsub val 0 4) "res:" then 0
	else let strstr val " " 0 -> sp in
	if sp==nil || (strlen val)-sp-1!=128 || XmppFwId==nil || XmppFwId!=(atoi strsub val 4 sp-4) then
		(Secholn "ota: FW res: without its image"; led 0 OTA_STEP_BAD; 1)
	else let strnew 64 -> sig in
	let XmppFwBuf -> firm in
	(
		getbinary_fw sig val 0 (sp+1) 64;
		set XmppFwBuf=nil;
		set XmppFwId=nil;
		set g_ota_url="";	// nothing to report a failure to over HTTP
		Secho "ota: installing "; Iecho strlen firm; Secho " bytes from the session at "; Iecholn time_ms;
		led 0 OTA_STEP_OK;
		led 1 OTA_STEP_OK;
		ota_flash firm sig;
		1
	);;

'''
FW_OLD = '\t\telse if !strcmp key "FW" then (flashFromUrl val;0)\n'
FW_NEW = '\t\telse if !strcmp key "FW" then (if !(XmppFwTake val) then flashFromUrl val;0)\n'


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: updates already come down the session")
        return False
    if RES_MARKER not in text:
        sys.exit(f"FAIL {path}: run patch_res_in_session.py first")
    for what, needle in (("XmppResBegin", BEGIN_ANCHOR),
                         ("XmppResBegin's keep test", KEEP_OLD),
                         ("XmppResDone", DONE_OLD),
                         ("XmppResSift's append", PART_OLD),
                         ("the fwota include", INCLUDE),
                         ("the FW command", FW_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(BEGIN_ANCHOR, FW_FUNS + BEGIN_ANCHOR, 1)
    text = text.replace(KEEP_OLD, KEEP_NEW, 1)
    text = text.replace(DONE_OLD, DONE_NEW, 1)
    text = text.replace(PART_OLD, PART_NEW, 1)
    text = text.replace(INCLUDE, INCLUDE + TAKE_FUNS, 1)
    text = text.replace(FW_OLD, FW_NEW, 1)
    for s in (FW_FUNS, KEEP_NEW, DONE_NEW, PART_NEW, TAKE_FUNS, FW_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: `FW res:N <sig>` installs an image received inside the session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
