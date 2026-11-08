#!/usr/bin/env python3
"""Turn a `Nab.bin` into the `.sim` a rabbit accepts — without PHP.

nabgcc ships `utils/mkfirmware.php`, and the container build calls it. That
made PHP a hard dependency of every firmware build, which on 2026-11-08 meant
no image could be built at all while the dev VM was unreachable and the only
other machine to hand had no PHP. This is a literal port of that script,
checked against its output rather than against a reading of it:
`--check <nab.bin> <firmware.sim>` reproduces a .sim the PHP produced and
compares byte for byte.

The format, from mkfirmware.php:

    "-violet-" + itoh8(2 * len) + hex(obfuscate(image)) + "-violet-"

`signing/simpayload.py` is the other direction (what the rabbit's `uncrypt`
does before it verifies a signature), and the two are inverses — which
`--check` also exercises, since a mismatch either way fails it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# mkfirmware.php's `$inv8`: the multiplicative inverses mod 256 of the odd
# numbers, indexed by key>>1. Kept as written there rather than computed, so a
# transcription error shows up as a diff against the PHP's own output.
INV8 = (
      1, 171, 205, 183,  57, 163, 197, 239, 241,  27,  61, 167,  41,  19,  53, 223,
    225, 139, 173, 151,  25, 131, 165, 207, 209, 251,  29, 135,   9, 243,  21, 191,
    193, 107, 141, 119, 249,  99, 133, 175, 177, 219, 253, 103, 233, 211, 245, 159,
    161,  75, 109,  87, 217,  67, 101, 143, 145, 187, 221,  71, 201, 179, 213, 127,
    129,  43,  77,  55, 185,  35,  69, 111, 113, 155, 189,  39, 169, 147, 181,  95,
     97,  11,  45,  23, 153,   3,  37,  79,  81, 123, 157,   7, 137, 115, 149,  63,
     65, 235,  13, 247, 121, 227,   5,  47,  49,  91, 125, 231, 105,  83, 117,  31,
     33, 203, 237, 215,  89, 195, 229,  15,  17,  59,  93, 199,  73,  51,  85, 255,
)

LIMIT = b"-violet-"
CRYPT_KEY = 0x47
CRYPT_ALPHA = 47


def obfuscate(buf: bytes, key: int = CRYPT_KEY, alpha: int = CRYPT_ALPHA) -> bytes:
    """mkfirmware.php's `strcrypt8`. The inverse of simpayload.deobfuscate."""
    out = bytearray(len(buf))
    for i, v in enumerate(buf):
        out[i] = (alpha + (v * INV8[key >> 1]) % 256) & 0xFF
        key = (1 + 2 * v) % 256
    return bytes(out)


def mksim(image: bytes) -> bytes:
    """The .sim for this image, byte for byte as mkfirmware.php writes it."""
    return (LIMIT
            + b"%08x" % (2 * len(image))
            + obfuscate(image).hex().encode("ascii")
            + LIMIT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="the Nab.bin to wrap")
    ap.add_argument("-o", "--output", help="where to write the .sim")
    ap.add_argument("--check", metavar="SIM",
                    help="a .sim produced by mkfirmware.php for this exact "
                         "image; compare against it instead of writing")
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    sim = mksim(image)

    if args.check:
        want = Path(args.check).read_bytes()
        if sim == want:
            print(f"byte-identical to {args.check} ({len(sim)} bytes)")
            return 0
        print(f"DIFFERS from {args.check}: {len(sim)} bytes vs {len(want)}",
              file=sys.stderr)
        for i, (a, b) in enumerate(zip(sim, want)):
            if a != b:
                print(f"  first difference at offset {i}: "
                      f"0x{a:02x} vs 0x{b:02x}", file=sys.stderr)
                break
        return 1

    if not args.output:
        print("give -o, or --check to compare", file=sys.stderr)
        return 2
    Path(args.output).write_bytes(sim)
    print(f"wrote {len(sim)} bytes to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
