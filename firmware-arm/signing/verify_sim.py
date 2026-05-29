#!/usr/bin/env python3
"""Verify a signed .sim against the embedded public key.

Reads the pubkey from firmware-arm/keys/signing_pubkey.h (the same one
that gets compiled into the firmware), so a successful verify here means
the firmware will also accept it.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519

_MAGIC = b"-violet-"
_SIG_MAGIC = b"-sig-"


def _read_pubkey_from_header(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(r"0x([0-9a-fA-F]{2})", text)
    if len(matches) < 32:
        raise ValueError(f"expected 32 bytes in pubkey header, found {len(matches)}")
    return bytes(int(m, 16) for m in matches[:32])


def _split_signed(blob: bytes) -> tuple[bytes, bytes]:
    """Return (payload, signature). Raises if malformed/unsigned."""
    if not blob.startswith(_MAGIC):
        raise ValueError("not a .sim file")
    # signed shape: ... -violet- + -sig-<128 hex>-sig-
    if not blob.endswith(_SIG_MAGIC):
        raise ValueError("not signed (missing trailing -sig- marker)")
    sig_open = blob.rfind(_SIG_MAGIC, 0, len(blob) - len(_SIG_MAGIC))
    if sig_open < 0:
        raise ValueError("malformed signed .sim (single -sig- marker)")
    sig_hex = blob[sig_open + len(_SIG_MAGIC) : -len(_SIG_MAGIC)]
    if len(sig_hex) != 128:
        raise ValueError(f"signature is {len(sig_hex)} hex chars, expected 128")
    sig = bytes.fromhex(sig_hex.decode("ascii"))
    inner = blob[:sig_open]
    if not inner.endswith(_MAGIC):
        raise ValueError("inner section does not end with -violet-")
    body_hex = inner[len(_MAGIC) + 8 : -len(_MAGIC)]
    payload = bytes.fromhex(body_hex.decode("ascii"))
    return payload, sig


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("sim", help="Path to the signed .sim")
    p.add_argument(
        "--pub-header",
        default=str(Path(__file__).resolve().parent.parent / "keys" / "signing_pubkey.h"),
    )
    args = p.parse_args()

    pub = _read_pubkey_from_header(Path(args.pub_header))
    try:
        payload, sig = _split_signed(Path(args.sim).read_bytes())
    except ValueError as exc:
        print(f"verify({Path(args.sim).name}) = False  ({exc})")
        return 1
    ok = ed25519.verify(pub, payload, sig)
    print(f"verify({Path(args.sim).name}, key={Path(args.pub_header).name}) = {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
