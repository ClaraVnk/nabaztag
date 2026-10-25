#!/usr/bin/env python3
"""Refuse to flash an image that is going to brick the rabbit.

A Nabaztag has no bootloader you can fall back on and no reset worth the name.
When an image faults early the CPU dead-loops in the Data Abort vector and the
only way back is a J-Link on the JTAG pads, or a Raspberry Pi bit-banging
them — see BRICK_FORENSICS.md and RECOVERY.md, which exist because that
already happened.

So this reads the built image before anyone flashes it and says no. Every
check below is a static property of the artefacts: no rabbit is involved, it
runs in a second, and it belongs in the build.

The first check is the one that bricked a rabbit on 2026-06-20. The brick was
an uninitialised global read before anything wrote it: `-fdata-sections`
splits uninitialised globals into per-variable `.bss.*` sections, those land
outside `__bss_start__..__bss_end__`, and cstartup's zero-loop only clears
that range. The audio ring-buffer index was read as garbage, gave an
out-of-bounds load in audioPlayFetchByte, a Data Abort, and the dead loop.

What this does NOT check is that all of `.bss` is zeroed, and the reason
matters. apply-mods.py's note of 2026-06-21 says bracketing `*(.bss.*)`
correctly leaves ZERO VM heap on the 16 KB SRAM, so the bytecode loader cannot
allocate and the rabbit stalls right after bc.jsp. The unzeroed region doubles
as the heap, deliberately, and the fix was to force the handful of globals
that are read before they are written into `.data` instead -- see
patch_audio_fifo_data and patch_cold_state.

So the check is "is every read-before-write global initialised", and its list
is read out of apply-mods.py so the two cannot drift apart. An earlier draft
of this file checked the whole of `.bss` and condemned nine good images and a
freshly built one.

  usage: preflight.py bin/lean [--key keys/signing_pubkey.h]

Exit status is the number of failed checks, so it drops into a build script.
Pure stdlib: no pyelftools, because a tool that refuses to flash has no
business having dependencies that might not be installed on the day.
"""

from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

# From sys/ml67q4051.ld. The last flash sector holds configuration data and is
# deliberately outside the usable region.
INTROM_ORIGIN, INTROM_LENGTH = 0x08000000, 124 * 1024
INTRAM_ORIGIN, INTRAM_LENGTH = 0x10000000, 16 * 1024
# ExtRAM is declared as 32 KB, not the 1 MB the stock script claimed: the USB
# pool starts at EXTRAM_ADDR 0xD0008000 and the VM heap at SRAM_BASE
# 0xD0010000 (inc/utils/mem.h, inc/vm/vmem.h). A 1 MB region would let a
# section be placed straight through both, and neither is visible to the
# linker. .extbss lives in the 32 KB below the pool -- see patch_extbss.
EXTRAM_ORIGIN, EXTRAM_LENGTH = 0xD0000000, 32 * 1024
# What the linker cannot see, and so what a region check cannot cover.
USB_POOL_ORIGIN = 0xD0008000   # EXTRAM_ADDR, inc/utils/mem.h
VM_HEAP_ORIGIN = 0xD0010000    # SRAM_BASE / _vmem_heap, inc/vm/vmem.h

# sys/asm/init.s carves 0x1680 bytes off the top of IntRAM for the six mode
# stacks, in this order downwards: UND 0x80, SVC 0x80, ABT 0x80, FIQ 0x100,
# IRQ 0x400, and the user stack 0x1000 at the bottom of the block. So the user
# stack pointer starts just under the five privileged ones and grows down
# through its 4 KB.
STACK_BYTES = 0x1680
USR_STACK_SIZE = 0x1000
# What the user stack pointer starts at: the top of IntRAM less the five
# stacks above it (0x1680 - 0x1000).
USR_STACK_TOP = 0x10000000 + 16 * 1024 - (STACK_BYTES - USR_STACK_SIZE)
HEAP_GAP = 0x100

# Sections the KEEP() protections exist for: --gc-sections would otherwise
# drop them, and a vector table dropped is an image that never starts.
MUST_EXIST = (".intvec",)
MUST_EXIST_PREFIX = (".startup", ".ramfunc", ".bytecode")

SHT_NOBITS = 8
SHF_ALLOC = 0x2


