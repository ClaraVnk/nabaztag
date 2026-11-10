#!/usr/bin/env python3
"""A rabbit that speaks in its sleep goes back to sleep.

Violet's runtime ends every program the same way: endOfProgram sets
`run = idleRun`, and stopStream, at the end of a streamed sound, asks the
server for the `idle` resource. Neither looks at gSleepState. Violet never
spoke to a sleeping rabbit, so it never mattered; the warren does, when the
owner shows it a tag during its quiet hours. Seen on a real rabbit on
2026-11-10 at 23:51: asleep, a tag, the sentence played, then the rabbit
bound `idle` with gSleepState still 1 -- ears up from the stream, the nose
lit by idleRun, and every later sleep or wake command ignored (startSleep
and endSleep both trust gSleepState) until it was unplugged.

Here endOfProgram, for a rabbit whose gSleepState says it is asleep, goes
back to sleep the way the server's sleep command does it -- startSleep with
gSleepState cleared first, so it is a real falling asleep: the violet
belly, the ears reset, the `asleep` resource, then the lights off and the
ears down (_goSleepNow, sleepRun). An awake rabbit ends its program as
before.

What "spoke in its sleep" means is the bug itself: stopStream announced
`idle` while gSleepState was 1. It now notes that (gWokeToPlay), and only
then does endOfProgram fall asleep again. Two other programs end on a
sleeping rabbit and must not: the sleep command itself, whose endOfProgram
finds the ears resetting -- stock Violet set `run = idleRun` there, over
startSleep's earResetWaitRun, so _goSleepNow never ran; now run is left
alone and the rabbit lies down -- and anything else played in its sleep
without a stream, after which it simply stays in sleepRun instead of
idleRun. A sleep command sent to a sleeping rabbit therefore costs no
second ear reset.

Independent of the session patches; build-bytecode.sh applies it after
them. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* sleep-after-program */"

PROTO_ANCHOR = "fun endOfProgram doNotNotify =\n"
PROTO = ("/* sleep-after-program */\n"
         "// endOfProgram puts a rabbit that spoke in its sleep back to sleep.\n"
         "// See bytecode/patch_sleep_after_program.py.\n"
         "var gWokeToPlay = 0;;\t// a stream ended while asleep: it announced idle\n"
         "proto startSleep 0;;\n\n")
END_OLD = "\t\t// retour normal\n\t\tset run = idleRun;\n\t\t0\n"
END_NEW = ("\t\t// retour normal -- and asleep before the program, asleep after it:\n"
           "\t\t// the ears resetting means it WAS the sleep command, whose\n"
           "\t\t// earResetWaitRun lies the rabbit down; a stream that announced idle\n"
           "\t\t// means it spoke, and falls asleep again for real; else it stays asleep.\n"
           "\t\tif !gSleepState then (set run = idleRun; 0)\n"
           "\t\telse if earResetting then 0\n"
           "\t\telse if gWokeToPlay then (set gWokeToPlay = 0; set gSleepState = 0; startSleep; 0)\n"
           "\t\telse (set run = sleepRun; 0)\n")
STREAM_OLD = "fun stopStream=\n  Streamw \"stopStream\";\n"
STREAM_NEW = ("fun stopStream=\n  Streamw \"stopStream\";\n"
              "\tif gSleepState then set gWokeToPlay = 1;\t// sleep-after-program\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: a sleeping rabbit already goes back to sleep")
        return False
    for what, needle in (("endOfProgram", PROTO_ANCHOR),
                         ("endOfProgram's return to idle", END_OLD),
                         ("startSleep", "fun startSleep=\n"),
                         ("stopStream", STREAM_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(PROTO_ANCHOR, PROTO + PROTO_ANCHOR, 1)
    text = text.replace(END_OLD, END_NEW, 1)
    text = text.replace(STREAM_OLD, STREAM_NEW, 1)
    for s in (PROTO, END_NEW, STREAM_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: a program played in the rabbit's sleep ends with it asleep")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
