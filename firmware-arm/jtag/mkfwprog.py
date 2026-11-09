#!/usr/bin/env python3
"""Turn a Nab.bin into the TCL that writes it over JTAG.

A word of flash costs about 19 ms over bit-banged JTAG, so the only
optimisation that matters is not writing the words that do not need writing:
an erased sector reads 0xFF everywhere, and the firmware's own writer skips
all-0xFF words for the same reason. On a 117 KB image that removes a few
thousand commands and a couple of minutes.

    ./mkfwprog.py ../bin/lean/Nab.bin -o /tmp/fwprog.tcl
    openocd -f nabaztag-pi.cfg -f oki.tcl -f /tmp/fwprog.tcl -c shutdown

The generated script erases sectors 0..30 and programs the image, then reads
it back and compares. The comparison is the point: this chip's writer has no
error checking at all -- `write_ok` is commented out in mem.c -- so a flash
that did nothing looks exactly like a flash that worked. Which is how a
rabbit ran a firmware nobody had installed for two weeks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

FLASH_BASE = 0x08000000
SECTOR_SIZE = 0x1000
CONF_SECTOR = 31          # Wi-Fi and server configuration; never erased here
# Only the configuration sector is spared. The June 2026 recovery spared 30
# as well, which capped an image at 122 880 bytes; the linker gives the
# firmware 124 KB (sys/ml67q4051.ld: "last sector is config data"), and
# Naboot 1.0.0.2 is 123 420 bytes -- written by OTA into sector 30 since.
SPARED = 1


def plan_resume(data: bytes, current: bytes):
    """The erases and the words still to write, given what the flash holds.

    Per sector, inside the image: nothing to do if every word matches; the
    missing words alone if each of them still reads 0xFFFFFFFF (programming
    only clears bits, so an erased word takes its value as it is); otherwise
    the sector is erased and all its words written. A word that holds a
    wrong value cannot be fixed without an erase, and an erase takes the
    whole sector with it."""
    if len(current) < len(data):
        sys.exit(f"the dump is {len(current)} bytes, shorter than the image "
                 f"({len(data)}): cannot tell what is left")
    erase, live = [], []
    for base in range(0, len(data), SECTOR_SIZE):
        want = [(i, data[i:i + 4]) for i in range(base, min(base + SECTOR_SIZE, len(data)), 4)]
        wrong = [(i, w) for i, w in want if current[i:i + 4] != w]
        if not wrong:
            continue
        if all(current[i:i + 4] == b"\xff\xff\xff\xff" for i, _ in wrong):
            live += [(i, w) for i, w in wrong if w != b"\xff\xff\xff\xff"]
        else:
            addr = FLASH_BASE + base
            erase += [f'echo "erasing sector {base // SECTOR_SIZE:2d} at 0x{addr:08x}"',
                      f"oki_erase_sector 0x{addr:08x}",
                      f"oki_verify_erased 0x{addr:08x}"]
            live += [(i, w) for i, w in want if w != b"\xff\xff\xff\xff"]
    live.sort()
    return erase, live


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path, help="a built Nab.bin")
    ap.add_argument("-o", "--output", type=Path, default=Path("fwprog.tcl"))
    ap.add_argument("--verify-only", action="store_true",
                    help="emit the readback and comparison, no erase or write")
    ap.add_argument("--no-erase", action="store_true",
                    help="program without erasing first — to resume a write "
                         "that was interrupted on sectors already erased. "
                         "Programming only clears bits, so rewriting a word "
                         "that already holds the right value is harmless.")
    ap.add_argument("--against", type=Path, metavar="CURRENT",
                    help="what the flash holds now (a dump of at least the "
                         "image's length): emit only the work left -- a "
                         "sector that already matches is skipped, one whose "
                         "missing words are still erased is completed without "
                         "an erase, any other is erased and written again. "
                         "flash.sh uses it to resume after an interrupted pass "
                         "instead of starting over from word 0.")
    args = ap.parse_args()

    data = args.image.read_bytes()
    limit = (CONF_SECTOR + 1 - SPARED) * SECTOR_SIZE
    if len(data) > limit:
        sys.exit(f"{args.image} is {len(data)} bytes; only {limit} are "
                 f"writable without touching the configuration sectors")

    # Pad to a word so the last command is a full four bytes.
    if len(data) % 4:
        data += b"\xff" * (4 - len(data) % 4)

    words = [(i, data[i:i + 4]) for i in range(0, len(data), 4)]
    live = [(i, w) for i, w in words if w != b"\xff\xff\xff\xff"]
    erase_cmds = [f"oki_erase_firmware {CONF_SECTOR - SPARED}"]

    if args.against is not None:
        erase_cmds, live = plan_resume(data, args.against.read_bytes())
        if not erase_cmds and not live:
            print(f"{args.output}: nothing left to write, the flash already holds the image")

    out = [
        "# Generated by mkfwprog.py -- do not edit.",
        f"#   image   {args.image.name}",
        f"#   bytes   {len(data)}",
        f"#   words   {len(live)} to write, {len(words) - len(live)} already 0xFF",
        f"#   est.    ~{len(live) * 19 // 60000} min at 19 ms per word",
        "",
        "halt",
        "oki_wd_off",
        # The core must be halted before anything is erased: taking sector 0
        # away from a running core mid-instruction is what wrecked the write
        # of 2026-11-07. The identifier is printed for the log and is NOT a
        # gate -- autoselect does not engage on this part, so it only ever
        # reports the array's own bytes (see oki.tcl).
        "oki_require_halted",
        "oki_reset_mode",
        "oki_id",
        "",
    ]
    if not args.verify_only:
        if not args.no_erase:
            out += erase_cmds + [""]
        # A line every 1000 words. Programming a whole image takes over an
        # hour on a bit-banged adapter, and a run with no output is
        # indistinguishable from a run that has wedged -- which matters when
        # the rabbit cannot boot until it finishes.
        for n, (off, w) in enumerate(live):
            addr = FLASH_BASE + off
            if n % 1000 == 0:
                out.append(f'echo "programmed {n} of {len(live)} words"')
            out.append("oki_program_word 0x%08x 0x%02x 0x%02x 0x%02x 0x%02x"
                       % (addr, w[0], w[1], w[2], w[3]))
        out.append(f'echo "programmed {len(live)} of {len(live)} words"')
        out += ["", "oki_reset_status", ""]

    out += [
        "# Read it back and say so out loud. A silent flash tells you nothing.",
        f'oki_dump /tmp/readback.bin 0x{FLASH_BASE:08x} 0x{len(data):x}',
        f'echo "now: sha256sum /tmp/readback.bin and compare with the image"',
    ]

    args.output.write_text("\n".join(out) + "\n")
    skipped = "already in place" if args.against is not None else "skipped as 0xFF"
    print(f"{args.output}: {len(live)} words to program "
          f"({len(words) - len(live)} {skipped}), "
          f"about {len(live) * 19 / 60000:.0f} minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