class Elf:
    """Just enough ELF32 little-endian to answer the questions above."""

    def __init__(self, path: Path):
        self.path = path
        d = path.read_bytes()
        if d[:4] != b"\x7fELF":
            raise ValueError(f"{path}: not an ELF file")
        if d[4] != 1 or d[5] != 1:
            raise ValueError(f"{path}: not 32-bit little-endian")
        (e_shoff,) = struct.unpack_from("<I", d, 0x20)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x2E)

        raw = []
        for i in range(e_shnum):
            off = e_shoff + i * e_shentsize
            (name, stype, flags, addr, offset, size, link, info,
             align, entsize) = struct.unpack_from("<10I", d, off)
            raw.append(dict(name=name, type=stype, flags=flags, addr=addr,
                            offset=offset, size=size, link=link, info=info,
                            entsize=entsize))

        strtab_off = raw[e_shstrndx]["offset"]

        def name_at(idx: int) -> str:
            end = d.index(b"\0", strtab_off + idx)
            return d[strtab_off + idx:end].decode("ascii", "replace")

        self.sections = []
        for s in raw:
            s["sname"] = name_at(s["name"])
            self.sections.append(s)

        self.symbols: dict[str, int] = {}
        for s in self.sections:
            if s["sname"] not in (".symtab",):
                continue
            strs = self.sections[s["link"]]["offset"]
            n = s["size"] // (s["entsize"] or 16)
            for i in range(n):
                off = s["offset"] + i * s["entsize"]
                st_name, st_value = struct.unpack_from("<II", d, off)
                if st_name == 0:
                    continue
                end = d.index(b"\0", strs + st_name)
                self.symbols[d[strs + st_name:end].decode("ascii", "replace")] = st_value

    def allocated(self):
        return [s for s in self.sections
                if (s["flags"] & SHF_ALLOC) and s["size"] > 0]

    def by_prefix(self, prefix: str):
        return [s for s in self.sections if s["sname"].startswith(prefix)]


class Report:
    def __init__(self):
        self.failures = 0

    def ok(self, what: str, detail: str = "") -> None:
        print(f"  ok    {what}" + (f" — {detail}" if detail else ""))

    def fail(self, what: str, detail: str) -> None:
        self.failures += 1
        print(f"  FAIL  {what}\n        {detail}")

    def note(self, what: str, detail: str = "") -> None:
        print(f"  note  {what}" + (f" — {detail}" if detail else ""))


def guard_names(repo: Path) -> tuple[list[str], str]:
    """The globals that are read before anything writes them.

    Parsed out of apply-mods.py's own edits rather than copied, because a list
    that lives in two places is a list that disagrees with itself. Falls back
    to what those steps hold today if the parse fails, and says so.
    """
    fallback = ["usbhost_init_status", "usbctrl_state", "rec_state",
                "play_state", "eapol_state", "replay_active",
                "ieee80211_state", "play_w", "play_r"]
    mods = repo / "apply-mods.py"
    if not mods.is_file():
        return fallback, "apply-mods.py not found, using the built-in list"
    text = mods.read_text(encoding="utf-8", errors="replace")
    found = []
    # Both steps write `<type> <name> __attribute__((section(".data")))`,
    # or an extern of the same, into a replacement string.
    for m in re.finditer(r'(\w+)\s*\{data\}', text):
        if m.group(1) not in found:
            found.append(m.group(1))
    for m in re.finditer(r'(\w+)\s+__attribute__\(\(section\(["\\]*\.data', text):
        if m.group(1) not in found:
            found.append(m.group(1))
    if len(found) < 3:
        return fallback, "could not parse apply-mods.py, using the built-in list"
    return found, f"{len(found)} name(s) read out of apply-mods.py"


def check_guards_initialised(elf: Elf, r: Report, repo: Path) -> None:
    names, how = guard_names(repo)
    r.note("read-before-write globals", how)

    in_bss = []
    for n in names:
        if any(s["sname"] == f".bss.{n}" and s["size"] > 0 for s in elf.sections):
            in_bss.append(n)

    if in_bss:
        r.fail("every read-before-write global is initialised",
               f"{', '.join(in_bss)} still live in a .bss.* section, which "
               f"cstartup does not clear. One of these read as garbage is the "
               f"2026-06-20 brick: an out-of-bounds load, a Data Abort, and "
               f"the CPU dead-loops in the vector. Run apply-mods.py with the "
               f"audio_fifo_data and cold_state steps")
    else:
        r.ok("every read-before-write global is initialised",
             f"all {len(names)} are out of .bss.*")


