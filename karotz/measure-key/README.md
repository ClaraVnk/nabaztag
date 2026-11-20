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
