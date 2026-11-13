#!/usr/bin/env python3
"""flashsim -- the rabbit's own flash writer, run on an ARM7 over a flash that
can lose power, and the reset that follows.

./rabbit and ./rabbit-vm compile the firmware's C for the host, and their
flash_uc is a stub: "there is no flash here to brick". That is the right
answer for everything up to flashFirmware and no answer at all for what
happens after it, which is where a rabbit becomes a brick. This is the other
half. It takes the *built* image -- Nab.elf, the bytes a rabbit runs -- and
executes it on an ARM7TDMI model (Unicorn):

  boot    the reset vector, cstartup, the .data relocation, up to main();
  flash   the resident image's flash_uc, from the copy cstartup put in RAM,
          writing another image through a model of the Oki flash controller
          (FLACON, the SPD command sequences, erase to 0xFF, programming
          that can only clear bits), with the watchdog counted beside it;
  sweep   that flash interrupted after every operation in turn -- an erase or
          a four-byte word -- and a reset on what is left in the flash.

What is modelled and what is not is in docs/OTA-ANTIBRICK.md, section 4. The
short version: the instructions are the real ones, the flash controller is the
protocol the firmware speaks and nothing more, and the two times an operation
takes are parameters (--erase-ms, --prog-us) whose defaults add up to the
63 ms per sector that Oki's own comment in mem.c gives. A result in
operations or bytes is measured; a result in milliseconds is that assumption
multiplied out.

Pure Python but for Unicorn; the ELF reader is the fifty lines it needs.
"""

from __future__ import annotations

import argparse
import random
import struct
import sys
from pathlib import Path

from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_BLOCK,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_UNMAPPED, UC_PROT_ALL)
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_SP)

FLASH_BASE = 0x08000000
FLASH_SIZE = 0x20000
SECTOR = 0x1000
INTRAM, INTRAM_SIZE = 0x10000000, 0x4000
EXTRAM, EXTRAM_SIZE = 0xD0000000, 0x100000
STAGE = 0xD0020000          # where the new image waits, in the VM's heap
RETURN_PAD = 0xFFFF0000     # flash_uc's caller, if it ever comes back

FLACON = 0xB7000100
CLKCNT = 0xB7000010
TMEN, TMOVF = 0xB8001004, 0xB8001010
WDTCON, TBGCON, WDTCNT = 0xB7E00000, 0xB7E00004, 0xB7E00010

# Cycles per instruction, for the time the CPU itself takes between flash
# operations. An ARM7TDMI averages about 1.9 on core code; reads from the
# external RAM the image waits in cost wait states. It is a small term: at
# 32 MHz a whole 28-sector flash is about 0.4 s of instructions against
# 1.8 s of flash operations.
CYCLES_PER_INSN = 3.0
CPU_HZ = 32_000_000.0