def note_bss_region(elf: Elf, r: Report) -> None:
    """How much .bss is left unzeroed, which here is by design."""
    start = elf.symbols.get("__bss_start__")
    end = elf.symbols.get("__bss_end__")
    orphans = [s for s in elf.by_prefix(".bss")
               if s["size"] > 0 and not (start is not None and end is not None
                                         and s["addr"] >= start
                                         and s["addr"] + s["size"] <= end)]
    total = sum(s["size"] for s in orphans)
    if start is None or end is None:
        r.fail("the zero-loop's bounds exist",
               "__bss_start__ or __bss_end__ is missing, so nothing can say "
               "what cstartup clears")
        return
    r.note("the zero-loop clears", f"{end - start} bytes "
           f"(0x{start:08x}..0x{end:08x})")
    if orphans:
        r.note(f"{len(orphans)} .bss.* section(s) are left as they were",
               f"{total} bytes — deliberate, the VM heap sits over them; see "
               f"apply-mods.py's note of 2026-06-21")


def check_no_overlap(elf: Elf, r: Report) -> None:
    secs = sorted(elf.allocated(), key=lambda s: s["addr"])
    clash = []
    for a, b in zip(secs, secs[1:]):
        if a["addr"] + a["size"] > b["addr"]:
            clash.append((a["sname"], a["addr"], a["size"], b["sname"], b["addr"]))
    if clash:
        n, aa, sz, m, ba = clash[0]
        r.fail("no two sections share an address",
               f"{n} (0x{aa:08x}+{sz}) runs into {m} (0x{ba:08x})"
               + (f", and {len(clash)-1} more" if len(clash) > 1 else ""))
    else:
        r.ok("no two sections share an address", f"{len(secs)} allocated")


def check_regions(elf: Elf, r: Report) -> None:
    regions = (("IntROM", INTROM_ORIGIN, INTROM_LENGTH),
               ("IntRAM", INTRAM_ORIGIN, INTRAM_LENGTH),
               ("ExtRAM", EXTRAM_ORIGIN, EXTRAM_LENGTH))
    stray = []
    for s in elf.allocated():
        lo, hi = s["addr"], s["addr"] + s["size"]
        if not any(o <= lo and hi <= o + l for _, o, l in regions):
            stray.append((s["sname"], lo, s["size"]))
    if stray:
        n, a, sz = stray[0]
        r.fail("every section is inside a declared memory region",
               f"{n} at 0x{a:08x} ({sz} B) is in none of IntROM, IntRAM or "
               f"ExtRAM" + (f", and {len(stray)-1} more" if len(stray) > 1 else ""))
    else:
        r.ok("every section is inside a declared memory region")

    for name, origin, length in regions:
        used = [s for s in elf.allocated()
                if origin <= s["addr"] < origin + length]
        if not used:
            continue
        top = max(s["addr"] + s["size"] for s in used)
        r.note(f"{name} used to 0x{top:08x}",
               f"{top - origin} of {length} bytes")


def check_stack_margin(elf: Elf, r: Report, floor: int) -> None:
    """How far the user stack can grow before it hits a live global.

    Two numbers, and the second is the one that matters. An earlier version of
    this check measured only `.data` and the bracketed `.bss`, on the grounds
    that the orphan `.bss.*` sections deliberately overlap the heap. That is
    right for the zero-loop question and wrong for this one: those orphans are
    live globals, and pacifox's issue #27 names them — IRQ_HANDLER_TABLE, the
    _vmem_* pointers, and vlog.c's 4 KB buffer_temp. The stack does not care
    that they are unzeroed; it will write over them.

    So the margin is from the user stack pointer's starting value down to the
    highest allocated object in internal RAM, whatever section it is in. On a
    lean build that is about 1.9 KB, and the first thing a deeper call chain
    overwrites is the interrupt dispatch table.
    """
    bss_end = elf.symbols.get("__bss_end__")
    zeroed = [s for s in elf.allocated()
              if INTRAM_ORIGIN <= s["addr"] < INTRAM_ORIGIN + INTRAM_LENGTH
              and s["sname"] in (".data", ".bss")]
    if zeroed:
        top = max(s["addr"] + s["size"] for s in zeroed)
        if bss_end is not None:
            top = max(top, bss_end)
        r.note("the initialised data ends at", f"0x{top:08x}")

    ram = [s for s in elf.allocated()
           if INTRAM_ORIGIN <= s["addr"] < INTRAM_ORIGIN + INTRAM_LENGTH]
    if not ram:
        r.note("nothing in internal RAM")
        return

    highest = max(ram, key=lambda s: s["addr"] + s["size"])
    top = highest["addr"] + highest["size"]
    stack_top = elf.symbols.get("__stack_start__")
    # __stack_start__ is the top of the whole 0x1680 block; the user stack
    # begins below the five privileged ones.
    usr_top = (stack_top - (STACK_BYTES - USR_STACK_SIZE)
               if stack_top is not None else USR_STACK_TOP)
    margin = usr_top - top

    if margin < 0:
        r.fail("the user stack has room before it reaches a global",
               f"{highest['sname']} ends at 0x{top:08x}, which is {-margin} "
               f"bytes ABOVE where the user stack pointer starts "
               f"(0x{usr_top:08x}) — they share memory from the first call")
    elif margin < floor:
        r.fail("the user stack has room before it reaches a global",
               f"{margin} bytes between 0x{usr_top:08x} and {highest['sname']} "
               f"at 0x{top:08x}, and the floor is {floor}. A deeper call chain "
               f"overwrites that global; on a lean build the first one is the "
               f"interrupt dispatch table. Raise it with --stack-floor if that "
               f"is deliberate")
    else:
        r.ok("the user stack has room before it reaches a global",
             f"{margin} bytes down to {highest['sname']} at 0x{top:08x}")


