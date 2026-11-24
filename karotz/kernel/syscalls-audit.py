#!/usr/bin/env python3
"""Which system calls Terrier OS's programs make that a 6.1 kernel built from
karotz-6.1.config does not implement (ENOSYS), before the rabbit says so.

    syscalls-audit.py <vmlinux> <System.map> <syscall.tbl> <rootfs dir or ELF file>...
        [--objdump arm-none-eabi-objdump] [--all]

Why: three of 2026-11-24's faults on Gengar were syscalls the kernel did not
serve (nanosleep, clock_gettime and select without CONFIG_COMPAT_32BIT_TIME,
flock without CONFIG_FILE_LOCKING), each found one boot at a time, by what
stopped working. The kernel knows its own table and the binaries say which
numbers they put in r7 before `svc 0`: this reads both.

Kernel side: sys_call_table in vmlinux (its address and length from
System.map), each entry resolved to a symbol; `sys_ni_syscall` is a number
the kernel answers with ENOSYS. User side: every ARM ELF under the roots is
disassembled and each `svc 0` is paired with the value last put in r7 (a mov,
an orr/add on it, or a literal loaded from the pool). uClibc's syscall()
wrapper takes the number from a register: counted as "dynamic", not named.

Prints, per binary, the syscalls it makes that the kernel lacks; then the
union, with the binaries, and for each the programs that import the C
library's function of that name (a daemon calls shmget(), the svc is in
libuClibc): who would get the ENOSYS. Exit 1 when the union is not empty (--all prints
every syscall seen, implemented or not). The judgement stays human: a few
numbers are probed with a fallback (uClibc tries clock_gettime64 before
clock_gettime, statx before fstatat64).
"""
import os
import re
import struct
import subprocess
import sys

EM_ARM = 40
PT_LOAD = 1
# ARM's private calls (cacheflush, set_tls...), served outside the table.
ARM_NR_BASE = 0x0f0000
ARM_PRIVATE = {1: "breakpoint", 2: "cacheflush", 3: "usr26", 4: "usr32", 5: "set_tls", 6: "get_tls"}


def die(msg):
    sys.exit(f"syscalls-audit: {msg}")


def kernel_table(vmlinux, system_map):
    """nr -> symbol name of the entry, from the table in vmlinux."""
    syms = []
    for line in open(system_map, encoding="utf-8", errors="replace"):
        parts = line.split()
        if len(parts) >= 3:
            syms.append((int(parts[0], 16), parts[2]))
    syms.sort()
    # Every syscall the kernel lacks is a weak alias of sys_ni_syscall (the
    # same address under many names): entries are told by address.
    by_addr = {}
    for a, n in syms:
        by_addr.setdefault(a, n)
    table = next((a for a, n in syms if n == "sys_call_table"), None)
    ni = next((a for a, n in syms if n == "sys_ni_syscall"), None)
    if table is None or ni is None:
        die("sys_call_table or sys_ni_syscall not in System.map")
    by_addr[ni] = "sys_ni_syscall"
    after = next(a for a, n in syms if a > table)
    count = (after - table) // 4
    data = open(vmlinux, "rb").read()
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        die("vmlinux is not a 32-bit little-endian ELF")
    e_phoff, = struct.unpack_from("<I", data, 28)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
    for i in range(e_phnum):
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(
            "<IIIII", data, e_phoff + i * e_phentsize)
        if p_type == PT_LOAD and p_vaddr <= table < p_vaddr + p_filesz:
            off = p_offset + (table - p_vaddr)
            words = struct.unpack_from(f"<{count}I", data, off)
            return {nr: by_addr.get(w, f"?{w:08x}") for nr, w in enumerate(words)}
    die("sys_call_table is in no PT_LOAD segment")


def names(tbl):
    """nr -> name, from arch/arm/tools/syscall.tbl (the EABI numbers)."""
    out = {}
    for line in open(tbl, encoding="utf-8"):
        line = line.split("#", 1)[0].split()
        if len(line) >= 3 and line[1] in ("common", "eabi"):
            out[int(line[0])] = line[2]
    return out


def elf_arm(path):
    try:
        with open(path, "rb") as f:
            h = f.read(20)
    except OSError:
        return False
    return len(h) == 20 and h[:4] == b"\x7fELF" and h[4] == 1 and \
        struct.unpack_from("<H", h, 18)[0] == EM_ARM


