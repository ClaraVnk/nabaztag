#!/usr/bin/env python3
"""Rebuild the Karotz's /usr from a yaffs2 dump that lost its OOB.

A plain `nanddump` (no -o) keeps the 2048-byte pages and drops the spare
area, where yaffs2 keeps each chunk's object id, chunk number and sequence
number. What is left still holds every object header (type, parent id,
name, size, mode, symlink target) and every data page, so /usr comes back
by position:

- a file's data are the data pages written just before its last header
  (yaffs writes the data, then the header with the final size);
- a directory's own id is not in its header: it is the unknown parent id
  the headers right after it use most (cp and tar fill a directory as soon
  as they create it);
- the newest header of a (parent, name) wins (mtime, then position), unless
  its data do not hold together and an older copy's do;
  parents 3 and 4 (unlinked, deleted) drop it.

A rebuild, not a copy: report.txt lists what could not be placed. A tar of
/usr taken on the rabbit is exact; this is for dumps that already exist.

    usr-from-yaffs.py karotz-mtd6.bin <outdir>
"""
import os
import stat
import struct
import sys
from collections import Counter, defaultdict

PAGE = 2048
FILE, SYMLINK, DIR, HARDLINK, SPECIAL = 1, 2, 3, 4, 5
ROOT, LOSTFOUND, UNLINKED, DELETED = 1, 2, 3, 4


def header(page):
    t, parent = struct.unpack_from("<II", page)
    if t not in (FILE, SYMLINK, DIR, HARDLINK, SPECIAL) or page[8:10] != b"\xff\xff":
        return None
    name = page[10:266].split(b"\0")[0]
    if not name or not all(32 <= c < 127 for c in name) or b"/" in name:
        return None
    mode, uid, gid = struct.unpack_from("<III", page, 0x10C)
    mtime, = struct.unpack_from("<I", page, 0x11C)
    size, equiv = struct.unpack_from("<Ii", page, 0x124)
    alias = page[0x12C:0x12C + 160].split(b"\0")[0]
    return {"type": t, "parent": parent, "name": name.decode(), "mode": mode,
            "uid": uid, "gid": gid, "mtime": mtime, "size": size, "equiv": equiv,
            "alias": alias.decode("latin-1")}


def looks_whole(body):
    """An ELF whose section names are where its header says; a script
    with no NUL and no erased flash in it. Anything else is not checked."""
    if body[:4] == b"\x7fELF":
        if len(body) < 0x34:
            return False
        shoff, = struct.unpack_from("<I", body, 0x20)
        size, count, names = struct.unpack_from("<HHH", body, 0x2E)
        if not count:
            return True
        if shoff + count * size > len(body) or names >= count:
            return False
        off, length = struct.unpack_from("<II", body, shoff + names * size + 16)
        return b".text" in body[off:off + length]
    if body[:2] == b"#!":
        return b"\0" not in body and b"\xff" * 4 not in body
    return True


