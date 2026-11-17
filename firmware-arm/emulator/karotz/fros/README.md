# A Karotz's own system, from its dump

The rest of `karotz/` runs OpenKarotz's CGI and our client under `qemu-arm`.
This boots the rabbit's whole userland -- its `linuxrc`, busybox init,
`karotz_init.sh`, `startup.sh`, its daemons and libraries -- from a flash dump,
as a first step towards a modern FROS.

QEMU has no S3C2440, so the Karotz's own kernel (machine 1912, "RABBITV3 BY
PRAGMATEC", Linux 2.6.28) does not boot. A mainline kernel for ARM Versatile
(ARM926, ARMv5) runs the Karotz's ARMv4T EABI binaries as they are.

## The dump

`karotz-mtdN.bin` as `measure.sh` or `nanddump` wrote them, in one directory:

| | |
|---|---|
| mtd0 | the bootloader, Pragmatec "Rabbit V3 BIOS v1.16" (not used here) |
| mtd1, mtd3 | kernel zImages (normal, rescue) |
| mtd2 | the root: a gzip'd cramfs (`rootfs.fros001.img` on FROS) |
| mtd4 | the rescue ramdisk |
| mtd6 | /usr, yaffs2 |

**It never goes into this repository**: /usr holds the owner's Wi-Fi network
and password.

## What boot.sh does

1. mtd2 → the cramfs root, unpacked by `root-for-qemu.py`, with one line of
   `linuxrc` changed: /usr comes from the emulated disk, not
   `mount -t yaffs /dev/mtdblock6`.
2. mtd6 → /usr, rebuilt by `usr-from-yaffs.py`. A plain `nanddump` drops the
   spare area where yaffs2 keeps object ids and sequence numbers, so /usr is
   rebuilt by position: directory ids from the headers that follow them, a
   file's data from the pages before its header, the newest copy whose data
   hold together (ELF headers, scripts without erased flash). `report.txt`
   lists what it could not place. A tar of /usr taken on the rabbit is exact;
   prefer one when the rabbit is at hand.
3. QEMU `versatilepb`, the root as an initrd, /usr on a SCSI disk, the
   bootloader's own command line with the console on `ttyAMA0`.

FR's dump (2026-11-17) carried two `/etc/inittab`, one of them only
`::once:/sbin/reboot`; without the OOB nothing says which came last, so
`boot.sh` drops that one and `linuxrc` restores the factory file.

## Stand-ins for the rabbit

- `karotz_stubs.c`, built into the kernel: `/dev/rabbit_button` (c 52),
  `rabbit_power` (c 53), `rabbit_ears` (c 60) and the LED's
  `/dev/spidev0.1`. Each opens, a read waits, and every write, ioctl and SPI
  transfer is printed. Measured that way, the LED daemon writes (register,
  value) pairs, registers 0-8, inverted (`ff` off): cyan pulsing at boot,
  red when the network is missing.
- `waitfornetwork.sh` replaces FROS's Wi-Fi script (which holds the owner's
  network and password): QEMU's Ethernet becomes `wlan0` with user-mode
  networking's address.

With them startup.sh runs to the end: network, D-Bus, cron, the daemons
under immortaldog, the time from pool.ntp.org, `ready.mp3` (no sound card).

## Mindscape's platform protocol (`PLATFORM=1`)

The controller dials `ip_server:port_server` from karotz.conf
(`platform.karotz.com:9123`). `PLATFORM=1` points it at `fake_platform.py`
inside the container, with test credentials in voos.conf, and starts it:

```sh
SECONDS_UP=240 podman run --rm -e SECONDS_UP -e PLATFORM=1 -v <dump dir>:/dump:ro,Z fros
```

[LapinLibre](https://github.com/sweetlilmre/LapinLibre) (MIT, GPL-2.0)
already documents this protocol and serves it (`tools/app-server/`,
`docs/system.md`), on a free firmware for the Karotz built from source: read
it first. What follows was measured independently on Mindscape's own
binary, and agrees with it.

Measured 2026-11-17 on FR's controller:

- framing: a 4-byte big-endian length, then a `VoosMsg` protobuf
  (`Voos-message.proto`, github.com/dirk-thomas/karotz); plain TCP, no TLS;
- the platform speaks first: `authenticationToken{token}`; the rabbit
  answers `authenticationResponse{login = voos.conf id, authentication =
  hex(md5(pwd + token))}` with `correlationId` = the token message's id;
- then the rabbit pings (`ping`, field 1000) and drops the platform after
  two unanswered ones; a ping back with its `correlationId` keeps it;
- `config` (field 600) lists the apps: one app, `deployment: "external"`
  (it runs on a server), with one `configInstance{name, uuid}`. The
  controller answers with `karotzinfo_response` (MAC, IP, rootfs and system
  versions, language) and becomes AVAILABLE. It still fetches the app's zip
  from `url_app` (`/file/<apiKey>/<version>/get`, a `descriptor.xml` inside)
  and drops the app when there is none -- next to serve;
- `interactiveMode START` with `configId` = that instance's uuid: the rabbit
  becomes INTERACTIVE (ttl 900 s) and answers `response{code: OK}`;
  without a `configId` it is refused (`voosMsgid != currentId`);
- then, with the interactive id: `led.light{color: "00FF00"}` → OK, and the
  LED goes green on the SPI bus (registers 6-8 on, 0-5 off; red was 0-2);
  `tts{START, text, lang}` → OK, the voice daemon fetches the speech from
  the TTS server its configuration names and hands it to madplay (no sound
  card here); `ears{left, right}` → ERROR: the ears daemon opens
  `/dev/rabbit_ears` and never writes to it, waiting, it seems, on an answer
  from the real driver -- to measure on a real Karotz.

`PLATFORM=1` also starts the ears, voice and multimedia daemons, which the
controller drives over D-Bus and FROS does not start.
