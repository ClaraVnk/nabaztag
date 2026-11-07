# The JTAG station

A Raspberry Pi as the probe, five jumpers, no J-Link. This is how a bricked
Nabaztag/tag v2 gets its firmware back, and — more often useful — how you
find out what firmware a rabbit is *actually* running, which nothing else in
this project can tell you.

Everything here was reconstructed from `nabgcc`'s own flash writer
(`src/utils/mem.c`, `write_uc_flash_sec`) after the original station's SD
card was lost. It is versioned this time.

## Wiring

The rabbit's 8-pin header is at the **top-left** corner of the board, rabbit
seen from the front, reachable after the four tri-wing screws of the base.

```
   rabbit                           Raspberry Pi (40-pin header)
   ------------------------------   ----------------------------
   1  3V3  (Vref)   leave unconnected
   2  GND                           pin 20
   3  nTRST         leave unconnected
   4  TDI                           pin 19   GPIO10
   5  TMS                           pin 24   GPIO8
   6  TCK                           pin 23   GPIO11
   7  TDO                           pin 21   GPIO9
   8  RESETN        leave unconnected  (deliberately — see below)
```

Any Pi with the standard 40-pin header works; the pin numbers above are
physical and identical on a 4, a 3 or a Zero 2 W. A Zero 2 W is slower per
GPIO round-trip, so expect the ten-minute full write to take somewhat
longer; nothing else changes.

**The rabbit stays on its own mains adapter.** The Pi drives the five logic
lines and nothing else — it does not power the SoC, and an unpowered board
does not answer JTAG.

## What the Pi needs

```sh
sudo apt install openocd        # 0.12 or newer, for the linuxgpiod driver
sudo usermod -aG gpio "$USER"   # or run openocd under sudo
```

OpenOCD 0.11 and older want the old `linuxgpiod_jtag_nums` form; the config
carries both, commented.

## Three things to do with it

**Prove the wiring, change nothing.** Manufacturer `0x98`, device `0xCD`:

```sh
openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_id; shutdown"
```

**Read the flash back.** 128 KB at `0x08000000`. This is the only honest
answer to "what is this rabbit running":

```sh
openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump /tmp/flash.bin; shutdown"
sha256sum /tmp/flash.bin        # compare with bin/<mode>/Nab.bin
```

A dump is also the backup to take *before* writing anything. Keep it off the
card.

**Write an image.**

```sh
./mkfwprog.py ../bin/lean/Nab.bin -o /tmp/fwprog.tcl
openocd -f nabaztag-pi.cfg -f oki.tcl -f /tmp/fwprog.tcl -c shutdown
```

It erases sectors 0–29, programs the image four bytes per command, then
reads it back so you can compare. Sectors 30 and 31 are spared, so the
rabbit keeps its Wi-Fi and server configuration; erase 31 and it comes back
as it left the factory.

## What bites

- **Do not wire RESETN.** With it connected and SRST declared, this chip
  answers `DBGACK timeout`. And `reset halt` does not work on this ARM7TDMI
  however you configure it. To reset the rabbit, pull the plug.
- **The first `halt` on a running rabbit often times out.** Run the command
  again; it catches on the second attempt. A rabbit already dead-looping in
  an abort handler halts first time.
- **The chip's own writer checks nothing.** `write_ok` is commented out in
  `mem.c`, and a flash that wrote nothing at all looks exactly like one that
  worked: the LEDs run their whole sequence and the rabbit reboots by
  itself. Always read back and compare. In November 2026 a rabbit ran, for
  two weeks, a firmware that four "successful" flashes had failed to
  replace.
- **A programmed word costs ~19 ms.** Raising `adapter speed` does not help
  and does make a long jumper flaky. Skipping the 0xFF words does help, and
  `mkfwprog.py` already does it.
