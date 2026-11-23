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

MEM, CONSOLE, RECORD = 0x100000, 0xC0000, 0x10000
raw = open(sys.argv[1], "rb").read()
zones = [(f"dump {i}", i * RECORD, RECORD) for i in range((MEM - CONSOLE) // RECORD)]
zones.append(("console", MEM - CONSOLE, CONSOLE))
for name, off, size in zones:
    sig, start, used = struct.unpack_from("<4sII", raw, off)
    if sig != b"DBGC" or not used:
        continue
    data = raw[off + 12:off + size]
    cap = len(data)
    start, used = start % cap, min(used, cap)
    text = data[:used] if used < cap else data[start:] + data[:start]
    print(f"===== {name}: {used} bytes =====")
    print(text.decode("utf-8", "replace"))
