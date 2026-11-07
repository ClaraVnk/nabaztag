#!/usr/bin/env python3
"""Sign a .sim firmware image with the Ed25519 private key.

Input format (produced by nabgcc utils/mkfirmware.php):
    '-violet-' + size_hex(8 ASCII) + payload_hex + '-violet-'

Output format (signed):
    <input> + '-sig-' + signature_hex(128 ASCII) + '-sig-'

The signature is computed over the PLAIN image -- the payload after
`uncrypt`, byte for byte the Nab.bin that gets written to flash. See
simpayload.py: it used to cover the obfuscated payload instead, which the
rabbit can never reproduce because it de-obfuscates before it verifies, and
that mismatch is why no signed .sim had ever installed. The legacy bootloader
stops at the closing '-violet-' so signed .sim remain installable by the
unmodified bootloader — only the NEW bootloader checks the trailer.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519
import simpayload

_MAGIC = b"-violet-"
_SIG_MAGIC = b"-sig-"


def _parse_sim(blob: bytes) -> tuple[bytes, bytes]:
    """Return (header_through_close, payload_bytes). Raises ValueError if malformed."""
    if not blob.startswith(_MAGIC):
        raise ValueError("not a .sim file (missing opening -violet- marker)")
    if not blob.endswith(_MAGIC):
        raise ValueError("not a stock .sim (must end with -violet-); is it already signed?")
    size_hex = blob[len(_MAGIC):len(_MAGIC) + 8]
    try:
        twice_size = int(size_hex, 16)
    except ValueError as exc:
        raise ValueError("malformed size header") from exc
    body_hex = blob[len(_MAGIC) + 8 : -len(_MAGIC)]
    if len(body_hex) != twice_size:
        raise ValueError(
            f"size header ({twice_size} hex chars) disagrees with body ({len(body_hex)} hex chars)"
        )
    try:
        payload = bytes.fromhex(body_hex.decode("ascii"))
    except ValueError as exc:
        raise ValueError("body is not hex") from exc
    return blob, payload


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("sim", help="Path to the unsigned .sim")
    p.add_argument(
        "-o", "--out", default=None,
        help="Output path. Defaults to <input>.signed",
    )
    p.add_argument(
        "--priv", default=str(Path.home() / ".nabaztag" / "signing_key.bin"),
        help="Private seed path. Default: ~/.nabaztag/signing_key.bin",
    )
    args = p.parse_args()

    sim_path = Path(args.sim)
    priv_path = Path(args.priv)
    out_path = Path(args.out) if args.out else sim_path.with_suffix(sim_path.suffix + ".signed")

    if not priv_path.is_file():
        print(f"error: private seed not found at {priv_path}. Run gen_key.py first.", file=sys.stderr)
        return 2

    priv = priv_path.read_bytes()
    if len(priv) != 32:
        print(f"error: private seed must be 32 bytes, got {len(priv)}", file=sys.stderr)
        return 2

    blob, payload = _parse_sim(sim_path.read_bytes())
    signed_over = simpayload.deobfuscate(payload)
    sig = ed25519.sign(priv, signed_over)
    signed = blob + _SIG_MAGIC + sig.hex().encode("ascii") + _SIG_MAGIC

    out_path.write_bytes(signed)
    print(f"signed {sim_path.name} → {out_path}")
    print(f"  payload bytes : {len(payload)} (signature covers the plain image)")
    print(f"  signature hex : {sig.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
