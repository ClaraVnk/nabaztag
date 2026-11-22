#!/usr/bin/env python3
"""Three short presses of the head restart a rabbit on its server into config mode.

The bootloader already takes three presses while the rabbit is offline
(firmware-arm/boot-mods/triplepress.mtl). Online, the head is the runtime's
(Clara, 2026-11-22: "On veut aussi quand il est connecté au terrier. Comme
le karotz"). Violet's button driver (button.mtl) counts presses until
500 ms of quiet and reports one click or, for two presses or more, a
double click: three short presses were a double click. Here a third press
makes BUTTON_TCLIC, and controlwait calls configBoot for it, the opcode of
Naboot 1.2.1.3 that leaves a mark the bootloader reads at start
(apply-mods.py config_boot) and reboots: the rabbit comes back as its
access point with the setup page. First try, a click after a double click,
never fired: the third press was counted into the double click (emulator,
2026-11-22).

configBoot exists only on Naboot 1.2.1.3 and later. The literal
"cfg-boot:0" says so as compiled, and then three presses are a double
click, as they always were; the warren flips it to 1 for a rabbit whose
firmware has the opcode, as it does the RFID policy. Takes the sources
directory. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* triple-press-config */"

BTN_CONST_OLD = "const BUTTON_DLCLIC=4;;\n"
BTN_CONST_NEW = BTN_CONST_OLD + '''/* triple-press-config */
// Three presses or more: a triple click, on a firmware with configBoot only
// (the warren flips the digit to 1); a double click otherwise, as Violet's.
// See bytecode/patch_triple_press.py.
const BUTTON_TCLIC=5;;
var XmppCfgBootPolicy="cfg-boot:0";;
fun XmppCfgBootOn= (strget XmppCfgBootPolicy 9)==49;;
'''
BTN_EVENT_OLD = "\t\t\tset buttonevent=if buttonclicn==1 then BUTTON_CLIC else BUTTON_DCLIC;\n"
BTN_EVENT_NEW = ("\t\t\tset buttonevent=if buttonclicn==1 then BUTTON_CLIC\n"
                 "\t\t\t\telse if buttonclicn>=3 && XmppCfgBootOn then BUTTON_TCLIC else BUTTON_DCLIC;\n")

MAIN_OLD = ("fun controlwait=\n\tlet buttongetevent -> ev in\n"
            "\tlet if ev==nil then eargetevent else ev -> ev in\n"
            "\tif ev!=nil then\n\t(\n")
MAIN_NEW = MAIN_OLD + "\t\tif ev==BUTTON_TCLIC then configBoot;\t/* triple-press-config */\n"


def _read(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"      # main.mtl carries 8-bit comments


def patch(src: Path) -> bool:
    btn, main = src / "button.mtl", src / "main.mtl"
    b, b_enc = _read(btn)
    m, m_enc = _read(main)
    if MARKER in b:
        print(f"[skip] {src}: three presses already restart into config mode")
        return False
    for what, text, needle in (("button.mtl's constants", b, BTN_CONST_OLD),
                               ("button.mtl's event", b, BTN_EVENT_OLD),
                               ("controlwait", m, MAIN_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {src}: {what} is not the shape this patch expects")
    b = b.replace(BTN_CONST_OLD, BTN_CONST_NEW, 1).replace(BTN_EVENT_OLD, BTN_EVENT_NEW, 1)
    m = m.replace(MAIN_OLD, MAIN_NEW, 1)
    for s in (BTN_CONST_NEW, BTN_EVENT_NEW, MAIN_NEW):
        assert s.isascii(), "what we insert must be ASCII"
    btn.write_bytes(b.encode(b_enc))
    main.write_bytes(m.encode(m_enc))
    print(f"[ok]   {src}: three short presses restart into config mode (cfg-boot)")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
