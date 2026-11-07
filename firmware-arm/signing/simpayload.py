#!/usr/bin/env python3
"""The bytes a .sim signature covers — one definition, so nothing can disagree.

This module exists because of 2026-11-07. The signature was computed over the
payload **as it sits in the .sim**, which is obfuscated, while the rabbit calls
`uncrypt` before it verifies and therefore presents the plain image. Both
sides were self-consistent and the two never agreed, so `verifySig` returned 0
every single time — on the OTA path and on the config page's `httpflash` alike.
Neither path says anything when it refuses, so the rabbit downloaded the image,
declined it in silence, and kept a green belly. Five months of flashes wrote
nothing at all, and a JTAG dump was the only thing that ever noticed.

Measured the same evening, on hardware: `nab_flash_calls` stayed at 0 across a
whole OTA, so `sysFlash` was never even reached, and offline

    verify(pub, payload,            sig) is True     # what the signer signed
    verify(pub, deobfuscate(payload), sig) is False  # what the rabbit checks

So the signature now covers `deobfuscate(payload)`, which is byte-for-byte the
`Nab.bin` that gets written to flash. That is also the only sequence worth
signing: the rabbit then verifies what it is about to burn, not an intermediate
encoding of it.

Anything that signs or verifies a .sim imports from here. The Terrier cannot
import across repositories, so `simsig.py` carries a copy — keep the two
identical, and keep this docstring with it.
"""

from __future__ import annotations

MAGIC = b"-violet-"
SIG_MAGIC = b"-sig-"

# `uncrypt bin 0 nil 0x47 47` in the bytecode: key 0x47, alpha 47 decimal.
# Watch the bases -- the two look alike and are not the same number.
UNCRYPT_KEY = 0x47
UNCRYPT_ALPHA = 47


def deobfuscate(buf: bytes, key: int = UNCRYPT_KEY,
                alpha: int = UNCRYPT_ALPHA) -> bytes:
    """`decode8` from nabgcc `src/vm/vlog.c:518`, byte for byte.

        while(len--) {
          uint8_t v = ((*src) - alpha) * key;
          *(src++) = v;
          key = v + v + 1;
        }

    A keystream that depends on the plaintext it just produced, so it has to
    run forwards over the whole buffer. Everything is mod 256, including the
    subtraction, which in C wraps on the uint8_t.
    """
    out = bytearray(len(buf))
    for i, c in enumerate(buf):
        v = (((c - alpha) & 0xFF) * key) & 0xFF
        out[i] = v
        key = (v + v + 1) & 0xFF
    return bytes(out)


def parse_sim(blob: bytes) -> bytes:
    """The obfuscated payload of an unsigned .sim. Raises ValueError if the
    file is not one."""
    if not blob.startswith(MAGIC):
        raise ValueError("not a .sim file (missing opening -violet- marker)")
    if not blob.endswith(MAGIC):
        raise ValueError("not a stock .sim (must end with -violet-); "
                         "is it already signed?")
    size_hex = blob[len(MAGIC):len(MAGIC) + 8]
    try:
        twice_size = int(size_hex, 16)
    except ValueError as exc:
        raise ValueError("malformed size header") from exc
    body_hex = blob[len(MAGIC) + 8:-len(MAGIC)]
    if len(body_hex) != twice_size:
        raise ValueError(f"size header ({twice_size} hex chars) disagrees with "
                         f"body ({len(body_hex)} hex chars)")
    try:
        return bytes.fromhex(body_hex.decode("ascii"))
    except ValueError as exc:
        raise ValueError("body is not hex") from exc


def split_signed(blob: bytes) -> tuple[bytes, bytes]:
    """(obfuscated payload, 64-byte signature) of a signed .sim."""
    j = blob.find(SIG_MAGIC)
    if j < 0:
        raise ValueError("no -sig- trailer; this .sim is not signed")
    j += len(SIG_MAGIC)
    k = blob.find(SIG_MAGIC, j)
    if k < 0:
        raise ValueError("unterminated -sig- trailer")
    if k - j != 128:
        raise ValueError(f"signature is {k - j} hex chars, expected 128")
    try:
        sig = bytes.fromhex(blob[j:k].decode("ascii"))
    except ValueError as exc:
        raise ValueError("signature is not hex") from exc
    return parse_sim(blob[:j - len(SIG_MAGIC)]), sig


def signed_bytes(blob: bytes) -> bytes:
    """What a signature over this unsigned .sim must cover: the plain image."""
    return deobfuscate(parse_sim(blob))
