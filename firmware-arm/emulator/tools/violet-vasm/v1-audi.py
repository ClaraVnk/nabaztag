#!/usr/bin/env python3
"""An "Audi" sound for a Nabaztag v1: a tone in OKI 4-bit ADPCM, 16-byte header.

    v1-audi.py RATE SECONDS out.audi [--low-first]

The container Violet's firmware plays (what its code at 0x2890 in 2.0b,
0x217E in 1.4b reads): "Audi", 80 01 01 04, the sample rate on two bytes
big-endian, three zeros, the data length on three bytes, then the ADPCM
nibbles. The firmware looks at one header byte only, the high byte of the
rate (offset 8): 1F -> register 35 = 3 (8 000 Hz), 7D -> 7 (32 000 Hz),
anything else -> 6 (16 000 Hz, what Violet's server sent). The rest of the
header is skipped (data starts at +16).

The ADPCM is the Dialogic/OKI algorithm (MSM6258 family: 12-bit samples,
49 steps), first sample in the high nibble unless --low-first. The emulator
cannot say which nibble order the ML2870A wants (its OKI model is a register
log); the real rabbit can, with this file at both orders (2026-11-24).
"""
import math
import sys

STEPS = (16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
         107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
         494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552)
ADJ = (-1, -1, -1, -1, 2, 4, 6, 8)


def encode(pcm12, low_first=False):
    pred, idx, out, pend = 0, 0, bytearray(), None
    for s in pcm12:
        step = STEPS[idx]
        diff = s - pred
        code = 8 if diff < 0 else 0
        diff = abs(diff)
        if diff >= step: code |= 4; diff -= step
        if diff >= step >> 1: code |= 2; diff -= step >> 1
        if diff >= step >> 2: code |= 1
        delta = (step >> 3) + (step if code & 4 else 0) + (step >> 1 if code & 2 else 0) + (step >> 2 if code & 1 else 0)
        pred = max(-2048, min(2047, pred - delta if code & 8 else pred + delta))
        idx = max(0, min(48, idx + ADJ[code & 7]))
        if pend is None:
            pend = code
        else:
            out.append((code << 4) | pend if low_first else (pend << 4) | code)
            pend = None
    if pend is not None:
        out.append(pend if low_first else pend << 4)
    return bytes(out)


def audi(rate, adpcm):
    return (b"Audi" + bytes.fromhex("80010104") + rate.to_bytes(2, "big") + b"\0\0\0"
            + len(adpcm).to_bytes(3, "big") + adpcm)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rate, seconds, out = int(args[0]), float(args[1]), args[2]
    n = int(rate * seconds)
    # 440 Hz, then 880 Hz: a pitch that doubles is heard at any playback rate.
    pcm = [int(1500 * math.sin(2 * math.pi * (440 if i < n // 2 else 880) * i / rate)) for i in range(n)]
    data = encode(pcm, "--low-first" in sys.argv)
    open(out, "wb").write(audi(rate, data))
    print("%s: %d Hz, %d samples, %d ADPCM bytes, header byte 8 = %02X" % (out, rate, n, len(data), rate >> 8))


if __name__ == "__main__":
    main()
