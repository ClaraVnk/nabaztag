#!/usr/bin/env python3
"""Where -Os changes how the C touches memory, file by file.

1.2.0.2 (1.2.0.1 at -Os) never reached the network on the test rabbit
(2026-11-16, docs/FLASH-MAP.md); the emulators cannot see the radio's
bring-up. -Os is about -O2, and turns on what -O1 leaves off: strict
aliasing, loops turned into memcpy/memset calls, adjacent narrow stores
merged into wide ones. On a peripheral's registers, or on an ARM7, where a
word store to an unaligned address writes the aligned word, each of those
can break a driver and nothing else.

This compares two builds' assembly listings (obj/lst/<source>.lst, written
by the build): for each source file, the calls to memcpy, memset and
memmove, and the stores and loads by width. A file whose byte stores drop
while its word stores rise, or that gains a memset, is where to look.

    os-diff.py <lst dir at -O1> <lst dir at -Os> [path prefix ...]

tools/os-diff.sh extracts both directories from the build images.
"""
import re
import sys
from pathlib import Path

CALL = re.compile(r"\bbl\s+(memcpy|memset|memmove|__aeabi_mem\w+)\b")
OPS = {
    "strb": re.compile(r"\tstrb\b"), "strh": re.compile(r"\tstrh\b"),
    "str": re.compile(r"\tstr\s"), "stm": re.compile(r"\tstm\w*\b"),
    "ldrb": re.compile(r"\tldrb\b"), "ldrh": re.compile(r"\tldrh\b"),
    "ldr": re.compile(r"\tldr\s"), "ldm": re.compile(r"\tldm\w*\b"),
}


def count(path: Path) -> dict:
    c = dict.fromkeys(["mem"] + list(OPS), 0)
    for line in path.read_text(errors="replace").splitlines():
        if CALL.search(line):
            c["mem"] += 1
        for k, rx in OPS.items():
            if rx.search(line):
                c[k] += 1
    return c


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    prefixes = sys.argv[3:]
    rows = []
    for fa in sorted(a.rglob("*.lst")):
        rel = fa.relative_to(a).as_posix()
        if prefixes and not any(rel.startswith(p) for p in prefixes):
            continue
        fb = b / rel
        if not fb.exists():
            continue
        ca, cb = count(fa), count(fb)
        d = {k: cb[k] - ca[k] for k in ca}
        # what the three suspects leave behind: new library calls, narrow
        # stores turned wide
        flag = d["mem"] > 0 or (d["strb"] < 0 and (d["str"] > 0 or d["stm"] > 0)) \
            or (d["strh"] < 0 and d["str"] > 0)
        rows.append((flag, rel, ca, cb))
    print(f"{'file':42} {'mem':>7} {'strb':>9} {'strh':>9} {'str':>9} {'stm':>7}")
    for flag, rel, ca, cb in sorted(rows, key=lambda r: (not r[0], r[1])):
        cell = lambda k: f"{ca[k]}->{cb[k]}"
        print(f"{'!' if flag else ' '}{rel:41} {cell('mem'):>7} {cell('strb'):>9} "
              f"{cell('strh'):>9} {cell('str'):>9} {cell('stm'):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