def check_memory_landmarks(elf: Elf, r: Report) -> None:
    """The linker's own symbols have to describe a RAM that can exist.

    Added after this gate passed an image that would have bricked the rabbit,
    2026-10-25. A step that removed libc's malloc also removed the objects
    that pulled it in, the linker then placed the orphaned `.bss.*` sections
    *inside* the `.bss` output section instead of after it, and three things
    went wrong at once:

        __bss_end__     0x10003208   above the stacks init.s reserves from
                                     0x10002980 -- .bss lands on them, which
                                     is the 2026-06-20 brick
        __heap_start__  0x10003208
        __heap_end__    0x10002888   below its own start: a negative heap
        __stack_end__   0x10002988   below __bss_end__: .bss over the stack

    Every other check passed. They look at sections, and none of these is a
    section: a heap is two symbols and a stack is a region nothing declares.

    The lesson worth keeping is why the good layout was good. Nothing in the
    linker script arranged for the orphans to sit after `__bss_end__` — the
    linker simply placed them there, given that set of input objects. The
    working firmware depended on an accident, so any change to what got
    linked could flip it. `extbss` ends the accident: `.bss.*` is collected
    explicitly and 8 KB of scratch moves to external RAM so the result fits
    under the stacks. These checks stay anyway, because the next step that
    changes the link set will not announce itself either.
    """
    # Two allocators live in external RAM at fixed addresses that no section
    # header mentions: the USB/hcd pool and the VM heap. The linker will
    # happily place a section straight through either, and nothing downstream
    # would notice until a rabbit fetched its bytecode into the USB pool.
    ext = [t for t in elf.allocated() if t["addr"] >= EXTRAM_ORIGIN]
    clashes = []
    for t in ext:
        end = t["addr"] + t["size"]
        for name, base in (("the USB/hcd pool", USB_POOL_ORIGIN),
                           ("the VM heap", VM_HEAP_ORIGIN)):
            if end > base:
                clashes.append((t, name, base))
    if clashes:
        for t, name, base in clashes:
            r.fail("external RAM leaves the two allocators alone",
                   f"{t['sname']} 0x{t['addr']:08x}.."
                   f"0x{t['addr'] + t['size']:08x} runs into {name} at "
                   f"0x{base:08x}, which is a bare address in C "
                   f"(inc/utils/mem.h, inc/vm/vmem.h) and invisible to the "
                   f"linker")
    elif ext:
        top = max(t["addr"] + t["size"] for t in ext)
        r.ok("external RAM leaves the two allocators alone",
             f"{len(ext)} section(s) up to 0x{top:08x}, below the USB pool "
             f"at 0x{USB_POOL_ORIGIN:08x}")

    bss_end = elf.symbols.get("__bss_end__")
    heap_start = elf.symbols.get("__heap_start__")
    heap_end = elf.symbols.get("__heap_end__")
    stack_end = elf.symbols.get("__stack_end__")

    if heap_start is not None and heap_end is not None:
        if heap_end <= heap_start:
            r.fail("the newlib heap has a positive size",
                   f"__heap_start__ 0x{heap_start:08x} is at or above "
                   f"__heap_end__ 0x{heap_end:08x}, so the heap is empty or "
                   f"inverted. Anything that allocates gets an address out of "
                   f"the region, or _sbrk's bounds check misbehaves")
        else:
            r.ok("the newlib heap has a positive size",
                 f"{heap_end - heap_start} bytes")

    # Non-blocking, because the firmware we ship has this and runs: the libc
    # heap is declared over the orphan globals. It is harmless only as long as
    # nothing reaches libc's allocator -- main.c's MALLOC is usbh_malloc and
    # the bytecode's rand is sysRand(), so the one door left is libc rand().
    # Said plainly: the shipping firmware is one rand() call away from
    # overwriting counter_timer. A note, not a failure, so this gate does not
    # start rejecting the image on four rabbits -- but it is written down.
    if heap_start is not None and heap_end is not None and heap_end > heap_start:
        over = [t for t in elf.allocated()
                if t["sname"].startswith(".bss.")
                and heap_start <= t["addr"] < heap_end]
        if over:
            first = min(over, key=lambda t: t["addr"])
            r.note("the libc heap is declared over globals",
                   f"{len(over)} section(s), {sum(t['size'] for t in over)} "
                   f"bytes, starting at {first['sname']} 0x{first['addr']:08x}. "
                   f"Safe only while nothing calls libc malloc; libc rand() does")

    if bss_end is not None and stack_end is not None:
        if bss_end > stack_end:
            r.fail("the zeroed region stops below the stack",
                   f"__bss_end__ 0x{bss_end:08x} is above __stack_end__ "
                   f"0x{stack_end:08x}: the zero-loop writes into the stack "
                   f"region, and whatever the stack holds at that moment is "
                   f"cleared under it")
        else:
            r.ok("the zeroed region stops below the stack",
                 f"{stack_end - bss_end} bytes between them")

    # Orphaned .bss.* sections below __bss_end__ mean the linker has started
    # collecting them without anyone saying where they go, which on a 16 KB
    # SRAM puts .bss on the stacks. The check above catches that directly;
    # this one names the cause, because the symptom is a rabbit that fetches
    # its bytecode and then stops.
    #
    # It reports nothing once extbss is applied: there are no orphans left to
    # find, which is the point of that step. Said plainly because the note
    # this replaced claimed the orphans WERE the VM heap, and they never
    # were -- _vmem_heap is a fixed address in external RAM.
    orphans = [t for t in elf.allocated()
               if t["sname"].startswith(".bss.")
               and INTRAM_ORIGIN <= t["addr"] < INTRAM_ORIGIN + INTRAM_LENGTH]
    if orphans and bss_end is not None:
        swallowed = [t for t in orphans if t["addr"] < bss_end]
        if swallowed:
            total = sum(t["size"] for t in swallowed)
            r.fail("no orphan .bss.* has been swallowed by .bss",
                   f"{len(swallowed)} .bss.* section(s), {total} bytes, now "
                   f"sit below __bss_end__ 0x{bss_end:08x}. The linker has "
                   f"started collecting them with nowhere to put them, which "
                   f"on a 16 KB SRAM runs .bss into the stacks -- the "
                   f"2026-06-20 brick. Apply extbss, which places them and "
                   f"moves 8 KB of scratch to external RAM")
        else:
            r.ok("no orphan .bss.* has been swallowed by .bss",
                 f"{len(orphans)} left above __bss_end__, unzeroed -- apply "
                 f"extbss to collect them")


