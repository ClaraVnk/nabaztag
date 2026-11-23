#!/usr/bin/env python3
"""Put a Karotz's whole NAND back together from the measure key's dumps, as
QEMU's mini2440 NAND reads a -mtdblock file: every page followed by its OOB.

    karotz-flash.py <measure dir> <out>      (prints the chip id last)

The geometry is the chip the factory kernel names in dmesg.txt. Gengar's is
a Hynix 0xDA, 2048-byte pages and 64 bytes of OOB (measure of 2026-11-23,
oobdump: 119808 pages of 2112 bytes in mtd6); the 512-byte id 71 assumed
before it held the bytes in the wrong pages, and /usr mounted empty.

mtd5 and mtd6 (YAFFS) come with their OOB (mtdN-raw.bin from oobdump, or
mtdN-oob.bin from busybox nanddump -o, whose spare was not real); the
other partitions hold no YAFFS tags and come as data only, their OOB filled
with FF, an erased spare. Sizes are checked against log.txt's /proc/mtd.
"""
import re
import sys
from pathlib import Path

# Chip id -> page, OOB, pages (256 MiB both): what QEMU's nand.c builds.
GEOMETRY = {0x71: (512, 16, 524288), 0xDA: (2048, 64, 131072)}

d, out = Path(sys.argv[1]), Path(sys.argv[2])
dmesg = (d / "dmesg.txt").read_text(errors="replace")
cid = int(re.search(r"NAND device: Manufacturer ID: 0x[0-9a-f]+, Chip ID: 0x([0-9a-f]+)", dmesg).group(1), 16)
assert cid in GEOMETRY, f"chip id {cid:#x}: no geometry for it"
PAGE, OOB, PAGES = GEOMETRY[cid]
parts = re.findall(r"^mtd(\d+): ([0-9a-f]{8}) [0-9a-f]{8} ", (d / "log.txt").read_text(), re.M)
assert len(parts) == 8, f"/proc/mtd in log.txt: {len(parts)} partitions"
offsets = [int(a, 16) for a in re.findall(r"^0x([0-9a-f]{12})-0x[0-9a-f]{12} : ", dmesg, re.M)]
assert len(offsets) == len(parts), f"dmesg.txt: {len(offsets)} partition lines"
total = 0
with out.open("wb") as f:
    f.write(b"\xff" * (PAGE + OOB) * PAGES)
    for (n, size), at in zip(parts, offsets):
        size = int(size, 16)
        pages = size // PAGE
        f.seek(at // PAGE * (PAGE + OOB))
        # oobdump's copy first (the real spare), else busybox nanddump's
        oob = d / f"mtd{n}-raw.bin"
        if not oob.exists():
            oob = d / f"mtd{n}-oob.bin"
        if oob.exists():
            data = oob.read_bytes()
            assert len(data) == pages * (PAGE + OOB), f"{oob.name}: {len(data)} bytes, want {pages * (PAGE + OOB)}"
            f.write(data)
        else:
            raw = (d / f"mtd{n}.bin").read_bytes()
            # The key's `nanddump -o` keeps each page's spare after it: a
            # 2 KiB page and 64 bytes, not the data alone. Read as data, it
            # put Gengar's kernel and ramdisk 64 bytes further at every page,
            # and its ramdisk would not inflate (2026-11-23). The spare it
            # wrote is not the flash's (see oobdump), so an erased one here.
            if len(raw) == pages * (PAGE + OOB):
                data = b"".join(raw[i * (PAGE + OOB):i * (PAGE + OOB) + PAGE] for i in range(pages))
            else:
                data = raw[:size]
            assert len(data) == size, f"mtd{n}.bin: {len(raw)} bytes, want {size} or {pages * (PAGE + OOB)}"
            for i in range(pages):
                f.write(data[i * PAGE:(i + 1) * PAGE] + b"\xff" * OOB)
        total += pages
        print(f"mtd{n}: {pages} pages at {at:#x}{' with OOB' if oob.exists() else ''}")
print(f"{out}: {PAGES} pages of {PAGE}+{OOB} ({total} from the dumps)")
print(f"{cid:#x}")
