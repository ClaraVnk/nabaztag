#!/usr/bin/env python3
"""The emulator's MOD_STEPS for a firmware mode: the mode's steps that the
emulator builds (run.sh's list), in the mode's order.

    python3 tools/emu-steps.py naboot-1.0.0.3
    podman build -f firmware-arm/emulator/Containerfile \\
        --build-arg MOD_STEPS=$(python3 firmware-arm/emulator/tools/emu-steps.py naboot-1.0.0.3) -t emu-1003 .

The emulator cannot take every step of a release (size work, linker and page
steps only make sense for an image); the network, crypto and VM steps it
does take are the ones that decide how a rabbit of that version behaves.
2026-11-13: done by hand for 1.0.0.3 to prove its update to 1.2.0.0.
"""
import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
mods = (here / "../../apply-mods.py").read_text()
run = (here / "../run.sh").read_text()
emu = re.search(r"^STEPS=(\S+)", run, re.M).group(1).split(",")
# The two flash levers (2026-11-19): not in run.sh's default list, but the
# emulator's Makefile reads them off the patched tree (tls_chacha_only
# compiles AES-GCM out, opt_size_crypto puts the crypto objects at -Os), so a
# mode that carries them is emulated with them. Without this the emulator of
# naboot-1.2.1.0-lean-tel bound its session on a suite the image no longer
# offers.
emu += ["opt_size_crypto", "tls_chacha_only"]
# wifi_diag (2026-11-19): its opcode must exist in the emulator's VM, or the
# image's bootloader, which calls wifiDiag, stops where it writes its line.
emu += ["wifi_diag"]
# Every step that adds a core opcode, in the mode's own order: opcodes are
# numbered as they come (add_core_opcode), so one missing before wifi_diag
# gave wifiDiag another number in the emulator than in the image -- the
# image's bootloader then called the wrong opcode (2026-11-19). tls_certs
# adds them; boot_https goes with it (RUNBOOK §4 already added both by hand).
emu += ["tls_certs", "boot_https"]
# usb_watch (2026-11-20): vm_main.c stands in for the USB host controller.
emu += ["usb_watch"]
mode = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
m = re.search(r'"%s":\s*"([^"]+)"' % re.escape(mode), mods)
if not m:
    sys.exit(f"no mode {mode!r} in apply-mods.py")
print(",".join(s for s in m.group(1).split(",") if s in emu))