def main(src, out):
    raw = open(src, "rb").read()
    pages = [raw[i:i + PAGE] for i in range(0, len(raw), PAGE)]
    heads = []          # (page index, header)
    is_head = [False] * len(pages)
    for i, p in enumerate(pages):
        if p == b"\xff" * PAGE:
            continue
        h = header(p)
        if h:
            heads.append((i, h))
            is_head[i] = True

    # Directory ids. Children are written right after their directory, so
    # each directory scores the unknown parent ids of the headers that
    # follow its own (closer counts more); the best pairs win, one id per
    # directory, never an id that is already one of its ancestors.
    fixed = {0, ROOT, LOSTFOUND, UNLINKED, DELETED}
    score = defaultdict(Counter)
    for k, (i, h) in enumerate(heads):
        if h["type"] != DIR or h["parent"] in (0, UNLINKED, DELETED):
            continue
        for j, c in heads[k + 1:k + 60]:
            if c["parent"] not in fixed:
                score[(h["parent"], h["name"])][c["parent"]] += 1.0 / (1 + j - i)
    names = {}          # dir id -> (parent id, name)

    def ancestors(d):
        seen = set()
        while d in names and d not in seen:
            seen.add(d)
            d = names[d][0]
        return seen | {d}

    for _, key, x in sorted(((s, key, x) for key, c in score.items()
                             for x, s in c.items()), reverse=True):
        if key in names.values() or x in names or x in ancestors(key[0]):
            continue
        names[x] = key
    # A directory nothing points into is empty: give it an id of its own.
    spare = -1
    for key in score:
        if key not in names.values():
            names[spare] = key
            spare -= 1

    def path_of(d, depth=0):
        if d == ROOT:
            return ""
        if d not in names or depth > 32:
            return None
        p = path_of(names[d][0], depth + 1)
        return None if p is None else p + "/" + names[d][1]

    # The newest header of each (parent, name) wins: by mtime first, since
    # erase blocks are not in write order (an /etc/inittab two minutes
    # newer sat 42 000 pages earlier), then by position within a block.
    versions = defaultdict(list)    # newest first
    for i, h in sorted(heads, key=lambda ih: (ih[1]["mtime"], ih[0]), reverse=True):
        versions[(h["parent"], h["name"])].append((i, h))
    last = {key: v[0] for key, v in versions.items()}

    def body_at(i, h):
        """A file's data: the data pages written just before its header."""
        n = (h["size"] + PAGE - 1) // PAGE
        data, j = [], i - 1
        while len(data) < n and j >= 0:
            if not is_head[j] and pages[j] != b"\xff" * PAGE:
                data.append(pages[j])
            j -= 1
        return b"".join(reversed(data))[:h["size"]]

    lost = []
    os.makedirs(out, exist_ok=True)
    for (parent, name), (i, h) in sorted(last.items(), key=lambda kv: kv[1][0]):
        if parent in (UNLINKED, DELETED):
            continue
        base = path_of(parent)
        if base is None:
            lost.append(f"no dir {parent}: {name}")
            continue
        dst = out + base + "/" + name
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        perm = stat.S_IMODE(h["mode"])
        if h["type"] == DIR:
            os.makedirs(dst, exist_ok=True)
            os.chmod(dst, perm or 0o755)
        elif h["type"] == SYMLINK:
            if not os.path.lexists(dst):
                os.symlink(h["alias"], dst)
        elif h["type"] == FILE:
            # The newest version whose data hold together. A file installed
            # twice (FR's Karotz: FROS over itself) has an older copy written
            # in one run and a newer one scattered across erase blocks that
            # are not in write order without the OOB: libuClibc's newer copy
            # had lost its ELF header, its older one was whole (2026-11-17).
            # When any copy is an ELF, only an ELF counts.
            files = [(vi, vh) for vi, vh in versions[(parent, name)] if vh["type"] == FILE]
            bodies = [(vh, body_at(vi, vh)) for vi, vh in files]
            elf = any(b[:4] == b"\x7fELF" for _, b in bodies)
            whole = [(vh, b) for vh, b in bodies if len(b) == vh["size"] and looks_whole(b)
                     and (not elf or b[:4] == b"\x7fELF")]
            h, body = whole[0] if whole else bodies[0]
            perm = stat.S_IMODE(h["mode"])
            if not whole:
                # Erase blocks are not in write order without the OOB's
                # sequence numbers: a file across two may get another's data.
                lost.append(f"damaged: {base}/{name}")
            elif whole[0][0] is not bodies[0][0]:
                lost.append(f"older copy: {base}/{name}")
            with open(dst, "wb") as f:
                f.write(body)
            os.chmod(dst, perm or 0o644)
        else:
            lost.append(f"type {h['type']}: {base}/{name}")
    with open(os.path.join(out, "..", "report.txt"), "w") as f:
        f.write("\n".join(lost) + "\n")
    print(f"usr-from-yaffs: {len(heads)} headers, {len(names)} directories, "
          f"{len(lost)} not placed", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
