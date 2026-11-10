#!/usr/bin/env python3
"""Which translation blocks ran a different number of times in two qemu
traces (-d in_asm,exec,nochain). argv: traceA traceB elf -> per-function
instruction difference, using arm-none-eabi-nm for names."""
import re, subprocess, sys, collections
trace_re = re.compile(r"^Trace \d+: 0x[0-9a-f]+ \[[0-9a-f]+/([0-9a-f]+)/")
insn_re = re.compile(r"^0x([0-9a-f]+):")


def objd_count(line):
    """Instructions in a block that qemu >= 8 prints as raw bytes, without a
    disassembler: `OBJD-T: <hex>` (Thumb, 2 bytes each, BL/BLX a 4-byte
    pair starting 0b11110) or `OBJD-A: <hex>` (ARM, 4 bytes each). None if
    the line is not one of those."""
    kind, _, hexs = line.partition(":")
    hexs = hexs.strip()
    if kind == "OBJD-A":
        return len(hexs) // 8
    if kind != "OBJD-T":
        return None
    b = bytes.fromhex(hexs)
    n = i = 0
    while i + 1 < len(b):
        hw = b[i] | (b[i + 1] << 8)
        i += 4 if (hw >> 11) == 0b11110 else 2
        n += 1
    return n
def load(path):
    tb, cnt, cur = {}, collections.Counter(), None
    for line in open(path):
        m = trace_re.match(line)
        if m: cnt[int(m.group(1), 16) & ~1] += 1; continue
        if line.startswith("IN:"): cur = None; continue
        k = objd_count(line)
        if k is not None and cur is not None:
            tb[cur] += k - 1          # the address line already counted one
            continue
        m = insn_re.match(line)
        if m:
            a = int(m.group(1), 16)
            if cur is None: cur = a; tb[cur] = 0
            tb[cur] += 1
    return tb, cnt
ta, ca = load(sys.argv[1]); tb, cb = load(sys.argv[2])
syms = []
for l in subprocess.run(["arm-none-eabi-nm", "-n", sys.argv[3]], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) == 3 and p[1] in "tT": syms.append((int(p[0], 16) & ~1, p[2]))
def name(pc):
    best = "?"
    for a, n in syms:
        if a <= pc: best = n
        else: break
    return best
diff = collections.Counter()
for pc in set(ca) | set(cb):
    n = ta.get(pc, tb.get(pc, 0))
    d = (cb[pc] - ca[pc]) * n
    if d: diff[name(pc)] += d
for f, d in sorted(diff.items(), key=lambda x: -abs(x[1])): print(f"{d:+10d} {f}")