class Elf:
    """Load segments and symbols of a 32-bit little-endian ARM ELF."""

    def __init__(self, path):
        self.path = str(path)
        b = Path(path).read_bytes()
        if b[:4] != b"\x7fELF" or b[4] != 1 or b[5] != 1:
            raise SystemExit(f"{path}: not a 32-bit little-endian ELF")
        phoff, shoff = struct.unpack_from("<II", b, 0x1C)
        phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", b, 0x2A)
        self.segments = []
        for i in range(phnum):
            (ptype, off, vaddr, paddr, filesz, memsz,
             _flags, _align) = struct.unpack_from("<IIIIIIII", b, phoff + i * phentsize)
            if ptype == 1 and filesz:
                self.segments.append((paddr, vaddr, b[off:off + filesz]))
        secs = [struct.unpack_from("<IIIIIIIIII", b, shoff + i * shentsize)
                for i in range(shnum)]
        self.sym = {}
        for s in secs:
            if s[1] != 2:      # SHT_SYMTAB
                continue
            stroff = secs[s[6]][4]
            for i in range(s[5] // 16):
                name, val, _size, info, _o, _ndx = struct.unpack_from(
                    "<IIIBBH", b, s[4] + i * 16)
                if info & 0xF in (0, 1, 2) and name:
                    end = b.index(b"\0", stroff + name)
                    self.sym.setdefault(b[stroff + name:end].decode(), val)

    def image(self) -> bytes:
        """What objcopy -O binary writes: the segments at their load address."""
        rom = [(p, d) for p, _v, d in self.segments
               if FLASH_BASE <= p < FLASH_BASE + FLASH_SIZE]
        end = max(p + len(d) for p, d in rom)
        out = bytearray(b"\0" * (end - FLASH_BASE))
        for p, d in rom:
            out[p - FLASH_BASE:p - FLASH_BASE + len(d)] = d
        return bytes(out)

    def data_init(self):
        """(RAM address, bytes) of .data as cstartup must leave it."""
        for p, v, d in self.segments:
            if INTRAM <= v < INTRAM + INTRAM_SIZE and FLASH_BASE <= p:
                return v, d
        return None


class PowerCut(Exception):
    pass


class Flash:
    """The Oki flash as the firmware addresses it.

    128 KB in 4 KB sectors. A command is a sequence of byte writes into the
    flash's own address range; FLACON bit 0 permits programming and bit 1
    says the last command completed. An erase sets a sector to 0xFF, a
    program ANDs four bytes into four cells -- a cell goes from 1 to 0 and
    only an erase takes it back.
    """

    ERASE = [(0x15554, 0xAA), (0x0AAA8, 0x55), (0x15554, 0x80),
             (0x15554, 0xAA), (0x0AAA8, 0x55)]
    PROG = [(0x15554, 0xAA), (0x0AAA8, 0x55), (0x15554, 0xA0)]

    def __init__(self, content: bytes, erase_ms: float, prog_us: float,
                 faults=()):
        self.mem = bytearray(b"\xff" * FLASH_SIZE)
        self.mem[:len(content)] = content
        self.erase_us = erase_ms * 1000.0
        self.prog_us = prog_us
        self.flacon = 0
        self.seq = []
        self.pbuf = None
        self.ops = []          # (kind, addr, before, after, t_start_us)
        self.anomalies = 0
        self.ignored = 0
        self.faults = [list(f) for f in faults]
        self.fault_hits = []
        self.on_op = None      # callback(kind, addr, before, after) -> None

    # -- faults: [kind, addr, remaining] ----------------------------------
    def _fault(self, kind, addr):
        for f in self.faults:
            if f[0] == kind and f[1] == addr and f[2] != 0:
                if f[2] > 0:
                    f[2] -= 1
                self.fault_hits.append((kind, addr, len(self.ops)))
                return True
        return False

    def write(self, off: int, val: int):
        if not (self.flacon & 1):
            self.ignored += 1
            return
        if self.pbuf is not None:
            self.pbuf.append((off, val))
            if len(self.pbuf) == 4:
                self._program()
            return
        seq = self.seq + [(off, val)]
        if seq == self.PROG:
            self.pbuf, self.seq = [], []
        elif len(seq) == 6 and seq[:5] == self.ERASE and val == 0x30:
            self.seq = []
            self._erase(off & ~(SECTOR - 1))
        elif seq == self.ERASE[:len(seq)] or seq == self.PROG[:len(seq)]:
            self.seq = seq
        else:
            self.anomalies += 1
            self.seq = []

    def _erase(self, base):
        before = bytes(self.mem[base:base + SECTOR])
        after = bytearray(b"\xff" * SECTOR)
        if self._fault("erase-weak", base):
            # A sector that reports done and is not: one cell in 97 unmoved.
            for i in range(5, SECTOR, 97):
                after[i] = before[i] & 0xFE
        self._commit("erase", base, before, bytes(after), self.erase_us)

    def _program(self):
        addr = self.pbuf[0][0]
        vals = bytes(v for _a, v in self.pbuf)
        ok = all(a == addr + i for i, (a, _v) in enumerate(self.pbuf))
        self.pbuf = None
        if not ok:
            self.anomalies += 1
            return
        before = bytes(self.mem[addr:addr + 4])
        after = bytes(b & v for b, v in zip(before, vals))
        if self._fault("prog-weak", addr & ~3) or self._fault("stuck", addr & ~3):
            after = before         # the word did not take
        self._commit("prog", addr, before, after, self.prog_us)

    def _commit(self, kind, addr, before, after, dur_us):
        if self.on_op:
            self.on_op(kind, addr, before, after, dur_us)   # may raise PowerCut
        self.mem[addr:addr + len(after)] = after
        self.flacon |= 2


class Machine:
    """An ML67Q4051 as far as a reset and a flash need one."""

    def __init__(self, flash: Flash, wdt_halt_works: bool = False):
        self.flash = flash
        self.wdt_halt_works = wdt_halt_works
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(0x00000000, FLASH_SIZE, UC_PROT_ALL)    # the boot alias
        uc.mem_map(FLASH_BASE, FLASH_SIZE, UC_PROT_ALL)
        uc.mem_map(INTRAM, INTRAM_SIZE, UC_PROT_ALL)
        uc.mem_map(EXTRAM, EXTRAM_SIZE, UC_PROT_ALL)
        uc.mem_map(RETURN_PAD, 0x1000, UC_PROT_ALL)
        uc.mem_write(RETURN_PAD, b"\xfe\xe7" * 0x800)       # thumb: b .
        self.mmio_pages = set()
        self._sync_flash()
        self.insns = 0
        self.t_us = 0.0
        self.dirty = False
        self.stop_reason = None
        self.stops = {}
        self.stop_on_thumb = False
        self.flash_exec_while_writing = []
        self.writing = False
        # system state
        self.clkcnt = 0x00040100       # ring oscillator selected, as at reset
        self.tmen = 0
        # watchdog: armed by wdt_start() in main(); 2.09 s
        self.wd_running = False
        self.wd_window_us = 2_090_000.0
        self.wd_halted = False
        self.wd_last = 0.0
        self.wd_longest = 0.0
        self.wd_unlock = False
        self.wd_cnt_unlock = False
        self.wd_c3 = False
        self.wd_bite_at = None
        self.kicks = 0
        self.cut_at = None             # (op index, "before"|"mid")
        self.mmio_log = None           # list to record (addr, size, value)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._flash_write,
                    begin=FLASH_BASE, end=FLASH_BASE + FLASH_SIZE - 1)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        uc.hook_add(UC_HOOK_BLOCK, self._block)
        flash.on_op = self._on_op

    # -- memory --------------------------------------------------------------
    def _sync_flash(self):
        self.uc.mem_write(0, bytes(self.flash.mem))
        self.uc.mem_write(FLASH_BASE, bytes(self.flash.mem))
        self.dirty = False

    def _map_mmio(self, addr):
        page = addr & ~0xFFF
        if page in self.mmio_pages:
            return
        self.mmio_pages.add(page)
        self.uc.mem_map(page, 0x1000, UC_PROT_ALL)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._mmio_write, begin=page, end=page + 0xFFF)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._mmio_read, begin=page, end=page + 0xFFF)

    def _unmapped(self, uc, access, addr, size, value, _ud):
        # Peripherals live at 0x4000_0000 and up, outside the two RAMs.
        if 0x40000000 <= addr < 0xD0000000 and not (addr & 0xFFF) + size > 0x1000:
            self._map_mmio(addr)
            return True
        return False

    def _mmio_read(self, uc, access, addr, size, value, _ud):
        if addr == FLACON:
            uc.mem_write(FLACON, bytes([self.flash.flacon]))
        elif addr == CLKCNT:
            uc.mem_write(CLKCNT, struct.pack("<I", self.clkcnt))
        elif addr == TMOVF:
            uc.mem_write(TMOVF, struct.pack("<I", 1 if self.tmen else 0))

    def _mmio_write(self, uc, access, addr, size, value, _ud):
        if self.mmio_log is not None:
            self.mmio_log.append((addr, size, value & ((1 << (8 * size)) - 1)))
        if addr == FLACON:
            # 0x03: permit programming, clear the completion flag. 0x02 at the
            # end of a sector and clr_bit(...,0x01) in init_uc_flash prohibit.
            self.flash.flacon = value & 1
            self.flash.seq, self.flash.pbuf = [], None
        elif addr == CLKCNT:
            self.clkcnt = value & 0xFFFFFFFF
        elif addr == TMEN:
            self.tmen = value & 1
        elif addr == WDTCON:
            v = value & 0xFF
            if v == 0xC3:
                self.wd_c3 = True
            elif v == 0x3C:
                if self.wd_c3:
                    self._wd_account()
                    self.wd_last = self.now()
                    self.kicks += 1
                self.wd_running = True
                self.wd_c3 = False
        elif addr == TBGCON:
            v = value & 0xFF
            if v == 0x5A:
                self.wd_unlock = True
            else:
                if self.wd_unlock or self.wdt_halt_works:
                    self._wd_account()
                    was = self.wd_halted
                    self.wd_halted = bool(v & 0x80)
                    if was and not self.wd_halted:
                        self.wd_last = self.now()   # the count resumes
                    if self.wd_unlock and self.writing:
                        # flash_uc's last act: the divider set to 65 ms and
                        # the counter to overflow. The write is over; what
                        # the watchdog did during it is what was asked.
                        self.wd_running = False
                self.wd_unlock = False
        elif addr == WDTCNT:
            v = value & 0xFFFF
            if v == 0x5A5A:
                self.wd_cnt_unlock = True
            elif self.wd_cnt_unlock:
                # reset_uc()/flash_uc(): the counter set to overflow at once
                self.stop("reset")

    # -- time and the watchdog -------------------------------------------------
    def now(self):
        return self.t_us + self.insns * CYCLES_PER_INSN / CPU_HZ * 1e6

    def _wd_account(self):
        """Longest stretch the watchdog ran unfed; bite when it passes the window."""
        if not self.wd_running or self.wd_halted:
            return
        gap = self.now() - self.wd_last
        if gap > self.wd_longest:
            self.wd_longest = gap
        if gap > self.wd_window_us and self.wd_bite_at is None:
            self.wd_bite_at = self.wd_last + self.wd_window_us

    # -- execution hooks -----------------------------------------------------
    def _block(self, uc, addr, size, _ud):
        thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
        self.insns += size // (2 if thumb else 4)
        if addr in self.stops:
            self.stop(self.stops[addr])
            return
        if thumb and self.stop_on_thumb:
            self.stop("main:first Thumb code")
            return
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE or addr < FLASH_SIZE:
            if self.writing:
                self.flash_exec_while_writing.append(addr)
            if self.dirty:
                self._sync_flash()

    def _flash_write(self, uc, access, addr, size, value, _ud):
        self.dirty = True
        for i in range(size):
            try:
                self.flash.write(addr - FLASH_BASE + i, (value >> (8 * i)) & 0xFF)
            except PowerCut:
                self.stop("cut")
                return

    def _on_op(self, kind, addr, before, after, dur_us):
        k = len(self.flash.ops)
        t0 = self.now()
        self.flash.ops.append((kind, addr, before, after, t0, dur_us))
        if self.cut_at is not None and self.cut_at[0] == k:
            if self.cut_at[1] == "mid":
                half = mid_state(kind, before, after, k)
                self.flash.mem[addr:addr + len(half)] = half
            raise PowerCut()
        self.t_us += dur_us
        self._wd_account()
        if self.wd_bite_at is not None and self.stop_reason is None:
            # the reset arrives while this operation is in flight
            half = mid_state(kind, before, after, k)
            self.flash.mem[addr:addr + len(half)] = half
            self.flash.ops[-1] = (kind, addr, before, half, t0, dur_us)
            self.stop("watchdog")
            raise PowerCut()
        # the alias the firmware reads back through follows at once
        self.uc.mem_write(addr, after)

    def stop(self, why):
        if self.stop_reason is None:
            self.stop_reason = why
        self.uc.emu_stop()

    def run(self, pc, limit):
        self.stop_reason = None
        try:
            self.uc.emu_start(pc, 0xFFFFFFF0, count=limit)
        except UcError as e:
            if self.stop_reason is None:
                self.stop_reason = f"fault:{e}"
        if self.stop_reason is None:
            self.stop_reason = "limit"
        if self.dirty:
            self._sync_flash()
        return self.stop_reason

    def pc(self):
        return self.uc.reg_read(UC_ARM_REG_PC)


