# A Karotz without the Karotz

The Karotz (2011, the Nabaztag v3) runs Linux 2.6.28 on an ARM920T
(`/proc/cpuinfo`: `Hardware: RABBITV3 BY PRAGMATEC`; the console is
`ttySAC0`, so an S3C24xx), with OpenKarotz or FreeRabbits OS on top. The
Terrier's client, `terrier-karotz`, runs on it next to OpenKarotz.

## What runs today: the rabbit's userland

`Containerfile` builds a Karotz from the outside in:

- OpenKarotz's real web sources (pinned commit and checksum) under busybox
  `httpd`: the CGI scripts the client calls, with their own parsing, their
  own sleep state and their own refusals;
- the hardware those scripts drive -- `/karotz/bin/ears`, the LED and ears
  daemons behind `dbus-send`, `mplayer`, `madplay` -- replaced by recorders
  (`stubs/`) that write one line per request to `/proof/hw.log`; the
  `mplayer` recorder fetches and decodes what it is given (mpg123), so a
  sound that would not play fails;
- `dbus-monitor` fed through a FIFO (`/proof/dbus.fifo`), in its own output
  format, for the head button and RFID tags;
- the client, built for the rabbit, under `qemu-arm`, started by the very
  `run.sh` a Karotz uses.

```sh
tools/karotz-proof.sh                         # the released client starts and asks for its key
tools/karotz-proof.sh --terrier <checkout>    # the Terrier's scenario: adoption, every verb,
                                              # the button, a tag, a signed update
```

(from `firmware-arm/emulator`, on a machine with podman). `RELEASE`
overrides the client release the first form downloads.

What this does not show: the Karotz's own kernel, `/dev/random` that never
fills (0.1.3 stalled on it; qemu-user takes the host's entropy), and the
ARMv4T instruction set (`qemu-arm -cpu arm926`: qemu's own vdso faults on
`ti925t`). The client's build checks those two by other means
(`llvm-readelf -A`, `strings`).

## Next: the whole Karotz

The rabbit's own kernel and root filesystem on an emulated S3C24xx board.
No kernel source or image for "RABBITV3" is public, so it starts with a
real Karotz: `measure.sh` collects, read-only, onto a USB key, what the
board model needs -- cpuinfo, dmesg, iomem, `/proc/mtd` and a dump of every
MTD partition (the kernel is in one), the device nodes, and ten seconds of
D-Bus while an ear is turned by hand.

```sh
sh measure.sh /mnt/usbkey/karotz-measure     # on the Karotz, over SSH
```
