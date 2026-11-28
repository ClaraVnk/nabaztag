# Flashing a rabbit by JTAG — the commands, in order

The short way, from a clone on the Pi: `station.sh`, next to this file (see README.md). This runbook is the long way, step by step, from another machine over ssh.

Copy-paste runbook. No judgement calls, no reading between the lines. For why
any of it is shaped this way, see [README.md](README.md); for the recovery
story around it, [../docs/RECOVERY.md](../docs/RECOVERY.md).

**Flashing is at your own risk.**

Two things that are true before you start and worth knowing, because both look
like failure and neither is:

- **The first write pass dies.** Every time, on a healthy rabbit, somewhere in
  the first few sectors, with `Error: not halted` / `failed to write memory`.
  Relaunch the same command and the second pass completes. Budget two runs.
- **A flash ends in a software reset, and that leaves the radio asleep.** The
  first belly light blinks orange and stays there. Unplug, wait ten seconds,
  plug back in. Only the mains wakes the 2006 Wi-Fi dongle.

## 0. What you need

- the rabbit **on its own mains adapter**, open, five jumpers to the Pi
  (wiring in [README.md](README.md#wiring) — the Pi powers nothing)
- the Pi reachable over ssh. Below it is called `pi-host`; put your own host
  or `~/.ssh/config` alias there
- an image built: `bin/<mode>/Nab.bin`, with `<mode>` usually `lean`

```sh
./build.sh --mode lean --remote <user@dev-vm> --runtime podman
```

Never build on the Home Assistant host: the build starves the add-on and drops
the rabbit mid-session.

## 1. Put the station on the Pi (first time only)

```sh
scp -r firmware-arm/jtag pi-host:~/nabaztag-flash
ssh -t pi-host 'bash ~/nabaztag-flash/setup-station.sh'
```

## 2. Prove the wiring before touching anything

```sh
ssh pi-host 'cd ~/nabaztag-flash && openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_id; resume; shutdown"'
```

**The `resume` is not optional.** Attaching halts the core, and `shutdown`
does not undo that: without it the command that was supposed to "change
nothing" leaves the rabbit frozen until you pull its plug. Measured
2026-11-08 — `curstate` read `halted` after an `oki_id; shutdown`.

What you want to see is `Examination succeed` and a `halt` that lands. The
line `flash id: manufacturer 0x18, device 0x18` followed by
`(expected 0x98 / 0xcd -- check the wiring before writing anything)` is
**normal and not a wiring fault**: autoselect never engages on this part, so
the read returns array data, not an identifier. Ignore the complaint; trust
`Examination succeed`, and trust the read-back comparison at step 5.

If the first `halt` times out, run the command again.

## 3. Back the current flash up

Five minutes, and it is the only copy of what the rabbit is running now.

```sh
ssh pi-host 'cd ~/nabaztag-flash && openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump /tmp/before.bin; resume; shutdown"'
scp pi-host:/tmp/before.bin .
sha256sum before.bin
```

Keep it off the Pi's card. **A dump carries the rabbit's Wi-Fi passphrase and
PMK in sector 31** — the `.gitignore` beside this file keeps dumps out of git;
keep them out of anywhere else too.

## 4. Write the image

```sh
scp firmware-arm/bin/lean/Nab.bin pi-host:~/nabaztag-flash/Nab.new.bin
ssh -t pi-host 'cd ~/nabaztag-flash && ./flash.sh Nab.new.bin'
tail -f /tmp/flash-progress.log      # on the Pi, from another shell
```

The JTAG link does not last a whole image: the first pass usually dies
partway through the erase, and later passes can die a few thousand words
into the write. `flash.sh` reads the flash back after each pass (in 16 KB
pieces) and plans only what is left (`mkfwprog.py --against`): a sector
already right is skipped, one with its missing words still erased is
completed without an erase. Up to `FLASH_PASSES` passes (8); running it
again after an interruption also resumes. It exits 0 only when the
read-back is byte-identical to the image.

Sectors 30 and 31 are spared, so the rabbit keeps its Wi-Fi and server
configuration. `mkfwprog.py` reports every thousand words: a silent run and
a wedged run look identical, and the rabbit cannot boot until this
finishes.

## 5. Check what is actually in the chip

The chip's own writer has no error checking — `write_ok` is commented out in
nabgcc's `mem.c` — so a flash that wrote nothing looks exactly like one that
worked. This is the step that tells them apart, and it is not optional.

```sh
ssh pi-host 'cd ~/nabaztag-flash && openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump /tmp/after.bin; resume; shutdown"'
scp pi-host:/tmp/after.bin .
cmp -n $(stat -f%z firmware-arm/bin/lean/Nab.bin) after.bin firmware-arm/bin/lean/Nab.bin && echo "byte-exact"
```

`cmp` compares only as many bytes as the image is long, because the dump is
the whole 128 KB and the image is shorter.

## 6. Power-cycle and confirm from the warren

Unplug, ten seconds, plug back in. Then the boot URL in the Terrier's log is
the one thing the rabbit says about itself that only a new image can change:

```
GET /vl/bc.jsp?v=0.0.0.13&m=…&h=4&z=<build mark>&s=1
```

`z=` is the build stamp of the image now running. If it is the stamp of what
you just wrote, the flash landed. If it has not changed, it did not — whatever
else looked fine.

## 7. Listening to a running rabbit (no halt)

A firmware built with the `dcc_telemetry` step (modes `lean-wpa3-tel`,
`lean-wpa3-slim-tel`) prints a status line over the JTAG wires without the
core ever being halted: Wi-Fi state, SAE, EAPOL, USB errors, resets,
bytecode loads. The fields are explained at the top of `listen.sh`.

```sh
ssh pi-host 'cd ~/nabaztag-flash && ./listen.sh | tee -a ~/listen.log'
```

Stop it with Ctrl-C: the script kills OpenOCD (only SIGKILL stops it), or
the next run fails with "Error requesting gpio line tdo"; then
`pkill -9 openocd` on the Pi. Listening never halts the core, so the rabbit
keeps running whatever happens to the listener.

## When it goes wrong

| what you see | what it means |
| --- | --- |
| `Error: not halted`, `failed to write memory`, first run | expected; relaunch |
| `manufacturer 0x18, device 0x18` | expected; the identifier is not readable on this part |
| `DBGACK timeout` on every attempt | RESETN is wired. Unwire it |
| `scan chain interrogation failed: all zeroes` | the rabbit has no power. That is the whole message |
| JTAG does not answer at all | the rabbit is not on its mains, or TDO/TDI are swapped |
| the rabbit goes silent after a read | you forgot `resume`, or the halt outlasted three XMPP probes. Pull the plug |
| first belly light blinks orange after the flash | expected; it needs the mains, not another flash |
| `z=` unchanged after a power cycle | nothing was written. Go back to step 4 |

A true brick — a Data Abort dead-loop — and its bisection ladder are in
[../docs/BRICK_FORENSICS.md](../docs/BRICK_FORENSICS.md).