def mid_state(kind, before, after, seed):
    """A flash operation the power left half way through.

    An erase drifts every cell toward 1 and a program pulls some toward 0;
    stopped in the middle, each cell is wherever it had got to. Modelled as
    each bit having made the move or not, at random -- seeded, so a run can
    be repeated. No verdict below depends on which bits: a sector in this
    state is neither image's.
    """
    rnd = random.Random(seed)
    out = bytearray(before)
    for i in range(len(before)):
        m = rnd.getrandbits(8)
        out[i] = (before[i] & ~m | after[i] & m) & 0xFF
    return bytes(out)


# ---------------------------------------------------------------------------

BOOT_LIMIT = 3_000_000     # instructions; cstartup needs about 8,000, and a
                           # boot guard's CRC over 124 KB about 1.5 million


def boot(content: bytes, images, erase_ms=22.0, prog_us=40.0, limit=BOOT_LIMIT):
    """Reset on this flash content. -> (verdict, detail)

    `images` is a list of (name, Elf, bytes) the content is compared with. A
    flash boots when the CPU reaches main() *and* the content is one of the
    images, whole, with .data as that image's cstartup must leave it. The
    CPU reaching the address of a main() through a flash that is half one
    image and half another is not a boot.
    """
    m = Machine(Flash(content, erase_ms, prog_us))
    whole = None
    for name, elf, img in images:
        if content[:len(img)] == img:
            whole = (name, elf)
    mains = {}
    for name, elf, _img in images:
        if elf is None:
            # No symbols: cstartup is ARM and main() is the first Thumb code
            # an image of this toolchain runs.
            m.stop_on_thumb = True
            continue
        if "main" in elf.sym:
            mains.setdefault(elf.sym["main"] & ~1, []).append(name)
        if "nab_guard_rescue" in elf.sym:
            m.stops.setdefault(elf.sym["nab_guard_rescue"] & ~1, "guard-rescue")
    for addr, names in mains.items():
        m.stops.setdefault(addr, "main:" + "/".join(names))
    m.uc.reg_write(UC_ARM_REG_CPSR, 0xD3)        # SVC, IRQ and FIQ masked, ARM
    why = m.run(0x00000000, limit)
    if why == "guard-rescue":
        return "GUARD", f"the boot guard refused the image after {m.insns} instructions"
    if why.startswith("main:"):
        if whole is None:
            return "BRICK", (f"the CPU gets as far as a main() ({why[5:]}), in a "
                             "flash that is no whole image")
        name, elf = whole
        if elf is None:
            return "BOOTS", (f"{name}: first Thumb code at {m.pc():08x} after "
                             f"{m.insns} instructions (no ELF: .data not checked)")
        if name not in why[5:].split("/"):
            return "BRICK", f"whole image {name} but stopped at {why}"
        di = elf.data_init()
        if di and bytes(m.uc.mem_read(di[0], len(di[1]))) != di[1]:
            return "BRICK", f"{name}: main() reached with .data not relocated"
        return "BOOTS", f"{name}: main() after {m.insns} instructions, .data in place"
    mode = m.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F
    return "BRICK", f"{why} at pc={m.pc():08x} mode={mode:02x}" + (
        f" (whole image {whole[0]})" if whole else "")