def find_elves(roots):
    for r in roots:
        if os.path.isfile(r):
            if elf_arm(r):
                yield r
            continue
        for d, _, files in os.walk(r):
            for f in sorted(files):
                p = os.path.join(d, f)
                if not os.path.islink(p) and elf_arm(p):
                    yield p


INSN = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{4,8})\s+(\S+)\s*(.*)$")
IMM = re.compile(r"#(-?\d+)")


def syscalls_of(path, objdump):
    """The syscall numbers a binary puts in r7 before svc 0; None = dynamic."""
    res = subprocess.run([objdump, "-d", path], capture_output=True, text=True,
                         errors="replace")
    words, lines = {}, []
    for line in res.stdout.splitlines():
        m = INSN.match(line)
        if m:
            words[int(m.group(1), 16)] = int(m.group(2), 16)
            lines.append(m)
    seen, r7 = set(), None
    for m in lines:
        mnem, ops = m.group(3), m.group(4)
        if mnem in ("svc", "swi") and ops.split()[0] in ("0x00000000", "0", "0x0"):
            seen.add(r7)
            continue
        if not ops.startswith("r7,"):
            continue
        imm = IMM.search(ops)
        if mnem in ("mov", "movs") and imm and "r7, r" not in ops:
            r7 = int(imm.group(1))
        elif mnem in ("orr", "add") and ops.startswith("r7, r7, #") and imm and r7 is not None:
            r7 = r7 | int(imm.group(1)) if mnem == "orr" else r7 + int(imm.group(1))
        elif mnem == "ldr" and "[pc, #" in ops and imm:
            # the literal is 8 bytes past this instruction in ARM mode
            addr = int(m.group(1), 16) + 8 + int(imm.group(1))
            r7 = words.get(addr)
        else:
            r7 = None
    return seen


def importers(path, objdump, wanted):
    """The dynamic symbols a binary imports among `wanted` (libc wrappers)."""
    res = subprocess.run([objdump, "-T", path], capture_output=True, text=True,
                         errors="replace")
    found = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if "*UND*" in parts and parts[-1] in wanted:
            found.add(parts[-1])
    return found


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    objdump = "arm-none-eabi-objdump"
    if "--objdump" in sys.argv:
        objdump = sys.argv[sys.argv.index("--objdump") + 1]
        args.remove(objdump)
    show_all = "--all" in sys.argv
    if len(args) < 4:
        die(__doc__.strip().splitlines()[2].strip())
    vmlinux, system_map, tbl, roots = args[0], args[1], args[2], args[3:]
    table = kernel_table(vmlinux, system_map)
    name = names(tbl)
    missing = {nr for nr, sym in table.items() if sym == "sys_ni_syscall"}
    print(f"kernel: {len(table)} entries in sys_call_table, "
          f"{len(missing)} answered with ENOSYS")
    union, elves = {}, list(find_elves(roots))
    for path in elves:
        nrs = syscalls_of(path, objdump)
        dyn = None in nrs
        nrs.discard(None)
        lacking = sorted(n for n in nrs if n < ARM_NR_BASE and (n in missing or n >= len(table)))

        def label(n):
            if n >= ARM_NR_BASE:
                return f"arm_{ARM_PRIVATE.get(n - ARM_NR_BASE, 'private')}({n:#x})"
            return f"{name.get(n, 'nr')}({n})"
        if show_all:
            print(f"{path}: {' '.join(label(n) for n in sorted(nrs))}"
                  f"{' +dynamic' if dyn else ''}")
        if lacking:
            print(f"{path}: ENOSYS for {' '.join(label(n) for n in lacking)}")
            for n in lacking:
                union.setdefault(n, []).append(os.path.basename(path))
    if not union:
        print("OK: no syscall made by these binaries is missing from the kernel")
        return 0
    print("== missing from the kernel, and who calls them:")
    wanted = {name.get(n, "") for n in union}
    users = {}
    for path in elves:
        for fn in importers(path, objdump, wanted):
            users.setdefault(fn, []).append(os.path.basename(path))
    for n in sorted(union):
        fn = name.get(n, "nr")
        via = f" ; via libc: {' '.join(sorted(set(users[fn])))}" if fn in users else ""
        print(f"   {fn}({n}): {' '.join(union[n])}{via}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
