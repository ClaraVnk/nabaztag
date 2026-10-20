#!/usr/bin/env python3
"""Reconnect instead of rebooting when the server's socket closes.

Stock Violet, in main.mtl:

    fun XmppGateStreamClosed xSession Mode =
        // la connection a déconnecté. On pourrait essayer de gentiment
        // relancer la connection, mais le plus efficace est de simplement
        // rebooter le bunny.
        Xw "gate stream closed, rebooting";
        reboot 0x0407FE58 0x13fb6754
    ;;

That was a reasonable call in 2006, when the server never went away. It is the
wrong one for a warren that redeploys: measured on real hardware 2026-10-20,
the rabbit *does* reboot (13 s after the drop) and *does* re-fetch its 103 KB
of bytecode — then dies before it even asks for locate.jsp, and sits there
orange until someone unplugs it. A soft `reboot` does not bring the 2006
rt2501 USB radio back; only a power cycle does.

So take the branch the Violet authors wrote and then declined:
`XmppSessionIsDisconnected`, which stops what the rabbit is doing, resets it to
idle, lights the base violet, waits 5 s plus a random 0-30 s (so a whole warren
does not stampede back at once) and calls `XmppGateReconnect`. The network
stack is never torn down, so there is nothing to bring back.

That path is not new code: Violet already uses it for the BOSH transport, for a
server-closed stream, and for stanza errors. This patch only routes the TCP
case to it as well.

Idempotent: re-running on an already-patched tree is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* reconnect-not-reboot */"

OLD = """	Xw "gate stream closed, rebooting";
	reboot 0x0407FE58 0x13fb6754
;;"""

NEW = """	/* reconnect-not-reboot */
	// Upstream rebooted here. On hardware a soft reboot re-fetches the bytecode
	// and then dies before locate.jsp: the radio never comes back without a
	// power cycle. Reconnect gently instead (base goes violet, 5 s + random
	// 0-30 s, then XmppGateReconnect). See firmware/patch_reconnect.py.
	Xw "gate stream closed, reconnecting";
	XmppSessionIsDisconnected xSession
;;"""


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")      # main.mtl carries 8-bit comments
        encoding = "latin-1"

    if MARKER in text:
        print(f"[skip] {path}: already reconnects instead of rebooting")
        return False
    if OLD not in text:
        sys.exit(f"FAIL {path}: the stock XmppGateStreamClosed body was not found — "
                 "did upstream change, or did another patch get there first?")
    if "fun XmppSessionIsDisconnected" not in text:
        sys.exit(f"FAIL {path}: XmppSessionIsDisconnected is missing; the gentle "
                 "reconnect path this patch relies on does not exist here.")

    assert NEW.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.replace(OLD, NEW, 1).encode(encoding))
    print(f"[ok]   {path}: stream-closed now reconnects (was: reboot)")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
