#!/usr/bin/env python3
"""Build the recovery bytecode: an OTA path that does not ask the rabbit to
verify a signature it cannot verify.

Why this exists. `verifySig` has never once returned 1 on any rabbit: the
firmware's wrapper passed one buffer as both the input and the output of
crypto_sign_open, and TweetNaCl drops the public key on the half of the
signature it still has to read (nabaztag `b8f6dd1`). So a rabbit already
running a signature-gated build — `full`, `lean`, any `lean-*` — refuses every
signed image, including the one that fixes the refusal. Over the air it is
walled in, and the config-page path uses the same verifier.

The runtime bytecode is not: the warren serves it fresh at every boot, with no
flash and no brick risk. So the way out is here. This step makes the OTA path
flash an image the **warren** has verified — `simsig.verify_file` runs before
any `FW` command is sent — instead of one the rabbit has verified. The check
still runs and still reports, as `e=000000E3`, so the warren learns which
rabbits are in this state; it just no longer refuses.

That is a real reduction in defence: the rabbit's own check was the one thing
that did not depend on the transport, and the transport is plain HTTP, so the
server being sound is no longer the whole question — the DNS and the network
path of every owner join it. Which is why it ends by itself. A fixed firmware
reports `&s=1` in its boot URL, and the warren serves that rabbit the normal,
gated bytecode again, per rabbit, with nobody having to remember (mint.py).

Idempotent. Usage: patch_ota_recovery.py <dir containing fwota.mtl>
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ota-recovery */"

OLD = '''	else if (verifySig firm sig)==0 then (ota_fail_report 0xF3; 0)
	else
	let flashFirmware firm 0x13fb6754 0x0407FE58 -> bad in
	(
		ota_fail_report if bad==nil then 0xF4 else bad;
		0
	);;'''

NEW = '''	else
	(
		// ota-recovery: the check runs and reports, and does not refuse.
		// This rabbit cannot verify a signature at all, so refusing on its
		// verdict means refusing everything for ever. The warren verified
		// this image before sending it.
		if (verifySig firm sig)==0 then ota_fail_report 0xE3 else nil;
		let flashFirmware firm 0x13fb6754 0x0407FE58 -> bad in
		(
			ota_fail_report if bad==nil then 0xF4 else bad;
			0
		)
	);;'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    p = Path(sys.argv[1]) / "fwota.mtl"
    if not p.is_file():
        print(f"no fwota.mtl in {sys.argv[1]}", file=sys.stderr)
        return 1
    t = p.read_text(encoding="utf-8")
    if MARKER in t:
        print(f"[skip] {p}: already the recovery variant")
        return 0
    if t.count(OLD) != 1:
        print(f"FAIL {p}: the gated branch is not the shape this patch "
              f"expects (found {t.count(OLD)}). Refusing to guess: a bytecode "
              f"that flashes unsigned images by accident is worse than one "
              f"that does it on purpose.", file=sys.stderr)
        return 1
    p.write_text(t.replace(OLD, NEW, 1) + f"\n{MARKER}\n", encoding="utf-8")
    print(f"[ok]   {p}: OTA leans on the warren's signature check, and says so")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
