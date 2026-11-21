#!/usr/bin/env python3
"""uncramfs.py <image> <dir> -- unpack a cramfs (Terrier OS's root) without
root: directories, files and symbolic links; device nodes are skipped (the
initramfs has devtmpfs). 7z dropped entries of it (2026-11-21), and
fsck.cramfs --extract stops at the first mknod a rootless build cannot do."""
import os
import struct
import sys
import zlib

MAGIC = 0x28CD3D45
PAGE = 4096


def inode(buf, off):
    mode, uid, size_gid, name_off = struct.unpack_from("<HHII", buf, off)
    size, namelen, offset = size_gid & 0xFFFFFF, (name_off & 0x3F) * 4, (name_off >> 6) * 4
    name = buf[off + 12:off + 12 + namelen].rstrip(b"\0").decode("utf-8", "replace")
    return mode, size, offset, name, off + 12 + namelen


def data(buf, offset, size):
    if size == 0:
        return b""
    nblocks = (size + PAGE - 1) // PAGE
    ends = struct.unpack_from(f"<{nblocks}I", buf, offset)
    start, out = offset + 4 * nblocks, []
    for end in ends:
        out.append(zlib.decompress(buf[start:end]) if end > start else b"\0" * PAGE)
        start = end
    return b"".join(out)[:size]


def walk(buf, offset, size, dest):
    end = offset + size
    while offset < end:
        mode, fsize, foff, name, offset = inode(buf, offset)
        path = os.path.join(dest, name)
        kind = mode & 0o170000
        if kind == 0o040000:
            os.makedirs(path, exist_ok=True)
            walk(buf, foff, fsize, path)
            os.chmod(path, mode & 0o7777 | 0o700)
        elif kind == 0o100000:
            with open(path, "wb") as f:
                f.write(data(buf, foff, fsize))
            os.chmod(path, mode & 0o7777)
        elif kind == 0o120000:
            os.symlink(data(buf, foff, fsize).decode(), path)


def main():
    buf = open(sys.argv[1], "rb").read()
    if struct.unpack_from("<I", buf, 0)[0] != MAGIC:
        sys.exit("not a cramfs image")
    mode, size, offset, _name, _ = inode(buf, 64)
    os.makedirs(sys.argv[2], exist_ok=True)
    walk(buf, offset, size, sys.argv[2])


if __name__ == "__main__":
    main()