def flash_run(resident: Elf, old: bytes, new: bytes, *, faults=(), cut_at=None,
              wdt_halt_works=False, erase_ms=22.0, prog_us=40.0):
    """Boot the resident image, then have its flash_uc write `new`.

    The writer is the copy cstartup relocated to RAM, called as sysFlash calls
    it: flash_uc(image, length, buffer_temp), interrupts off, the watchdog
    armed and just fed. -> Machine, with stop_reason in
    reset | returned | cut | watchdog | limit | fault:...
    """
    fl = Flash(old, erase_ms, prog_us)
    m = Machine(fl, wdt_halt_works)
    m.stops[resident.sym["main"] & ~1] = "main"
    m.uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
    if m.run(0, BOOT_LIMIT) != "main":
        raise SystemExit(f"the resident image does not boot: {m.stop_reason}")
    m.stops = {RETURN_PAD: "returned"}
    fl.faults = [list(f) for f in faults]
    fl.ops.clear()
    m.uc.mem_write(STAGE, new)
    m.uc.reg_write(UC_ARM_REG_R0, STAGE)
    m.uc.reg_write(UC_ARM_REG_R1, len(new))
    m.uc.reg_write(UC_ARM_REG_R2, resident.sym["buffer_temp"])
    m.uc.reg_write(UC_ARM_REG_LR, RETURN_PAD | 1)
    m.uc.reg_write(UC_ARM_REG_CPSR, 0xD0)       # user mode, IRQ and FIQ masked
    m.uc.reg_write(UC_ARM_REG_SP, INTRAM + INTRAM_SIZE - 0x680)
    # main() has run wdt_start() and the interpreter fed the dog before the opcode
    m.wd_running, m.wd_window_us = True, 2_090_000.0
    m.insns, m.t_us, m.wd_last, m.wd_longest = 0, 0.0, 0.0, 0.0
    m.cut_at = cut_at
    m.writing = True
    m.run(resident.sym["flash_uc"], 400_000_000)
    m.writing = False
    m._wd_account()
    m.t_end_us = m.now()
    m.ret = m.uc.reg_read(UC_ARM_REG_R0) if m.stop_reason == "returned" else None
    return m


