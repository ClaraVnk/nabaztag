#!/usr/bin/env python3
"""Make QEMU mini2440 boot a kernel itself, as a Karotz's locked bootloader
does: ATAGs, a machine number, no U-Boot (2026-11-22).

The machine only ever copied -kernel to RAM and jumped to a U-Boot it
expected in its NAND (an empty one: "Trying to execute code outside RAM or
ROM at 0x34000000"). With -kernel and no u-boot.bin beside it, this hands the
kernel to QEMU's own ARM loader instead, with the machine number from
MINI2440_MACH (default 1999, the mini2440; the Karotz's board is 1912).
"""
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else "hw/mini2440.c")
s = p.read_text()
if "MINI2440_MACH" in s:
    sys.exit(0)
if '#include "arm-misc.h"' not in s:
    s = s.replace('#include "', '#include "arm-misc.h"\n#include "', 1)
old = "    mini2440_reset(mini);\n}\n"
assert s.count(old) == 1, "mini2440_init's end not found"
new = """    mini2440_reset(mini);

    if (kernel_filename && access("mini2440/u-boot.bin", R_OK) && access("u-boot.bin", R_OK)) {
        static struct arm_boot_info binfo;
        const char *mach = getenv("MINI2440_MACH");
        binfo.ram_size = mini->ram;
        binfo.kernel_filename = kernel_filename;
        binfo.kernel_cmdline = kernel_cmdline;
        binfo.initrd_filename = initrd_filename;
        binfo.loader_start = S3C_RAM_BASE;
        binfo.board_id = mach ? atoi(mach) : 1999;
        mini->kernel = NULL;
        mini2440_printf("direct boot, machine %d\\n", binfo.board_id);
        /* boot_info set first: arm_load_kernel then registers no reset of
           its own, whose cpu_reset would send the CPU to 0 (this QEMU's
           loader never sets the PC for Linux; the board does, below) */
        mini->cpu->env->boot_info = &binfo;
        arm_load_kernel(mini->cpu->env, &binfo);
        mini->cpu->env->regs[15] = binfo.loader_start;
        /* and at power-up too: the board reset above ran before
           mini2440_direct was set */
        stl_phys(0x53000000, 0);
    }
}
"""
s = s.replace(old, new, 1)
# The machine's own reset ran after QEMU's loader and sent the CPU back to
# the (empty) U-Boot: in direct boot, it leaves the CPU alone.
r_old = "static void mini2440_reset(void *opaque)\n{\n"
assert s.count(r_old) == 1, "mini2440_reset not found"
s = s.replace(r_old, "static int mini2440_direct;\n\n" + r_old
              + "    if (mini2440_direct) {\n        ((struct mini2440_board_s *) opaque)->cpu->env->regs[15] = S3C_RAM_BASE;\n        /* the watchdog is on at power-up (WTCON 0x8021) and a bootloader\n           turns it off before Linux: without one, it reset the board\n           in the middle of the decompression (2026-11-22) */\n        stl_phys(0x53000000, 0);\n        return;\n    }\n", 1)
s = s.replace("        mini->kernel = NULL;\n",
              "        mini->kernel = NULL;\n        mini2440_direct = 1;\n", 1)
p.write_text(s)
