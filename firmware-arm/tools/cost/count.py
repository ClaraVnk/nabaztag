#!/usr/bin/env python3
"""Sum executed instructions from `qemu-arm -d in_asm,exec,nochain` output.

in_asm prints each translation block once, one line per instruction, starting
with its address; exec prints "Trace N: <host> [<flags>/<pc>/...]" each time a
block runs. Instructions per block, keyed by the block's first pc, times the
number of times that block ran. With a pc as argv[1] (a tick function's
address, from nm), also the longest run of instructions between two
executions of that block -- the watchdog's view of the computation."""
import re
import sys

tick_pc = int(sys.argv[1], 16) & ~1 if len(sys.argv) > 1 else None
tb_len = {}
cur = None
executed = 0
blocks = 0
ticks = 0
since_tick = 0
max_gap = 0
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
for line in sys.stdin:
    m = trace_re.match(line)
    if m:
        pc = int(m.group(1), 16) & ~1
        n = tb_len.get(pc)
        if n is None:
            n = tb_len.get(pc | 1, 0)
        executed += n
        blocks += 1
        if tick_pc is not None:
            if pc == tick_pc:
                ticks += 1
                max_gap = max(max_gap, since_tick)
                since_tick = 0
            else:
                since_tick += n
        continue
    if line.startswith("IN:"):
        cur = None
        continue
    k = objd_count(line)
    if k is not None and cur is not None:
        tb_len[cur] += k - 1          # the address line already counted one
        continue
    m = insn_re.match(line)
    if m:
        a = int(m.group(1), 16)
        if cur is None:
            cur = a
            tb_len[cur] = 0
        tb_len[cur] += 1
print(f"blocks executed {blocks}, distinct {len(tb_len)}, instructions {executed}")
if tick_pc is not None:
    max_gap = max(max_gap, since_tick)
    print(f"ticks {ticks}, longest stretch without one {max_gap} instructions")
