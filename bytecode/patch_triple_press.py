#!/usr/bin/env python3
"""Three presses of the head restart a rabbit on its server into config mode.

The bootloader already takes three presses while the rabbit is offline
(firmware-arm/boot-mods/triplepress.mtl). Online, the head is the runtime's
(Clara, 2026-11-22: "On veut aussi quand il est connecté au terrier. Comme
le karotz"). Violet's button driver reports a double click and then a
click; here a click less than 1.5 s after a double click calls configBoot,
the opcode of Naboot 1.2.1.3 that leaves a mark the bootloader reads at
start (apply-mods.py config_boot) and reboots: the rabbit comes back as its
access point with the setup page. The double click has gone to the warren
as before.

configBoot exists only on Naboot 1.2.1.3 and later: on an older firmware
the call is an unknown opcode. The literal "cfg-boot:0" says so as
compiled; the warren flips it to 1 for a rabbit whose firmware has it, as
it does the RFID policy. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* triple-press-config */"

FUNS = '''/* triple-press-config */
// A click less than 1.5 s after a double click restarts into config mode,
// on a firmware with configBoot only (the warren flips the digit to 1).
// See bytecode/patch_triple_press.py.
var XmppCfgBootPolicy="cfg-boot:0";;
fun XmppCfgBootOn= (strget XmppCfgBootPolicy 9)==49;;
var gTripleDclic=0;;
fun tripleCheck ev=
	if ev==BUTTON_DCLIC then (set gTripleDclic=time_ms; 0)
	else if ev==BUTTON_CLIC && gTripleDclic!=0 && (time_ms-gTripleDclic)<1500 && XmppCfgBootOn then
	(
		set gTripleDclic=0;
		configBoot
	)
	else (set gTripleDclic=0; 0);;

'''
ANCHOR = "fun controlwait=\n"
OLD = ("fun controlwait=\n\tlet buttongetevent -> ev in\n"
       "\tlet if ev==nil then eargetevent else ev -> ev in\n"
       "\tif ev!=nil then\n\t(\n")
NEW = OLD + "\t\tif ev==BUTTON_CLIC || ev==BUTTON_DCLIC then tripleCheck ev;\n"


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: three presses already restart into config mode")
        return False
    if text.count(OLD) != 1:
        sys.exit(f"FAIL {path}: controlwait is not the shape this patch expects")
    text = text.replace(OLD, NEW, 1)
    text = text.replace(ANCHOR, FUNS + ANCHOR, 1)
    for s in (FUNS, NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a double click and a click restart into config mode")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
