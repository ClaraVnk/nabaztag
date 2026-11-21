#!/usr/bin/env python3
"""cpio-list.py <dir> <dest prefix>... -- gen_init_cpio's list for each tree,
owned by root, plus /dev/console: built without root, so no mknod."""
import os
import stat
import sys

print("dir /dev 755 0 0\nnod /dev/console 600 0 0 c 5 1")
args = sys.argv[1:]
for src, dest in zip(args[::2], args[1::2]):
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        d = os.path.normpath(os.path.join(dest, rel))
        if d not in ("/", "/dev"):
            print(f"dir {d} 755 0 0")
        for n in sorted(dirs + files):
            p = os.path.join(root, n)
            t = os.path.join(d, n)
            st = os.lstat(p)
            mode = oct(stat.S_IMODE(st.st_mode))[2:]
            if stat.S_ISLNK(st.st_mode):
                print(f"slink {t} {os.readlink(p)} {mode} 0 0")
            elif stat.S_ISREG(st.st_mode):
                print(f"file {t} {p} {mode} 0 0")
