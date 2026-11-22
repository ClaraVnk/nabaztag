#!/usr/bin/env python3
"""Put a Karotz's whole NAND back together from the measure key's dumps, as
QEMU's mini2440 NAND reads a -mtdblock file: every 512-byte page followed by
its 16 bytes of OOB, 524288 pages for the 256 MiB chip (id 71).

    karotz-flash.py <measure dir> <out>

mtd5 and mtd6 (YAFFS) come with their OOB (mtdN-raw.bin from oobdump, or
mtdN-oob.bin from busybox nanddump -o, whose spare was not real); the
other partitions hold no YAFFS tags and come as data only, their OOB filled
with FF, an erased spare. Sizes are checked against log.txt's /proc/mtd.
"""
import re
import sys
from pathlib import Path

PAGE, OOB, PAGES = 512, 16, 524288

d, out = Path(sys.argv[1]), Path(sys.argv[2])
parts = re.findall(r"^mtd(\d+): ([0-9a-f]{8}) [0-9a-f]{8} ", (d / "log.txt").read_text(), re.M)
assert len(parts) == 8, f"/proc/mtd in log.txt: {len(parts)} partitions"
offsets = [int(a, 16) for a in re.findall(r"^0x([0-9a-f]{12})-0x[0-9a-f]{12} : ", (d / "dmesg.txt").read_text(errors="replace"), re.M)]
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
            data = (d / f"mtd{n}.bin").read_bytes()[:size]
            assert len(data) == size, f"mtd{n}.bin: {len(data)} bytes, want {size}"
            for i in range(pages):
                f.write(data[i * PAGE:(i + 1) * PAGE] + b"\xff" * OOB)
        total += pages
        print(f"mtd{n}: {pages} pages at {at:#x}{' with OOB' if oob.exists() else ''}")
print(f"{out}: {PAGES} pages ({total} from the dumps)")
