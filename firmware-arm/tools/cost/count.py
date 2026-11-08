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
