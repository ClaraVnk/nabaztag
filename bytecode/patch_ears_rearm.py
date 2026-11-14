#!/usr/bin/env python3
"""An ear turned by hand is noticed again after a lost session.

The runtime only watches for a hand on an ear in EARSMODE_WAITANDDETECT,
and only two places put it there: the end of the tai-chi and _wakeupNow,
after the boot's ear reset. stopEverythingAndResetToIdle -- what a lost
XMPP session runs -- starts an ear reset too, then goes straight back to
idleRun: the reset ends in EARSMODE_WAIT and nothing re-arms the detection
until the next tai-chi or reboot. A turned ear then gives no chime, no
<ears> stanza, and the "amoureux" partner never moves.

Measured on 2026-11-14 with patch_ears_telemetry.py: Nabazprout, 30 s
after a boot during which its session was closed and reopened, sat in mode
1 (EARSMODE_WAIT) and stayed there. Two rabbits had never reported a turned
ear.

Now idleRun re-arms it on every pass: earSetWaitAndDetectMode only turns
EARSMODE_WAIT into EARSMODE_WAITANDDETECT, so a reset in progress, the
tai-chi's wait-only mode (it runs as chorCmdRun, not idleRun) and the
detection itself are left alone. Moves the warren commands in idle are
not mistaken for a hand: earWaitRun only counts a still ear's motion.

Patches sources/main.mtl. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ears-rearm */"

OLD = "\t( idleRun -> earRun;infoRun;bottomled;noseled;runIdle;CONTROL_WAIT) |\n"
NEW = ("\t( idleRun -> earRun;earSetWaitAndDetectMode /* ears-rearm */;"
       "infoRun;bottomled;noseled;runIdle;CONTROL_WAIT) |\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # Violet's sources carry 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: ear detection already re-armed in idle")
        return False
    if text.count(OLD) != 1:
        sys.exit(f"FAIL {path}: expected one idleRun line, found {text.count(OLD)}")
    text = text.replace(OLD, NEW, 1)
    assert NEW.isascii(), "what we insert must be ASCII: the sources are latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: an ear turned by hand is noticed again after a lost session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
