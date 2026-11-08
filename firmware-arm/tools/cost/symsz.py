#!/usr/bin/env python3
"""Print text/rodata symbol sizes from `nm -S` as decimal bytes."""
import sys
for line in sys.stdin:
    f = line.split()
    if len(f) == 4 and f[2] in "tTrR":
        print(f"   {int(f[1], 16):6d} {f[3]}")
