#!/usr/bin/env python3
"""Lift the bootloader bytecode out of a built firmware image.

The emulator's VM target links one bytecode under the symbol `dumpbc`, and
until 2026-11-07 it was always pointed at a .hybrid — the bytecode a warren
serves. That is the *second* bytecode a rabbit runs. The first one is
`boot.0.0.0.13.mtl`, compiled into the firmware itself, and it is where
wifiAuth, the netSaePw call that hands SAE its passphrase, the httpflash
signature gate and the boot URL all live. Nothing outside a real rabbit had
ever executed a line of it, which is how two WPA3 bugs in two days both
landed in that one file.

So: give this a Nab.elf (or a Nab.bin plus its Nab.elf for the symbol), get
the blob, and build the VM against it:

    tools/bootbc.py ../bin/lean-wpa3/Nab.elf -o obj/boot.bc
    make vm NABGCC=<tree> BYTECODE=obj/boot.bc
    ./rabbit-vm scenarios/vm/wpa3-sae.txt --eeprom r.eeprom --warren localhost

The `size` nm reports for dumpbc is the symbol's, which the linker rounds
down relative to what loaderSizeBC wants to read; the three section lengths
in the blob's own header are authoritative, so the slice is grown until they
add up. mkbc.py re-does that parse and refuses a blob that does not.
"""

from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

NM_CANDIDATES = ("arm-none-eabi-nm", "nm", "llvm-nm")


def dumpbc_symbol(elf: Path) -> tuple[int, int]:
    """(address, size) of dumpbc, from whichever nm is on this machine."""
    last = ""
    for nm in NM_CANDIDATES:
        try:
            out = subprocess.run([nm, "-S", str(elf)], capture_output=True,
                                 text=True, check=True).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            last = f"{nm}: {exc}"
            continue
        for line in out.splitlines():
            m = re.match(r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\S+\s+dumpbc$",
                         line.strip())
            if m:
                return int(m.group(1), 16), int(m.group(2), 16)
        last = f"{nm}: no dumpbc symbol in {elf}"
    sys.exit(f"could not locate dumpbc ({last})")


def parsed_length(blob: bytes) -> int:
    """What loaderSizeBC would walk: globals + code + the function table."""
    get_int = lambda o: struct.unpack_from("<i", blob, o)[0]
    get_short = lambda o: struct.unpack_from("<h", blob, o)[0]
    globals_length = get_int(0)
    code_length = get_int(globals_length)
    table = globals_length + 4 + code_length
    functions = get_short(table)
    return table + 2 + (functions << 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("elf", type=Path, help="a built Nab.elf")
    ap.add_argument("--bin", type=Path,
                    help="the matching Nab.bin (default: Nab.bin beside the elf)")
    ap.add_argument("-o", "--output", type=Path, default=Path("boot.bc"))
    args = ap.parse_args()

    image = args.bin or args.elf.with_name("Nab.bin")
    if not image.exists():
        sys.exit(f"{image} is missing — pass --bin")

    addr, size = dumpbc_symbol(args.elf)
    # .text starts at the reset address, so the file offset is the distance
    # from it. Any firmware this project builds is linked that way.
    base = addr & 0xFF000000
    offset = addr - base
    blob = image.read_bytes()
    if offset + size > len(blob):
        sys.exit(f"dumpbc at 0x{addr:08x} is outside a {len(blob)}-byte image")

    want = parsed_length(blob[offset:offset + size + 64])
    out = blob[offset:offset + want]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out)
    print(f"{args.output}: {len(out)} bytes of bootloader bytecode "
          f"from {image.name} at 0x{offset:x} (symbol said {size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
