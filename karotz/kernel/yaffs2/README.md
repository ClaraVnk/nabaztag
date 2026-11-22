# YAFFS for Linux 6.1 (vendored)

Aleph One's YAFFS (GPL v2), as the Yocto Project carries it in its 6.1
kernel: `fs/yaffs2` of `https://git.yoctoproject.org/linux-yocto`, branch
`v6.1/standard/base`, commit a3eebab9ba00cf38aab4f33ef14c28f76ab14134
(Linux 6.1.192, the version this directory's kernel builds). Copied unchanged.

Why this one (2026-11-22): the Karotz's `/usr` is YAFFS1. LapinLibre's
2.6.32 carries Aleph One's code of 2009 (3e5718ec), which no 6.1 builds;
Aleph One's own tree follows Linux only to 5.15 (`read_folio`,
`write_begin`, `bdevname` stop it on 6.1). Yocto ports it to each of its
kernels. The on-flash options are LapinLibre's (`karotz-6.1.config`).

One change, kept apart in `../yaffs1-small-page.patch` and applied by the
Containerfile: YAFFS1 on 512-byte pages was refused (in-band tags chosen
for it too). Aleph One's tree has the same test.

And one addition, `../yaffs1-mtd1/` hooked in by `../yaffs1-mtd1.patch`:
today's YAFFS writes YAFFS1 tags as a 16-byte spare (the pre-2.6.17 way);
the Karotz's 2.6.32 wrote 8-byte packed tags through MTD's free OOB bytes
(Aleph One's `yaffs_mtdif1.c` of 2009), which is what an existing `/usr`
holds. `yaffs_tags_mtd1.c` is that file ported to today's tags handler.
