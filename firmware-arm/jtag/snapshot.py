#!/usr/bin/env python3
"""Take everything off a live rabbit that a virtual one needs to be believable.

The emulator has always been fed invented material: an EEPROM written by
mkconf.py, a fake access point written from the standard, and a firmware
built on a dev VM. Every one of those is a guess about what a real rabbit
holds, and a guess that is wrong in the right place produces a bug that does
not exist -- or hides one that does. On 2026-11-07 the fake AP sent replay
counters that ran backwards, the firmware correctly rejected them, and for
twenty minutes that looked exactly like the Wi-Fi fault we were hunting.

So: with the JTAG station wired, take the real thing.

    ./snapshot.py -o snap/ --elf ../bin/lean-wpa3-timed/Nab.elf

What comes back, and what each piece is for:

  flash.bin    128 KB from 0x08000000 — the firmware actually on the chip,
               which is the only honest answer to "what is it running"
  config.bin   sector 31 alone, 4096 bytes — the rabbit's own configuration
               page. Hand it to rabbit-vm as --eeprom and the virtual rabbit
               boots with the real one's SSID, server and stored PMK instead
               of something mkconf.py made up.
  iram.bin     16 KB of internal SRAM from 0x10000000 — .data and .bss, so
               every global the firmware holds, frozen
  eram.bin     external RAM, with --extram. Slow (a megabyte over bit-banged
               JTAG), so it is off by default
  state.txt    the named globals, decoded, when an --elf is given. This is
               the part that turns "the belly is orange" into
               "ieee80211_mode is 0, it never asked the driver to connect"

The rabbit is halted for the read and resumed afterwards. It keeps running.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FLASH_BASE, FLASH_SIZE = 0x08000000, 0x20000
SECTOR_SIZE, CONF_SECTOR = 0x1000, 31
IRAM_BASE, IRAM_SIZE = 0x10000000, 0x4000
ERAM_BASE, ERAM_SIZE = 0xD0000000, 0x100000

# The globals worth printing without being asked. Everything else comes from
# --sym, or --all for the lot.
INTERESTING = [
    "ieee80211_state", "ieee80211_mode", "ieee80211_encryption",
    "ieee80211_authmode", "ieee80211_group_cipher", "ieee80211_assoc_ssid",
    "ieee80211_assoc_bssid", "ieee80211_assoc_channel", "ieee80211_timeout",
    "eapol_state", "replay_active", "replay_counter",
    "ieee80211_igtk_set", "ieee80211_rssi_average",
]


def openocd(cfg: Path, oki: Path, commands: list[str]) -> str:
    cmd = ["openocd", "-f", str(cfg), "-f", str(oki)]
    for c in commands:
        cmd += ["-c", c]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # OpenOCD writes everything to stderr, including the echo output.
    return r.stdout + r.stderr


def symbols(elf: Path) -> dict[str, tuple[int, int]]:
    """name -> (address, size), for the variables that live in RAM."""
    out = subprocess.run(["nm", "-S", str(elf)], capture_output=True,
                         text=True, check=True).stdout
    syms: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        m = re.match(r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+(\S)\s+(\S+)$",
                     line.strip())
        if not m:
            continue
        addr, size, kind, name = (int(m.group(1), 16), int(m.group(2), 16),
                                  m.group(3), m.group(4))
        # Address, not section letter. This linker script puts some
        # initialised globals in a section nm reports as 'T', and filtering on
        # the letter silently dropped ieee80211_state, eapol_state and
        # replay_active -- three of the most useful variables on the chip.
        if IRAM_BASE <= addr < IRAM_BASE + IRAM_SIZE and size:
            syms[name] = (addr, size)
    return syms


def render(name: str, raw: bytes) -> str:
    if len(raw) == 1:
        return f"0x{raw[0]:02x} ({raw[0]})"
    if len(raw) in (2, 4):
        v = int.from_bytes(raw, "little")
        return f"0x{v:0{len(raw) * 2}x} ({v})"
    text = "".join(chr(c) if 32 <= c < 127 else "." for c in raw)
    hexed = " ".join(f"{c:02x}" for c in raw[:24])
    tail = " …" if len(raw) > 24 else ""
    return f"{hexed}{tail}  |{text}|"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=Path, default=Path("snap"))
    ap.add_argument("--elf", type=Path, help="the ELF of the image on the chip")
    ap.add_argument("--force-elf", action="store_true",
                    help="decode even when the flash does not match the Nab.bin "
                         "beside the ELF. Every address is then suspect")
    ap.add_argument("--cfg", type=Path, default=Path("nabaztag-pi.cfg"))
    ap.add_argument("--oki", type=Path, default=Path("oki.tcl"))
    ap.add_argument("--extram", action="store_true",
                    help="also dump the megabyte of external RAM (slow)")
    ap.add_argument("--sym", action="append", default=[],
                    help="an extra global to decode; repeatable")
    ap.add_argument("--decode-only", action="store_true",
                    help="decode an existing snapshot, read nothing from the "
                         "rabbit")
    ap.add_argument("--all", action="store_true",
                    help="decode every global of 64 bytes or less")
    args = ap.parse_args()

    if not shutil.which("openocd"):
        sys.exit("openocd is not on PATH — run this on the station")
    args.out.mkdir(parents=True, exist_ok=True)
    flash, iram = args.out / "flash.bin", args.out / "iram.bin"

    # A plain halt, and interrupts masked before any read. Halting the time
    # base first (oki_wd_off) is right before erasing flash and wrong here:
    # with the counter stopped the core stops acknowledging debug requests.
    # And a running rabbit takes USB interrupts constantly -- OpenOCD's
    # memory reads run instructions on the core, an interrupt steals one, and
    # the read dies on "timeout waiting for SYSCOMP & DBGACK". Setting I and
    # F in CPSR makes every read reliable; the original is put back before
    # the rabbit is resumed, so it carries on none the wiser.
    #
    # Even then a long read is flaky over a bit-banged adapter, so this goes
    # in chunks and retries each one. A snapshot that fails nine times out of
    # ten is not an instrument.
    def grab(path: Path, base: int, size: int, chunk: int = 0x4000) -> bool:
        parts = []
        for off in range(0, size, chunk):
            n = min(chunk, size - off)
            piece = args.out / f".part{off:x}"
            for attempt in range(3):
                log = openocd(args.cfg, args.oki, [
                    "halt", "reg cpsr 0x800000f0",
                    f"dump_image {piece} {base + off:#x} {n:#x}",
                    f"reg cpsr {cpsr0:#x}", "resume", "shutdown"])
                if piece.exists() and piece.stat().st_size == n:
                    break
                print(f"  {path.name} +{off:#x}: attempt {attempt + 1} failed")
            else:
                print(log[-600:])
                return False
            parts.append(piece.read_bytes())
            piece.unlink()
        path.write_bytes(b"".join(parts))
        return True

    if args.decode_only:
        if not (flash.exists() and iram.exists()):
            sys.exit(f"{args.out} holds no snapshot to decode")
    else:
      # Whatever the rabbit was running with, so it can be handed back.
      log = openocd(args.cfg, args.oki, ["halt", "echo cpsr=[reg cpsr]",
                                         "resume", "shutdown"])
      m = re.search(r"cpsr \(/32\): (0x[0-9a-fA-F]+)", log)
      cpsr0 = int(m.group(1), 16) if m else 0x80000070

      if not grab(flash, FLASH_BASE, FLASH_SIZE):
          sys.exit("could not read the flash")
      if not grab(iram, IRAM_BASE, IRAM_SIZE):
          sys.exit("could not read internal RAM")
      if args.extram and not grab(args.out / "eram.bin", ERAM_BASE, ERAM_SIZE):
          sys.exit("could not read external RAM")

    blob = flash.read_bytes()
    conf = args.out / "config.bin"
    conf.write_bytes(blob[CONF_SECTOR * SECTOR_SIZE:
                          (CONF_SECTOR + 1) * SECTOR_SIZE])
    print(f"{flash}: {len(blob)} bytes")
    print(f"{conf}: {SECTOR_SIZE} bytes — pass this to rabbit-vm as --eeprom")
    print(f"{iram}: {IRAM_SIZE} bytes")

    if not args.elf:
        print("no --elf, so no decoded state; pass the ELF of the image on "
              "the chip and this prints what every global holds")
        return 0

    # The ELF has to be the image that is actually in the chip. On 2026-11-07
    # it was not -- `bin/full/Nab.elf` read against a chip running
    # lean-wpa3-timed -- and nothing complained, because a wrong address
    # returns plausible bytes rather than an error. gc_sections and extbss
    # move the whole layout, so the decoded state was fiction that read like
    # evidence. The flash is already in hand, so the claim is checkable.
    binary = args.elf.with_name("Nab.bin")
    if binary.exists():
        want = binary.read_bytes()
        if blob[:len(want)] != want:
            first = next((i for i, (a, b) in enumerate(zip(blob, want)) if a != b),
                         len(want))
            msg = (f"{args.elf} is NOT the image in this chip: the flash differs "
                   f"from {binary.name} at offset {first:#x}. Every address below "
                   f"would be read from the wrong layout. Find the right build "
                   f"(the &z= stamp in the boot URL names the build minute) or "
                   f"pass --force-elf if you know better.")
            if not args.force_elf:
                sys.exit(msg)
            print(f"warning: {msg}")
    else:
        print(f"note: no {binary.name} beside the ELF, so the image in the chip "
              f"could not be confirmed to match it")

    ram = iram.read_bytes()
    syms = symbols(args.elf)
    wanted = list(syms) if args.all else INTERESTING + args.sym
    lines = [f"# state of the rabbit, decoded against {args.elf.name}", ""]
    for name in wanted:
        if name not in syms:
            lines.append(f"{name:28} (not in this image)")
            continue
        addr, size = syms[name]
        if args.all and size > 64:
            continue
        off = addr - IRAM_BASE
        lines.append(f"{name:28} @{addr:#010x}  {render(name, ram[off:off + size])}")
    (args.out / "state.txt").write_text("\n".join(lines) + "\n")
    print(f"{args.out / 'state.txt'}: {len(lines) - 2} globals decoded")
    print()
    print("\n".join(lines[2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
