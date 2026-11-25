#!/usr/bin/env python3
"""Flash and RAM use of the firmware: from its hex (data bytes placed below
the configuration words) and gplink's map (the data sections)."""
import re
import sys

FLASH = 48 * 1024
RAM = 3968

hexfile, mapfile = sys.argv[1], sys.argv[2]
base, flash, top = 0, 0, 0
for line in open(hexfile):
    if not line.startswith(":"):
        continue
    n, addr, kind = int(line[1:3], 16), int(line[3:7], 16), int(line[7:9], 16)
    if kind == 4:
        base = int(line[9:13], 16) << 16
    elif kind == 0 and base == 0:
        flash += n
        top = max(top, addr + n)

ram = 0
in_sections = False
for line in open(mapfile):
    if line.startswith("                                 Section Info"):
        in_sections = True
        continue
    if in_sections and line.strip() == "":
        if ram:
            break
        continue
    m = re.match(r"\s*(\S+)\s+(udata|idata|udata_ovr|udata_shr)\s+0x([0-9a-f]+)\s+\S+\s+0x([0-9a-f]+)", line)
    if in_sections and m:
        ram += int(m.group(4), 16)
print(f"flash {flash} / {FLASH} bytes ({100 * flash // FLASH}%), highest address 0x{top:04X}")
print(f"ram {ram} / {RAM} bytes ({100 * ram // RAM}%)")
