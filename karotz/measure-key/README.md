# Measure a factory Karotz with a USB key

Before installing anything (FROS, OpenKarotz, LapinLibre) on a Karotz that
has no network and no SSH, read it first: its processor, its flash layout,
its kernel log, and a full copy of its flash, so the rabbit can be put back
exactly as it was.

The factory boot (`linuxrc`) runs a key's `autorun` as root when
`gpg --verify autorun.sig` passes. `autorun.sig` is Violet's clearsigned
rootfs notice and covers nothing of the script (read in
`rofra/karotz-openkarotz-firmware` `firmware/linuxrc` and in
`sweetlilmre/LapinLibre`), so any script beside it runs. Not yet tried on a
factory system by LapinLibre either: if nothing appears on the key, the boot
did not run it, and nothing on the rabbit changed.

`autorun` only reads. Everything goes to the key under `measure/`:
`log.txt` (cpuinfo, `/proc/mtd`, versions, USB devices), `dmesg.txt`,
`mount.txt`, `lsmod.txt`, `bins.txt`, `usr-tree.txt`, `etc-tree.txt`, one
`mtdN.bin` per flash partition (`nanddump -o`, data without OOB), then
`md5.txt` and `DONE` last.

On Terrier OS (its `linuxrc` runs a key's `autorun` too, without the
signature), the two YAFFS partitions come as `mtd5-oob.bin` and
`mtd6-oob.bin`, **with** their OOB (busybox `nanddump -o`, 528 bytes a
page): YAFFS1 keeps in the OOB which file each page belongs to, and the
data-only dump of 2026-11-17 could not be mounted. That is what step 3 of
`docs/TERRIER-OS-KERNEL.md` mounts with the 6.1 kernel. `mtd6-oob.bin` is
about 242 MB: a key of 1 GB.

Since 2026-11-22 the key also carries `oobdump` (built by `Containerfile`
here, put on the key by make-key.sh): the busybox copy's spare turned out
to be file text, not YAFFS tags. With it the YAFFS partitions come as
`mtd5-raw.bin` and `mtd6-raw.bin` (512 + 16 bytes a page, the spare read
with MEMREADOOB), and `oobdump-mtdN.txt` says how many spares were not
blank: none at all on a /usr full of files means the read failed again.

## Steps

1. Format a key of at least 512 MB as FAT32 (Mac: "MS-DOS (FAT)", Master
   Boot Record), and name it `KAROTZ`.
2. From this repository: `sh karotz/measure-key/make-key.sh /Volumes/KAROTZ`.
   It copies `autorun` (LF line ends) and fetches `autorun.sig` from
   LapinLibre at a pinned commit, checked by MD5; the signature is not kept
   here.
3. Rabbit off (wheel at the back), key in, rabbit on **without touching the
   head** (a held head at power-on is the factory reset). The rabbit goes on
   booting only when the script returns: its usual light back (solid cyan on
   a factory rabbit with no network) means the measure is done.
4. Power off, read `measure/` on the computer. Check each `mtdN.bin` against
   the sizes in `/proc/mtd` (in `log.txt`); `mtd6` is about 234 MiB.
