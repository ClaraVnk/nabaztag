#!/usr/bin/env python3
"""A measurement runtime: the ears' state, every 30 s, inside the session.

On 2026-11-14 an ear turned by hand on two real rabbits (Naboot 1.2.0.1,
the warren's baseline-https runtime) gave nothing: no chime, no ears put
back, no <ears> stanza, so the "amoureux" pairing never moved the partner.
The same runtime in the emulator (scenarios/vm/xmpp-ears.txt) sends it
every time. What differs is on the rabbit, and only its state can say what:
an ear marked broken at the boot reset (brokenE, never watched again), the
ears in another mode than EARSMODE_WAITANDDETECT, the interactive state.

So every 30 s, while the session is open, the rabbit sends

    <earsdbg xmlns="violet:nabaztag:telemetry"><m>mode</m><b>b0,b1</b>
    <v>motorget 0,1</v><l>lvalE 0,1</l><d>dirE 0,1</d><it>gItState</it>
    <s>gSleepState</s><p>gProcessingState</p></earsdbg>

which the warren logs as any message. -1 stands for nil. Not
"violet:nabaztag:earsdbg": the warren matches "violet:nabaztag:ears" anywhere
in a stanza and took each report for a turned ear (emulator, 2026-11-14). Not part of the
normal runtime: build-bytecode.sh applies it only with EARS_TELEMETRY=1.

Patches sources/main.mtl. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ears-telemetry */"

OLD = "fun loop=\n"
NEW = ("/* ears-telemetry */\n"
       "// See bytecode/patch_ears_telemetry.py.\n"
       "var earsTelLast = 0;;\n"
       "fun earsTelI v = itoa if v == nil then (0-1) else v;;\n"
       "fun earsTelPair a b = strcatlist (earsTelI a)::\",\"::(earsTelI b)::nil;;\n"
       "fun earsTelemetry =\n"
       "\tif XmppGlobalSession != nil && earsInited && time_ms - earsTelLast > 30000 then\n"
       "\tmatch XmppGlobalSession.sStatus with\n"
       "\t(sOpened -> (\n"
       "\t\tset earsTelLast = time_ms;\n"
       "\t\tlet ears.(0) -> e0 in let ears.(1) -> e1 in\n"
       "\t\txmpps_sendStanza\n"
       "\t\t\tXmppGlobalSession\n"
       "\t\t\tnil\n"
       "\t\t\txmpps_PlatformJID XmppGlobalSession XmppGlobalSession.sClients.clINT\n"
       "\t\t\tstanzaMessage\n"
       "\t\t\tstrcatlist \"<earsdbg xmlns=\\\"violet:nabaztag:telemetry\\\"><m>\"::(earsTelI earsmode)\n"
       "\t\t\t\t::\"</m><b>\"::(earsTelPair e0.brokenE e1.brokenE)\n"
       "\t\t\t\t::\"</b><v>\"::(earsTelPair (motorget 0) (motorget 1))\n"
       "\t\t\t\t::\"</v><l>\"::(earsTelPair e0.lvalE e1.lvalE)\n"
       "\t\t\t\t::\"</l><d>\"::(earsTelPair e0.dirE e1.dirE)\n"
       "\t\t\t\t::\"</d><it>\"::(earsTelI gItState)\n"
       "\t\t\t\t::\"</it><s>\"::(earsTelI gSleepState)\n"
       "\t\t\t\t::\"</s><p>\"::(earsTelI gProcessingState)\n"
       "\t\t\t\t::\"</p></earsdbg>\"::nil;\n"
       "\t\t0))\n"
       "\t|(_ -> 0)\n"
       "\telse 0;;\n\n"
       "fun loop=\n"
       "\tearsTelemetry;\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # Violet's sources carry 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: ears telemetry already there")
        return False
    if text.count(OLD) != 1:
        sys.exit(f"FAIL {path}: expected one `fun loop=`, found {text.count(OLD)}")
    text = text.replace(OLD, NEW, 1)
    assert NEW.isascii(), "what we insert must be ASCII: the sources are latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: the ears' state every 30 s inside the session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
