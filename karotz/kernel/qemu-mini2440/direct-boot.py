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

# The initrd went to RAM + 8 MiB, where a 6.1 zImage's decompressor copies
# itself (it ran at 0x3070xxxx): the cramfs was overwritten before the kernel
# read it ("RAMDISK: Couldn't find valid RAM disk image", 2026-11-22).
# 16 MiB is clear of it on the 64 MiB board.
b = p.parent / "arm_boot.c"
t = b.read_text()
if "0x00800000" in t:
    b.write_text(t.replace("#define INITRD_LOAD_ADDR 0x00800000", "#define INITRD_LOAD_ADDR 0x01000000"))

# The Karotz's own NAND: 256 MiB of 512-byte pages, Samsung id 71, which
# QEMU's nand.c knows but the board never picks. MINI2440_NAND_CID=0x71 and
# a -mtdblock file of 524288 pages of 528 bytes (data and OOB, as nanddump
# -o writes them) give the kernel Gengar's flash as it is (2026-11-22, the
# measure key's mtd5-oob.bin and mtd6-oob.bin: step 3 on a real /usr).
n_old = "    mini->nand = nand_init(NAND_MFR_SAMSUNG, nand_cid);\n"
s = p.read_text()
if "MINI2440_NAND_CID" not in s:
    assert s.count(n_old) == 1, "nand_init not found"
    s = s.replace(n_old, '    if (getenv("MINI2440_NAND_CID"))\n'
                         '        nand_cid = strtol(getenv("MINI2440_NAND_CID"), NULL, 0);\n' + n_old, 1)
    p.write_text(s)

# The clocks a Karotz's bootloader leaves: core 405 MHz, memory (HCLK)
# 135 MHz, peripherals 67.5 MHz (Gengar's 2.6.32 boot log, 2026-11-22):
# MPLLCON 0x7f021 from the 12 MHz crystal, CLKDIVN 7. QEMU's reset values
# gave HCLK 240 MHz, at which 6.1's NAND driver found no timing for the
# board's 20 ns ("cannot get suitable timings").
s = p.read_text()
w_old = "stl_phys(0x53000000, 0);"
if "0x4c000014" not in s:
    assert s.count(w_old) == 2, "the watchdog writes"
    s = s.replace(w_old, w_old + " stl_phys(0x4c000004, 0x7f021); stl_phys(0x4c000014, 7);")
    p.write_text(s)

# A 2 KiB-page chip above 128 MiB (Gengar's Hynix 0xDA, 131072 pages) takes a
# third row address byte, five in all. nand.c ran the read at the fourth and
# kept the address in 32 bits: every page past 65535 read as the page 65536
# below it, and Gengar's /usr mounted with half its directories (2026-11-23,
# nand-compare.sh: mtd7, at page 130688, read back wrong). Wait for the fifth
# byte when the chip is that big, and hold the address in 64 bits.
nd = p.parent / "nand.c"
t = nd.read_text()
if "row3" not in t:
    pairs = [
        ("    uint32_t cmd, addr;\n", "    uint32_t cmd;\n    uint64_t addr;  /* row3: five address bytes */\n"),
        ("void (*blk_load)(NANDFlashState *s, uint32_t addr, int offset);",
         "void (*blk_load)(NANDFlashState *s, uint64_t addr, int offset);"),
        ("        s->addr |= value << (s->addrlen * 8);\n",
         "        s->addr |= (uint64_t) value << (s->addrlen * 8);\n"),
        ("               s->addrlen == 4 && (\n",
         "               s->addrlen == (s->pages > 65536 ? 5 : 4) && (\n"),
        ("                uint32_t addr, int offset)", "                uint64_t addr, int offset)"),
    ]
    for old, new in pairs:
        assert t.count(old) == 1, old
        t = t.replace(old, new)
    nd.write_text(t)

# The status register never said ready: bit 6 (NAND_STATUS_READY to Linux,
# named BUSY here) stayed 0, and 6.1's nand_wait warned at every operation,
# a stack trace on the console each time; Gengar's /usr took longer to mount
# than the five minutes given (2026-11-23, real-boot.sh). Nothing here is
# ever busy, so the status always says ready.
nd = p.parent / "nand.c"
t = nd.read_text()
st_old = "        s->io[0] = s->status;\n"
if "always ready" not in t:
    assert t.count(st_old) == 1, "READSTATUS"
    t = t.replace(st_old, "        s->io[0] = s->status | NAND_IOSTATUS_BUSY;  /* bit 6 = ready: always ready */\n")
    nd.write_text(t)

# The ARM920T's pipeline, for kexec only (QEMU_ARM920_PIPELINE=1): Linux
# 2.6.32's cpu_arm920_reset turns the MMU off and its next instruction,
# `mov pc, r0`, is already fetched on the real CPU; QEMU fetches it again at
# its virtual address, 0xc0..., with no MMU, and stops ("Trying to execute
# code outside RAM", karotz/kexec/kexec-proof.sh, 2026-11-23). With the
# variable set, a kernel address read with the MMU off is the RAM it maps
# (0xc0000000 -> 0x30000000), as that prefetch makes it on the board.
hp = p.parent.parent / "target-arm" / "helper.c"
h = hp.read_text()
h_old = """    if ((env->cp15.c1_sys & 1) == 0) {
        /* MMU/MPU disabled.  */
        *phys_ptr = address;
"""
if "QEMU_ARM920_PIPELINE" not in h:
    assert h.count(h_old) == 1, "get_phys_addr's MMU-off branch"
    h = h.replace(h_old, h_old + """        {
            static int pipeline = -1;
            if (pipeline < 0)
                pipeline = getenv("QEMU_ARM920_PIPELINE") != NULL;
            if (pipeline && address >= 0xc0000000 && address < 0xc4000000)
                *phys_ptr = address - 0xc0000000 + 0x30000000;
        }
""")
    if "#include <stdlib.h>" not in h:
        h = "#include <stdlib.h>\n" + h
    hp.write_text(h)