def classify(content, old, new):
    if content[:len(new)] == new:
        return "NEW"
    if content[:len(old)] == old:
        return "OLD"
    return "MIXED"


def sectors_differing(content, img):
    return [s for s in range((len(img) + SECTOR - 1) // SECTOR)
            if content[s * SECTOR:min((s + 1) * SECTOR, len(img))]
            != img[s * SECTOR:(s + 1) * SECTOR]]


def load_image(path):
    p = Path(path)
    if p.suffix == ".elf":
        e = Elf(p)
        return e, e.image()
    return None, p.read_bytes()


def describe(m, old, new, images):
    content = bytes(m.flash.mem)
    cls = classify(content, old, new)
    verdict, detail = boot(content, images)
    ops = m.flash.ops
    n_er = sum(1 for o in ops if o[0] == "erase")
    n_pr = len(ops) - n_er
    print(f"  flash_uc ended by   {m.stop_reason}"
          + (f" (returned 0x{m.ret:x} to its caller)" if m.ret is not None else ""))
    print(f"  operations          {n_er} erases, {n_pr} words programmed, "
          f"{m.flash.anomalies} malformed commands")
    print(f"  time                {m.t_end_us / 1000:.0f} ms "
          f"({m.insns} instructions)")
    print(f"  watchdog            fed {m.kicks} times, longest unfed "
          f"{m.wd_longest / 1000:.0f} ms of {m.wd_window_us / 1000:.0f}"
          + (" -- BITES" if m.wd_bite_at is not None else ""))
    if m.flash.fault_hits:
        print(f"  injected faults hit {len(m.flash.fault_hits)}: "
              + ", ".join(f"{k}@0x{a:05x}" for k, a, _ in m.flash.fault_hits[:6]))
    if m.flash_exec_while_writing:
        print(f"  !! code ran from the flash during the write: "
              f"{len(m.flash_exec_while_writing)} blocks, first "
              f"0x{m.flash_exec_while_writing[0]:08x}")
    conf = [o for o in ops if o[1] >= FLASH_SIZE - SECTOR]
    if conf:
        print(f"  !! the configuration sector (0x1f000) was written: "
              f"{len(conf)} operations")
    print(f"  flash content       {cls}"
          + (f" ({len(sectors_differing(content, new))} sectors are not the new image's)"
             if cls == "MIXED" else ""))
    print(f"  reset on it         {verdict}: {detail}")
    return cls, verdict


def cmd_boot(a):
    elf, img = load_image(a.image)
    if elf is None:
        raise SystemExit("boot needs the .elf, for main()")
    v, d = boot(img, [("image", elf, img)])
    print(f"{a.image}: {len(img)} bytes -> {v}: {d}")
    return 0 if v == "BOOTS" else 1


def parse_faults(specs):
    out = []
    for s in specs or ():
        kind, addr, *n = s.split(":")
        if kind not in ("erase-weak", "prog-weak", "stuck"):
            raise SystemExit(f"unknown fault {kind}")
        out.append([kind, int(addr, 0), int(n[0]) if n else (-1 if kind == "stuck" else 1)])
    return out


def cmd_flash(a):
    res = Elf(a.resident)
    old = res.image()
    nelf, new = load_image(a.image)
    images = [("old", res, old), ("new", nelf, new)]
    print(f"resident {a.resident} ({len(old)} B) writes {a.image} ({len(new)} B); "
          f"TBGCON halt {'works' if a.wdt_halt_works else 'is ignored'}")
    m = flash_run(res, old, new, faults=parse_faults(a.fault),
                  wdt_halt_works=a.wdt_halt_works,
                  erase_ms=a.erase_ms, prog_us=a.prog_us)
    cls, verdict = describe(m, old, new, images)
    ok = cls == "NEW" and verdict == "BOOTS"
    if a.expect:
        want = a.expect.upper()
        got = verdict if verdict != "BOOTS" else f"BOOTS-{cls}"
        if got != want:
            print(f"FAIL  expected {want}, got {got}")
            return 1
        print(f"ok    {got}, as expected")
        return 0
    return 0 if ok else 1


def cmd_sweep(a):
    res = Elf(a.resident)
    old = res.image()
    nelf, new = load_image(a.image)
    images = [("old", res, old), ("new", nelf, new)]
    m = flash_run(res, old, new, wdt_halt_works=a.wdt_halt_works,
                  erase_ms=a.erase_ms, prog_us=a.prog_us)
    ops = m.flash.ops
    print(f"resident {a.resident} ({len(old)} B) writes {a.image} ({len(new)} B)")
    print(f"uninterrupted: {m.stop_reason}, {len(ops)} operations, "
          f"{m.t_end_us / 1000:.0f} ms, flash {classify(bytes(m.flash.mem), old, new)}")
    if m.stop_reason != "reset":
        print("the uninterrupted flash did not end in a reset; nothing to sweep")
        return 1
    # the state of the flash before each operation, and after the last
    state = bytearray(b"\xff" * FLASH_SIZE)
    state[:len(old)] = old
    # what the flash holds beyond the old image is whatever was there; blank here
    cls = []
    snaps = {}
    want = set(pick_states(ops, a.samples))
    for k, (kind, addr, before, after, t0, dur) in enumerate(ops):
        cls.append(classify(state, old, new))
        if k in want:
            snaps[k] = bytes(state)
        state[addr:addr + len(after)] = after
    cls.append(classify(state, old, new))
    snaps[len(ops)] = bytes(state)
    n = len(ops)
    fatal = [k for k in range(n + 1) if cls[k] == "MIXED"]
    # a cut *during* operation k leaves a state between k and k+1: fatal when
    # either side is, and also on the first destructive and last completing one
    during = [k for k in range(n) if cls[k] == "MIXED" or cls[k + 1] == "MIXED"]
    print()
    print(f"power cut BETWEEN operations: {len(fatal)} of {n + 1} instants leave "
          f"a flash that is neither image")
    print(f"power cut DURING an operation: {len(during)} of {n} operations")
    if during:
        t_first = ops[during[0]][4]
        t_last = ops[during[-1]][4] + ops[during[-1]][5]
        b_first = ops[during[0]][1]
        nbytes = sum(len(ops[k][3]) for k in during if ops[k][0] == "prog")
        print(f"fatal window: from operation {during[0]} ({ops[during[0]][0]} "
              f"0x{b_first:05x}) to operation {during[-1]} -- "
              f"{t_last - t_first:.0f} us = {(t_last - t_first) / 1000:.0f} ms of the "
              f"{m.t_end_us / 1000:.0f} ms the flash takes; "
              f"{sum(1 for k in during if ops[k][0] == 'erase')} sectors erased and "
              f"{nbytes} bytes programmed inside it")
    else:
        print("fatal window: none -- no operation changes the flash")
    # reset on a sample of them, on the real instructions
    print()
    print(f"reset emulated on {len(snaps) + (len(a.mid and want) if a.mid else 0)} of those states:")
    tally = {}
    examples = {}
    for k in sorted(snaps):
        v, d = boot(snaps[k], images)
        key = (cls[k], v)
        tally[key] = tally.get(key, 0) + 1
        examples.setdefault(key, (k, d))
    if a.mid:
        for k in sorted(want):
            kind, addr, before, after, t0, dur = ops[k]
            s = bytearray(snaps[k])
            half = mid_state(kind, before, after, k)
            s[addr:addr + len(half)] = half
            c = classify(s, old, new)
            v, d = boot(bytes(s), images)
            key = (c + " (cut mid-operation)", v)
            tally[key] = tally.get(key, 0) + 1
            examples.setdefault(key, (k, d))
    bad = 0
    for (c, v), cnt in sorted(tally.items()):
        k, d = examples[(c, v)]
        print(f"  {cnt:5d}  flash {c:28s} -> {v:6s}  e.g. before op {k}: {d}")
        whole = c in ("OLD", "NEW")
        if whole != (v == "BOOTS"):
            if not (v == "GUARD" and not whole):
                bad += 1
    if a.expect_guard:
        unguarded = sum(cnt for (c, v), cnt in tally.items()
                        if not c.startswith(("OLD", "NEW")) and v != "GUARD")
        print(("ok   " if unguarded == 0 else "FAIL ")
              + f"{unguarded} damaged states got past the boot guard")
        bad += unguarded
    return 1 if bad else 0


def pick_states(ops, samples):
    """Which instants to reset on: every sector boundary, the first and last
    operations, and an even spread of the rest."""
    n = len(ops)
    ks = {0, 1, 2, n - 2, n - 1}
    for k, o in enumerate(ops):
        if o[0] == "erase":
            ks.update((k, k + 1))
    if samples and n:
        ks.update(range(0, n, max(1, n // samples)))
    return sorted(k for k in ks if 0 <= k < n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--erase-ms", type=float, default=22.0,
                    help="time one sector erase takes (default 22)")
    ap.add_argument("--prog-us", type=float, default=40.0,
                    help="time one four-byte program takes (default 40)")
    ap.add_argument("--wdt-halt-works", action="store_true",
                    help="assume set_bit(TBGCON,0x80) stops the watchdog "
                         "without the 0x5A key (default: the write is ignored)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boot", help="reset on an image")
    b.add_argument("image")
    b.set_defaults(fn=cmd_boot)
    f = sub.add_parser("flash", help="the resident image flashes another")
    f.add_argument("--resident", required=True, help="Nab.elf of the image in place")
    f.add_argument("--image", required=True, help="Nab.elf or Nab.bin to write")
    f.add_argument("--fault", action="append",
                   help="erase-weak:<sector addr>[:n] | prog-weak:<word addr>[:n] "
                        "| stuck:<word addr>")
    f.add_argument("--expect", help="BOOTS-NEW | BOOTS-OLD | BRICK | GUARD")
    f.set_defaults(fn=cmd_flash)
    s = sub.add_parser("sweep", help="cut the power at every operation")
    s.add_argument("--resident", required=True)
    s.add_argument("--image", required=True)
    s.add_argument("--samples", type=int, default=200,
                   help="resets to emulate besides the sector boundaries")
    s.add_argument("--mid", action="store_true",
                   help="also reset on each sampled operation cut half way")
    s.add_argument("--expect-guard", action="store_true",
                   help="fail unless every damaged state stops in the boot guard")
    s.set_defaults(fn=cmd_sweep)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
