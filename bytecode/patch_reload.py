#!/usr/bin/env python3
"""`RB`: the warren restarts a rabbit, which then loads the runtime served now.

A rabbit takes a new runtime only at its next boot, and until now that boot
meant somebody unplugging it (Clara, 2026-11-23: "Une idée pour servir un
nouveau bytecode sans débrancher rebrancher ?"). The command language gets
one more key, beside SV and FW: `RB` calls Violet's own
`reboot 0x0407FE58 0x13fb6754`, the opcode that ends in sysReboot ->
reset_uc, the reset a rabbit already comes back from after an update
(flash_uc) and after three presses of the head (configBoot).

patch_reconnect.py took that reboot out of the dropped-socket path because
on the stock firmware of 2026-10-20 the radio stayed asleep after it; on
Naboot the same reset_uc ends every update, and the rabbit comes back on its
own. tools/xmpp-tls-proof.sh with scenarios/vm/xmpp-reload.txt proves the
restart and the new session in the emulator.

A runtime without this patch ignores the key, as it ignores any it does not
know. Applied after patch_main.py (the FW command); build-bytecode.sh does.
Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* reload-runtime */"

FW_OLD = '\t\telse if !strcmp key "FW" then '
RB_LINE = ('\t\telse if !strcmp key "RB" then (reboot 0x0407FE58 0x13fb6754;0)'
           '\t/* reload-runtime */\n')


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        s, enc = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        s, enc = raw.decode("latin-1"), "latin-1"     # main.mtl carries 8-bit comments
    if MARKER in s:
        return False
    if s.count(FW_OLD) != 1:
        sys.exit(f"FAIL {path}: the FW command is not there once -- run patch_main.py first")
    s = s.replace(FW_OLD, RB_LINE + FW_OLD, 1)
    path.write_bytes(s.encode(enc))
    return True


if __name__ == "__main__":
    p = Path(sys.argv[1])
    print("patched" if patch(p) else "already patched", p)
