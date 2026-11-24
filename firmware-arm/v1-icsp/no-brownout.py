#!/usr/bin/env python3
"""A v1 dump with the brown-out reset off, nothing else changed.

    firmware-arm/v1-icsp/no-brownout.py <in.hex> <out.hex>

CONFIG2L (0x300002) bit 1 is BOREN on the PIC18F6525/6627 (DS39612). Lazare's
1.4b has it on (0A); Violet's 2.0b has it off (18). On Lazare one ear motor
starting, the right one alone, dips its 8 V / 1 A supply under the reset
level and the rabbit restarts (2026-11-24): this clears that one bit and
rewrites the record's checksum. Every other byte stays as read, which the
script checks before writing.
"""
import sys

CONFIG2L = 0x300002
BOREN = 0x02


def records(path):
    base = 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            yield line, None, None
            continue
        b = bytes.fromhex(line[1:])
        addr, kind = (b[1] << 8) | b[2], b[3]
        if kind == 4:
            base = ((b[4] << 8) | b[5]) << 16
        yield line, (base + addr if kind == 0 else None), b


def main(src, dst):
    out, hit = [], 0
    for line, at, b in records(src):
        if at is not None and at <= CONFIG2L < at + b[0]:
            data = bytearray(b)
            i = 4 + CONFIG2L - at
            old = data[i]
            data[i] = old & ~BOREN
            data[-1] = (-sum(data[:-1])) & 0xFF
            line = ":" + data.hex().upper()
            hit += 1
            print(f"CONFIG2L {old:02X} -> {data[i]:02X}")
        out.append(line)
    if hit != 1:
        sys.exit(f"CONFIG2L found {hit} times in {src}")
    a = [l for l in open(src).read().split()]
    changed = [x for x, y in zip(a, out) if x != y]
    if len(changed) != 1 or len(a) != len(out):
        sys.exit("more than one record would change")
    open(dst, "w").write("\n".join(out) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:3])