def check_kept_sections(elf: Elf, r: Report) -> None:
    """The sections KEEP() exists for, when the build has them as sections.

    Not every build does, and that is not a fault: with the stock linker
    script the vector table and the startup code are simply part of .text.
    What matters is that *something* is mapped at the reset address, so that
    is what fails; a missing named section is a note.
    """
    at_reset = [s for s in elf.allocated() if s["addr"] == INTROM_ORIGIN]
    if not at_reset:
        r.fail("something is mapped at the reset address",
               f"no section starts at 0x{INTROM_ORIGIN:08x}, which is the "
               f"first address the CPU fetches from")
    else:
        r.ok("something is mapped at the reset address",
             f"{at_reset[0]['sname']}, {at_reset[0]['size']} bytes")

    missing = [n for n in MUST_EXIST
               if not any(s["sname"] == n and s["size"] > 0 for s in elf.sections)]
    for prefix in MUST_EXIST_PREFIX:
        if not any(s["sname"].startswith(prefix) and s["size"] > 0
                   for s in elf.sections):
            missing.append(prefix + "*")
    if missing:
        r.note("the KEEP()-protected sections are not separate here",
               f"{', '.join(missing)} — folded into .text, which the stock "
               f"linker script does. Only a concern once --gc-sections is on "
               f"and the linker script names them")
    else:
        r.ok("the sections KEEP() protects are still here")


