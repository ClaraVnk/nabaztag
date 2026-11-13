#!/usr/bin/env python3
"""A test runtime only: the rabbit tells the warren each step of an update.

2026-11-13. On Nabazprout (Naboot 1.2.0.0, the HTTPS runtime) an update
sent down the session never installed: the warren pushed the 127 KB image,
the rabbit went quiet, no LED of the update came on, and it answered nothing
more until it was unplugged. The same runtime installs the same image in the
emulator, and the TLS runtime installs it on the rabbit. Something on the
real rabbit stops it -- the heap is the first suspect: ota_flash leaves
~36 k words free for verifySig's ~27 k, measured with the smaller runtime.

So each step sends a <message> with an <ota> element, which the warren logs
as it logs every stanza: the image announced (and whether its buffer could be
allocated), received whole or dropped, taken by the FW command, after the
collection, after verifySig, before the flash. The last line logged is where
it stopped. Never in a release: build-bytecode.sh applies it with
OTA_REPORT=1 only.

Applied to main.mtl and fwota.mtl in a sources directory, after
patch_ota_in_session.py. Idempotent.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ota-report */"

SAY = '''/* ota-report */
// Test runtime only (bytecode/patch_ota_report.py): one step of an update,
// to the warren, which logs it.
fun OtaSay s=
	Secholn s;
	if XmppGlobalSession!=nil then
		XmppGateSend XmppGlobalSession (xmpps_createStanza XmppGlobalSession XmppGlobalSession.sJID
			(xmpps_PlatformJID XmppGlobalSession XmppGlobalSession.sClients.clINT) stanzaMessage
			(strcatlist "<ota>"::s::"</ota>"::nil));
	0;;
'''

MAIN = [
    ("fun XmppFwBegin id n=\n", SAY + "fun XmppFwBegin id n=\n", "XmppFwBegin"),
    ('\tSecho "ota: announced "; Iecho n; Secho " bytes at "; Iecholn time_ms;\n',
     '\tOtaSay strcatlist "announced "::(itoa n)::(if XmppFwBuf==nil then " NO-BUFFER" else " buffer-ok")::nil;\n',
     "the announce"),
    ('(set XmppFwId=XmppFwRid; Secho "ota: received "; Iecho XmppFwOff; Secho " bytes at "; Iecholn time_ms; 0)',
     '(set XmppFwId=XmppFwRid; OtaSay strcatlist "received "::(itoa XmppFwOff)::nil; 0)',
     "received"),
    ('(set XmppFwBuf=nil; Secholn "ota: the image did not arrive whole, dropped"; 0)',
     '(set XmppFwBuf=nil; OtaSay "dropped: not whole"; 0)', "dropped"),
    ('(Secholn "ota: FW res: without its image"; led 0 OTA_STEP_BAD; 1)',
     '(OtaSay "FW without its image"; led 0 OTA_STEP_BAD; 1)', "FW without image"),
    ('\t\tSecho "ota: installing "; Iecho strlen firm; Secho " bytes from the session at "; Iecholn time_ms;\n',
     '\t\tOtaSay strcatlist "installing "::(itoa strlen firm)::nil;\n', "installing"),
]

FWOTA = [
    ("\tgc;\n\tif (verifySig firm sig)==0 then\n",
     "\tgc;\n\tOtaSay \"collected, verifying\";\n\tif (verifySig firm sig)==0 then\n", "verifySig"),
    ("\t\t(led 2 OTA_STEP_BAD; ota_fail_report 0xF3; 0)\n",
     "\t\t(OtaSay \"signature refused\"; led 2 OTA_STEP_BAD; ota_fail_report 0xF3; 0)\n", "refused"),
    ("\t\tled 2 OTA_STEP_SIG;\n\t\tled 3 OTA_STEP_RUN;\n",
     "\t\tOtaSay \"signature ok, flashing\";\n\t\tled 2 OTA_STEP_SIG;\n\t\tled 3 OTA_STEP_RUN;\n", "flashing"),
]


def _edit(path: Path, edits, proto: bool) -> None:
    raw = path.read_bytes()
    try:
        text, enc = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, enc = raw.decode("latin-1"), "latin-1"
    for old, new, what in edits:
        if text.count(old) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(old)} "
                     "(patch_ota_in_session.py first)")
        text = text.replace(old, new, 1)
    if proto:
        text = "proto OtaSay 1;;\t/* ota-report */\n" + text
    path.write_bytes(text.encode(enc))


def patch(src: Path) -> bool:
    main, fw = src / "main.mtl", src / "fwota.mtl"
    if MARKER in main.read_bytes().decode("latin-1"):
        print(f"[skip] {src}: updates already report their steps")
        return False
    _edit(main, MAIN, proto=False)
    _edit(fw, FWOTA, proto=True)
    print(f"[ok]   {src}: each step of an update goes to the warren (TEST RUNTIME)")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
