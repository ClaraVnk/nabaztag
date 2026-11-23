#!/usr/bin/env python3
"""An initramfs (newc cpio, gzip) from an unpacked Terrier OS ramdisk's /bin,
/lib and /karotz/bin (the nose's `led` tool), an /init, and extra files: kexec-proof.sh's and the measure key's.

    mkcpio.py <out.cpio.gz> <ramdisk tree> <init> [name=path ...]
"""
import gzip, os, stat, sys

out_path, tree, init = sys.argv[1:4]
out, ino = [], [1]


def ent(name, mode, data=b"", rdev=(0, 0)):
    ino[0] += 1
    nb = name.encode() + b"\0"
    h = "070701" + "".join("%08X" % v for v in (
        ino[0], mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(nb), 0))
    rec = h.encode() + nb
    rec += b"\0" * (-len(rec) % 4) + data + b"\0" * (-len(data) % 4)
    out.append(rec)


for d in ("bin", "lib", "karotz", "karotz/bin", "dev", "proc", "sys", "tmp", "usr", "key"):
    ent(d, stat.S_IFDIR | 0o755)
ent("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
for sub in ("bin", "lib", "karotz/bin"):
    for root, dirs, files in os.walk(os.path.join(tree, sub)):
        rel = os.path.relpath(root, tree)
        if rel != sub and rel != "karotz":
            ent(rel, stat.S_IFDIR | 0o755)
        for f in files:
            p = os.path.join(root, f)
            r = os.path.relpath(p, tree)
            if os.path.islink(p):
                ent(r, stat.S_IFLNK | 0o777, os.readlink(p).encode())
            else:
                ent(r, stat.S_IFREG | 0o755, open(p, "rb").read())
ent("init", stat.S_IFREG | 0o755, open(init, "rb").read())
for spec in sys.argv[4:]:
    name, path = spec.split("=", 1)
    ent(name, stat.S_IFREG | 0o755, open(path, "rb").read())
ent("TRAILER!!!", 0)
blob = gzip.compress(b"".join(out), 9)
open(out_path, "wb").write(blob)
print(out_path, len(blob), "bytes")
