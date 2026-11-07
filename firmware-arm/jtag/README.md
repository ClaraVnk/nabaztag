# The JTAG station

A Raspberry Pi as the probe, five jumpers, no J-Link. This is how a bricked
Nabaztag/tag v2 gets its firmware back — and, more often useful, the only way
to find out what firmware a rabbit is *actually* running.

That second use is not hypothetical. On 2026-11-07 a dump showed a rabbit
running a build from 21 June, byte-exact, after five months of flashes that
had all reported success. Nothing else in this project could have told us.

![wiring](wiring.svg)

The picture is generated: `./mkwiring.py -o wiring.svg`.

## What is here

| file | what it is |
| --- | --- |
| `wiring.svg` / `mkwiring.py` | the five jumpers, and what draws them |
| `setup-station.sh` | provisions a fresh Pi: packages, group, GPIO bank check |
| `nabaztag-pi.cfg` | OpenOCD: linuxgpiod adapter, ARM7TDMI target, no SRST |
| `oki.tcl` | the OKI ML67Q4051 flash sequence — id, dump, erase, program |
| `mkfwprog.py` | turns a `Nab.bin` into the TCL that writes it |

`oki.tcl` is reconstructed from the firmware's own writer,
`write_uc_flash_sec()` in nabgcc `src/utils/mem.c` — the same unlock
addresses, commands and completion test the rabbit uses on itself. Stock
OpenOCD has no driver for this part, and RedoXyde's patched 0.8.0, which
does, cannot drive a Pi's GPIO: it predates linuxgpiod and modern kernels
dropped the sysfs interface it used.

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

Pin numbers are physical and identical on a Pi 2, 3, 4 or Zero 2 W. A Zero 2 W
is slower per GPIO round-trip; nothing else changes.

**The rabbit stays on its own mains adapter.** The Pi drives five logic lines
and nothing else — it does not power the SoC, and an unpowered board does not
answer JTAG.

## Setting the Pi up

```sh
scp -r jtag pi-host:~/nabaztag-flash
ssh -t pi-host 'bash ~/nabaztag-flash/setup-station.sh'
```

## Three things to do with it

**Prove the wiring, change nothing.** Manufacturer `0x98`, device `0xcd`:

```sh
openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_id; shutdown"
```

**Read the flash back.** 128 KB at `0x08000000`, about five minutes:

```sh
openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump /tmp/flash.bin; shutdown"
sha256sum /tmp/flash.bin        # compare with bin/<mode>/Nab.bin
```

Take one before writing anything, and keep it off the card.

**Write an image.** Erase and verify, program, read back:

```sh
./mkfwprog.py ../bin/lean/Nab.bin -o /tmp/fwprog.tcl
openocd -f nabaztag-pi.cfg -f oki.tcl -f /tmp/fwprog.tcl -c shutdown
```

Budget about **two hours**: half an hour to erase and verify thirty sectors,
an hour and a half to program thirty thousand words at four bytes a command.
It prints a line every thousand words, because a silent run and a wedged run
look identical and the rabbit cannot boot until it finishes. `--no-erase`
resumes a write whose sectors are already erased.

Sectors 30 and 31 are spared, so the rabbit keeps its Wi-Fi and server
configuration; erase 31 and it comes back as it left the factory.

## What bites

- **Do not wire RESETN.** With it connected and SRST declared, this chip
  answers `DBGACK timeout`. And `reset halt` does not work on this ARM7TDMI
  however you configure it. To reset the rabbit, pull the plug.
- **The first `halt` on a running rabbit often times out.** Run the command
  again. A rabbit already dead-looping in an abort handler halts first time.
- **The flash identifier cannot be read on this part — ignore it.** Autoselect
  never engages: `oki_id` reads the array itself. `0x18/0x18` is just the low
  byte of the two words at the reset vector (`0xe59ff018`, twice), and
  `0xFF/0xFF` is the same read once sector 0 is blank. An earlier version of
  this file called `0x18/0x18` the signature of a session left in autoselect;
  that was wrong, and a gate built on it went on to refuse every legitimate
  write. `oki_id` is logged for the record and decides nothing.
- **Halt the core before erasing anything, and keep it halted.** This is the
  one that actually bites. On 2026-11-08 a write erased sectors 0–2 while the
  core was running: the chip executed the hole where its own startup code had
  been, left debug state, and OpenOCD lost it with
  `timeout waiting for SYSCOMP & DBGACK`. The rabbit then would not boot —
  recoverable here, since the config page in sector 31 is spared and the image
  can simply be written again, but it costs the ten minutes twice.
  `mkfwprog.py` emits `oki_require_halted` ahead of the erase, which throws
  unless `curstate` is `halted`. The erase itself is checked by
  `oki_verify_erased` on every sector, and the whole image by a read-back and
  a byte compare — those are the checks with a basis.
- **Halt the watchdog before touching the flash.** A rabbit whose sector 0 is
  erased executes `0xFF`, faults, and resets itself over and over, so every
  read lands in a different reset. `oki_wd_off` stops the time base. Also in
  `oki_reset_mode`.
- **Sector erase is six writes and the last two are not a third unlock:**
  `AA, 55, 80`, then `AA, 55`, then `0x30` to the sector. Reuse the unlock
  helper for the second half and the part never starts — `FLACON` sits at
  `0x01` with FWEND never set. Nothing is erased in that case, so it costs a
  retry, not a rabbit.
- **The chip's own writer checks nothing.** `write_ok` is commented out in
  `mem.c`, and a flash that wrote nothing looks exactly like one that worked:
  the LEDs run the whole sequence and the rabbit reboots by itself. The
  `flash_verify` step in `apply-mods.py` fixes that in the firmware; here,
  always read back and compare.