def check_reset_vector(bin_path: Path, r: Report) -> None:
    if not bin_path.is_file():
        r.note(f"{bin_path.name} is absent, not checking the reset vector")
        return
    head = bin_path.read_bytes()[:4]
    if len(head) < 4:
        r.fail("the reset vector is an instruction", "the image is shorter than a word")
        return
    (word,) = struct.unpack("<I", head)
    # Two idioms are correct at a vector, and only accepting one of them was
    # my own bug: `B somewhere` (0xEA……) and `LDR PC, [PC, #offset]`
    # (0xE59FF0……), which is what a table of 32-bit handler addresses needs
    # and what this firmware actually uses.
    if (word >> 24) == 0xEA:
        target = INTROM_ORIGIN + 8 + ((word & 0x00FFFFFF) << 2)
        r.ok("the reset vector is an instruction", f"B 0x{target:08x}")
    elif (word & 0xFFFFF000) == 0xE59FF000:
        r.ok("the reset vector is an instruction",
             f"LDR PC, [PC, #{word & 0xFFF}]")
    else:
        r.fail("the reset vector is an instruction",
               f"the first word is 0x{word:08x}, which is neither a branch "
               f"(0xEA……) nor LDR PC (0xE59FF0……). The CPU fetches it as the "
               f"reset vector whatever it is")


def check_size(bin_path: Path, r: Report) -> None:
    if not bin_path.is_file():
        return
    size = bin_path.stat().st_size
    if size > INTROM_LENGTH:
        r.fail("the image fits in flash",
               f"{size} bytes against {INTROM_LENGTH} usable "
               f"({size - INTROM_LENGTH} over)")
    else:
        r.ok("the image fits in flash",
             f"{size} of {INTROM_LENGTH} bytes, {INTROM_LENGTH - size} spare")


def check_signature(build: Path, r: Report, key: Path | None) -> None:
    signed = sorted(build.glob("*.signed.sim"))
    if not signed:
        r.note("no signed .sim here, nothing to verify")
        return
    verify = Path(__file__).resolve().parent / "signing" / "verify_sim.py"
    if not verify.is_file():
        r.note("signing/verify_sim.py is absent, not verifying the signature")
        return
    cmd = [sys.executable, str(verify), str(signed[0])]
    if key:
        cmd += ["--pubkey", str(key)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa — a missing tool is not a bad signature
        r.note(f"could not run verify_sim.py ({exc})")
        return
    if out.returncode == 0:
        r.ok("the signature verifies", signed[0].name)
    else:
        r.fail("the signature verifies",
               f"{signed[0].name}: {(out.stderr or out.stdout).strip()[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Static checks on a built firmware, before anyone flashes it.")
    ap.add_argument("build", type=Path,
                    help="a build directory, e.g. bin/lean")
    ap.add_argument("--key", type=Path, default=None,
                    help="public key for the signature check")
    ap.add_argument("--stack-floor", type=int, default=1024,
                    help="bytes the user stack must have before it reaches the "
                         "highest global in internal RAM (default 1024)")
    args = ap.parse_args()

    build: Path = args.build
    if not build.is_dir():
        print(f"{build}: not a directory", file=sys.stderr)
        return 2

    elfs = sorted(build.glob("*.elf"))
    if not elfs:
        print(f"{build}: no .elf here, so there is nothing to check. Build "
              f"with the elf kept, or point this at the right directory.",
              file=sys.stderr)
        return 2

    print(f"== preflight {build}")
    r = Report()
    try:
        elf = Elf(elfs[0])
    except ValueError as exc:
        print(f"  FAIL  {exc}")
        return 1

    repo = Path(__file__).resolve().parent
    check_guards_initialised(elf, r, repo)
    note_bss_region(elf, r)
    check_no_overlap(elf, r)
    check_regions(elf, r)
    check_stack_margin(elf, r, args.stack_floor)
    check_memory_landmarks(elf, r)
    check_kept_sections(elf, r)
    check_reset_vector(build / "Nab.bin", r)
    check_size(build / "Nab.bin", r)
    check_signature(build, r, args.key)

    print()
    if r.failures:
        print(f"== {r.failures} check(s) failed — do not flash this")
    else:
        print("== every check passed")
    return r.failures


if __name__ == "__main__":
    raise SystemExit(main())
