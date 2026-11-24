#!/usr/bin/env python3
"""An initramfs (newc cpio, gzip) from an unpacked Terrier OS ramdisk's /bin,
/lib and /karotz/bin (the nose's `led` tool), an /init, and extra files: kexec-proof.sh's and the measure key's.

    mkcpio.py <out.cpio.gz> <ramdisk tree> <init> [name=path ...]
    mkcpio.py --all <out.cpio.gz> <ramdisk tree> <init>

--all: the whole tree, the rabbit's own system, with <init> as /init
(karotz/kexec make-key.sh: Terrier OS on 6.1, its /usr read-only).
"""
import gzip, os, stat, sys

whole = sys.argv[1] == "--all"
if whole:
    del sys.argv[1]
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


subs = sorted(os.listdir(tree)) if whole else ("bin", "lib", "karotz/bin")
dirs = ("dev", "proc", "sys", "tmp", "usr", "key") if whole else \
    ("bin", "lib", "karotz", "karotz/bin", "dev", "proc", "sys", "tmp", "usr", "key")
for d in dirs:
    if not (whole and os.path.isdir(os.path.join(tree, d))):
        ent(d, stat.S_IFDIR | 0o755)
ent("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
for sub in subs:
    if whole and not os.path.isdir(os.path.join(tree, sub)) and not os.path.islink(os.path.join(tree, sub)):
        p = os.path.join(tree, sub)
        ent(sub, stat.S_IFREG | (os.stat(p).st_mode & 0o777), open(p, "rb").read())
        continue
    if whole and os.path.islink(os.path.join(tree, sub)):
        ent(sub, stat.S_IFLNK | 0o777, os.readlink(os.path.join(tree, sub)).encode())
        continue
    if whole:
        ent(sub, stat.S_IFDIR | 0o755)
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
                mode = (os.stat(p).st_mode & 0o777) if whole else 0o755
                ent(r, stat.S_IFREG | mode, open(p, "rb").read())
ent("init", stat.S_IFREG | 0o755, open(init, "rb").read())
for spec in sys.argv[4:]:
    name, path = spec.split("=", 1)
    ent(name, stat.S_IFREG | 0o755, open(path, "rb").read())
ent("TRAILER!!!", 0)
blob = gzip.compress(b"".join(out), 9)
open(out_path, "wb").write(blob)
print(out_path, len(blob), "bytes")
