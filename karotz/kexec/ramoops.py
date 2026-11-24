#!/usr/bin/env python3
"""The 6.1 console a Karotz kept in RAM (ramoops), from the 1 MiB the key's
autorun saved after a watchdog restart (kexec/log/N-ramoops.bin).

    karotz/kexec/ramoops.py <N-ramoops.bin>

The region is the command line's: 0x100000 bytes, records of 0x10000 then
the console zone, its last 0xC0000. Each zone starts with "DBGC", the start
and the size of its ring (linux fs/pstore/ram_core.c, struct
persistent_ram_buffer). Prints every zone that holds text, oldest first.
"""
import struct
import sys

raw = open(sys.argv[1], "rb").read()
# The zones are found by their "DBGC" header, not computed: on Gengar the
# console sat at 0x7E000 with records of 0x12000 (each record's zone is its
# size plus ECC and header room), not where 0x100000 - 0xC0000 put it, and
# the reader printed nothing (2026-11-24).
zones = []
at = raw.find(b"DBGC")
while at >= 0:
    zones.append(at)
    at = raw.find(b"DBGC", at + 4)
for n, off in enumerate(zones):
    sig, start, used = struct.unpack_from("<4sII", raw, off)
    end = zones[n + 1] if n + 1 < len(zones) else len(raw)
    data = raw[off + 12:end]
    if not used or not data:
        continue
    cap = len(data)
    start, used = start % cap, min(used, cap)
    text = data[:used] if used < cap else data[start:] + data[:start]
    print(f"===== zone at 0x{off:X}: {used} bytes =====")
    print(text.decode("utf-8", "replace"))
