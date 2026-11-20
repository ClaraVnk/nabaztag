#!/usr/bin/env python3
"""Unpack the Karotz's cramfs root and point its /usr at the emulated disk.

linuxrc mounts /usr with `mount -t yaffs /dev/mtdblock6 /usr`; there is no
NAND here, so that one line becomes the ext2 disk boot.sh attaches
(/dev/sda). Nothing else in the rabbit's root changes. Device nodes are
left out: linuxrc makes its own on a tmpfs /dev.

fsck.cramfs --extract stops at the first device node it may not create
(rootless podman), hence this reader.

    root-for-qemu.py root.img <outdir>
"""
import os
import stat
import struct
import sys
import zlib

YAFFS_USR = b"/bin/mount -t yaffs /dev/mtdblock6 /usr"
# The disk may show up after init starts, and linuxrc's own tmpfs /dev
# hides devtmpfs: wait for it, then make its node like linuxrc makes others.
QEMU_USR = (b"for i in 1 2 3 4 5 6 7 8 9 10; do "
            b"[ -e /sys/block/sda/dev ] && break; sleep 1; done; "
            b"/bin/mknod /dev/sda b $(sed 's/:/ /' /sys/block/sda/dev); "
            b"/bin/mount -t ext2 /dev/sda /usr")


def main(src, out):
    d = open(src, "rb").read()

    def inode(o):
        a, b, c = struct.unpack_from("<III", d, o)
        n = (c & 0x3F) * 4
        return (a & 0xFFFF, a >> 16, b & 0xFFFFFF, b >> 24, (c >> 6) * 4,
                d[o + 12:o + 12 + n].split(b"\0")[0].decode(), o + 12 + n)

    def content(size, off):
        blocks = (size + 4095) // 4096
        ends = struct.unpack_from(f"<{blocks}I", d, off)
        start, body = off + 4 * blocks, b""
        for end in ends:
            body += zlib.decompress(d[start:end]) if end > start else b"\0" * 4096
            start = end
        return body[:size]

    def walk(o, path):
        mode, uid, size, gid, off, _, _ = inode(o)
        dst = out + path
        if stat.S_ISDIR(mode):
            os.makedirs(dst, exist_ok=True)
            p = off
            while p < off + size:
                name, nxt = inode(p)[5], inode(p)[6]
                walk(p, path + "/" + name)
                p = nxt
        elif stat.S_ISLNK(mode):
            os.symlink(content(size, off).decode(), dst)
        elif stat.S_ISREG(mode):
            body = content(size, off)
            if path == "/linuxrc":
                if YAFFS_USR not in body:
                    sys.exit("linuxrc: no yaffs mount of /usr to replace")
                body = body.replace(YAFFS_USR, QEMU_USR)
            with open(dst, "wb") as f:
                f.write(body)
        else:
            return
        os.lchown(dst, uid, gid)
        if not stat.S_ISLNK(mode):
            os.chmod(dst, stat.S_IMODE(mode))

    if struct.unpack_from("<I", d)[0] != 0x28CD3D45:
        sys.exit(f"{src}: not a cramfs")
    walk(0x40, "")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
