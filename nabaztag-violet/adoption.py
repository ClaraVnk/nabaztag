"""The per-rabbit key, on this add-on: exchange, owner's word, HMAC, TLS.

What a Naboot rabbit expects of its warren since 1.0.0.0, written from the
protocol in firmware-arm/docs/ADOPTION-KEY.md, which Le Terrier implements
too; the vectors in tests/ are the firmware's (crypto/nab_kx.c, nab_tls.c).

  kx.jsp     the rabbit's X25519 public key in, ours out. Both sides derive
             the key and five check colours; the rabbit lights them on its
             belly, the owner compares them with the add-on's page and says
             yes or no. A party in the middle of the exchange holds another
             secret with each side: the colours differ unless it guessed
             1 in 32 768 on its only try.
  bc.jsp     for a rabbit whose owner said yes, the TLS runtime with TLS
             required (one byte, xmpp-tls:0 -> 1) and an HMAC under the key
             over the whole bytecode; the bootloader refuses it otherwise.
  :5223      TLS 1.3, external PSK keyed from the adoption key, identity the
             MAC. Inside: the XMPP session, or one HTTP request for bc.jsp or
             locate.jsp (the bootloader of 1.0.0.2 and later asks there).

Stdlib only, like the rest of the add-on: two scalar multiplications per
adoption do not justify a native dependency. The TLS side needs Python 3.13
(SSLContext.set_psk_server_callback).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import ssl
import tempfile
import threading
import time

log = logging.getLogger("nabaztag")

# --------------------------------------------------------------------------- #
# X25519 + HKDF, mirrored from the rabbit's crypto/nab_kx.c
# --------------------------------------------------------------------------- #
SALT = b"nabaztag adoption v1"
LEDS = 5
_P = 2**255 - 19
_A24 = 121665


def _decode_u(u: bytes) -> int:
    b = bytearray(u)
    b[31] &= 127
    return int.from_bytes(b, "little")


def _decode_scalar(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(k: bytes, u: bytes) -> bytes:
    """RFC 7748 section 5, the Montgomery ladder."""
    if len(k) != 32 or len(u) != 32:
        raise ValueError("X25519 takes 32-byte scalars and points")
    k_int, x1 = _decode_scalar(k), _decode_u(u)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        k_t = (k_int >> t) & 1
        swap ^= k_t
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = k_t
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(priv: bytes) -> bytes:
    return x25519(priv, (9).to_bytes(32, "little"))


def keypair() -> tuple[bytes, bytes]:
    priv = secrets.token_bytes(32)
    return priv, public_key(priv)


def hkdf(length: int, salt: bytes, ikm: bytes, info: bytes) -> bytes:
    """RFC 5869 HKDF-SHA-256."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 0
    while len(out) < length:
        i += 1
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
    return out[:length]


class LowOrderPoint(ValueError):
    """The peer's key gives an all-zero secret (RFC 7748 6.1)."""


def derive(priv: bytes, peer_pub: bytes, rabbit_pub: bytes, warren_pub: bytes
           ) -> tuple[bytes, list[int]]:
    """The 32-byte key and the five check colours (0-7 each)."""
    shared = x25519(priv, peer_pub)
    if not any(shared):
        raise LowOrderPoint("the peer's public key is a low-order point")
    okm = hkdf(36, SALT, shared, b"nabkx" + rabbit_pub + warren_pub)
    bits = (okm[32] << 8) | okm[33]
    return okm[:32], [(bits >> (3 * (LEDS - 1 - i))) & 7 for i in range(LEDS)]


def tls_psk(key: bytes) -> bytes:
    """The TLS PSK: a child of the adoption key, as nab_tls_psk derives it.
    The HMAC uses the key itself, TLS this child, so neither use can be
    turned against the other."""
    if len(key) != 32:
        raise ValueError("the adoption key is 32 bytes")
    return hkdf(32, b"nabaztag tls v1", key, b"nabtls psk")


def fingerprint(key: bytes) -> str:
    """Which key the owner's page was showing the colours of. A hash under a
    label of its own, never the key: a "yes" applies to that key only."""
    return hashlib.sha256(b"nabaztag-violet kx form v1" + key).hexdigest()[:32]


# The eight colours of the bootloader (boot-mods/adoption.mtl, kx_color), in
# its order. LED i shows colour i: 0 bottom, 1 left, 2 middle, 3 right, 4 top.
COLOR_RGB = ["#ff0000", "#00ff00", "#0000ff", "#ffff00",
             "#00ffff", "#ff00ff", "#ffffff", "#ff8000"]
COLOR_NAMES = {
    "en": ["red", "green", "blue", "yellow", "cyan", "magenta", "white", "orange"],
    "fr": ["rouge", "vert", "bleu", "jaune", "cyan", "magenta", "blanc", "orange"],
}
LED_NAMES = {
    "en": ["bottom", "left", "middle", "right", "top"],
    "fr": ["bas", "gauche", "milieu", "droite", "haut"],
}


def describe_colors(colors: list[int], lang: str = "en") -> str:
    names, leds = COLOR_NAMES.get(lang, COLOR_NAMES["en"]), LED_NAMES.get(lang, LED_NAMES["en"])
    order = (4, 1, 2, 3, 0)          # top first, as one reads the belly
    return ", ".join(f"{leds[i]} {names[colors[i]]}" for i in order)


# --------------------------------------------------------------------------- #
# The keys, in the add-on's persistent /data
# --------------------------------------------------------------------------- #
class KeyStore:
    """mac -> {key, colors, state, created, decided}, one JSON file.

    States: pending (agreed, waiting for the owner), confirmed, rejected.
    First come, first kept: a second exchange for a MAC that has a pending
    or confirmed key is somebody else's (a rabbit keeps its key from its
    first exchange on), so it is refused; a rejected key is replaced, which
    is also how a rabbit whose configuration was wiped starts over -- after
    the owner rejects or forgets the old key here, deliberately."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for mac, r in (raw or {}).items():
                if len(bytes.fromhex(r["key"])) == 32 and r.get("state") in (
                        "pending", "confirmed", "rejected"):
                    self._rows[mac.lower()] = r
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A key store that cannot be read must not be overwritten by an
            # empty one: every confirmed rabbit would be locked out.
            raise RuntimeError(f"cannot read the adoption keys in {path}: {exc}") from exc

    def _save_locked(self) -> None:
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".keys-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._rows, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def get(self, mac: str) -> dict | None:
        with self._lock:
            r = self._rows.get((mac or "").lower())
            if r is None:
                return None
            return {"key": bytes.fromhex(r["key"]), "colors": list(r["colors"]),
                    "state": r["state"], "created": r.get("created"),
                    "decided": r.get("decided"),
                    "served": frozenset(r.get("served") or ())}

    def note_served(self, mac: str, policies) -> None:
        """The session policies of the bytecode bc.jsp last served this
        rabbit: what its session may be sent until its next boot. Kept on
        disk, so an add-on restart does not send a rabbit what it cannot
        read, or hold back what it can."""
        mac = mac.lower()
        served = sorted(policies)
        with self._lock:
            r = self._rows.get(mac)
            if r is None or (r.get("served") or []) == served:
                return
            r["served"] = served
            self._save_locked()

    def offer(self, mac: str, key: bytes, colors: list[int]) -> bool:
        mac = mac.lower()
        with self._lock:
            r = self._rows.get(mac)
            if r is not None and r["state"] in ("pending", "confirmed"):
                return False
            self._rows[mac] = {"key": key.hex(), "colors": list(colors),
                               "state": "pending", "created": time.time(),
                               "decided": None}
            self._save_locked()
        return True

    def decide(self, mac: str, confirmed: bool, fp: str | None = None) -> bool:
        """The owner's answer. Only a pending key, and with `fp` only the key
        whose colours the page showed."""
        mac = mac.lower()
        with self._lock:
            r = self._rows.get(mac)
            if r is None or r["state"] != "pending":
                return False
            if fp is not None and not hmac.compare_digest(
                    fp, fingerprint(bytes.fromhex(r["key"]))):
                return False
            r["state"] = "confirmed" if confirmed else "rejected"
            r["decided"] = time.time()
            self._save_locked()
        return True

    def forget(self, mac: str) -> bool:
        with self._lock:
            if self._rows.pop(mac.lower(), None) is None:
                return False
            self._save_locked()
        return True

    def listing(self) -> list[dict]:
        with self._lock:
            out = []
            for mac, r in sorted(self._rows.items()):
                key = bytes.fromhex(r["key"])
                out.append({"mac": mac, "state": r["state"], "colors": list(r["colors"]),
                            "rgb": [COLOR_RGB[c] for c in r["colors"]],
                            "fingerprint": fingerprint(key),
                            "created": r.get("created"), "decided": r.get("decided")})
            return out


# --------------------------------------------------------------------------- #
# The bytecode a rabbit is sent
# --------------------------------------------------------------------------- #
TLS_POLICY_OPTIONAL = b"xmpp-tls:0"
TLS_POLICY_REQUIRED = b"xmpp-tls:1"


def require_tls(blob: bytes) -> bytes:
    """The bytecode with TLS required, or as it is when it carries no policy
    (a runtime from before bytecode/patch_tls.py) or carries it more than
    once (build-bytecode.sh refuses to produce one; never guess)."""
    if blob.count(TLS_POLICY_OPTIONAL) != 1:
        return blob
    return blob.replace(TLS_POLICY_OPTIONAL, TLS_POLICY_REQUIRED)


def sign_bytecode(blob: bytes, key: bytes) -> bytes:
    """Append "Hmac" + 64 hex digits: HMAC-SHA-256 under the adoption key of
    the bytecode between its amber header and "Mind". A confirmed rabbit's
    bootloader refuses a bytecode without it."""
    if not blob.startswith(b"amber"):
        log.warning("bytecode without its amber header -- not signing it")
        return blob
    n = int(blob[5:13], 16)
    tag = hmac.new(key, blob[13:13 + n], hashlib.sha256).hexdigest()
    return blob + b"Hmac" + tag.encode()


# What else the runtime moves inside its TLS session once told to, one
# literal each, compiled as 0 (nabaztag bytecode/patch_*_in_session.py):
#   rfid  a tag on the reader as a stanza, not GET rfid.jsp
#   res   `ST res:N`: a sound sent down the session before the command
#   rsc   MU, MC and CH res:N likewise
#   mic   `RS session`: the microphone as <mic len=N/> + raw bytes
#   ota   `FW res:N <sig>`: the plain image down the session
# `live` (radio and podcasts pulled through the session) stays off: this
# add-on does not relay streams, the rabbit fetches them itself.
SESSION_POLICIES = ("rfid", "res", "rsc", "mic", "ota")


def session_policies(blob: bytes) -> frozenset[str]:
    """The policies seal() turns on in this bytecode: those it carries
    exactly once."""
    return frozenset(p for p in SESSION_POLICIES
                     if blob.count(b"xmpp-%s:0" % p.encode()) == 1)


def seal(blob: bytes, row: dict | None) -> bytes:
    """What bc.jsp sends: for a confirmed key, TLS required and every
    session policy on (session_policies), then the HMAC, so the HMAC covers
    them; the bytecode as it is otherwise."""
    if row is None or row.get("state") != "confirmed":
        return blob
    for p in session_policies(blob):
        blob = blob.replace(b"xmpp-%s:0" % p.encode(), b"xmpp-%s:1" % p.encode())
    return sign_bytecode(require_tls(blob), row["key"])


# --------------------------------------------------------------------------- #
# A firmware update down the session
# --------------------------------------------------------------------------- #
SIM_MAGIC = b"-violet-"
SIM_SIG = b"-sig-"


def _deobfuscate(buf: bytes, key: int = 0x47, alpha: int = 47) -> bytes:
    """`decode8` from nabgcc src/vm/vlog.c, as the bytecode's `uncrypt bin 0
    nil 0x47 47` runs it; a copy of firmware-arm/signing/simpayload.py."""
    out = bytearray(len(buf))
    for i, c in enumerate(buf):
        v = (((c - alpha) & 0xFF) * key) & 0xFF
        out[i] = v
        key = (v + v + 1) & 0xFF
    return bytes(out)


def sim_image(blob: bytes) -> tuple[bytes, bytes]:
    """(plain image, 64-byte signature) of a signed .sim, what `FW res:N
    <sig>` hands the rabbit: it verifies the signature over the plain image
    itself before it flashes, as it does for an image fetched by URL.
    ValueError when the file is not a signed .sim."""
    if not blob.startswith(SIM_MAGIC) or not blob.endswith(SIM_SIG):
        raise ValueError("not a signed .sim")
    opened = blob.rfind(SIM_SIG, 0, len(blob) - len(SIM_SIG))
    sig_hex = blob[opened + len(SIM_SIG):-len(SIM_SIG)] if opened >= 0 else b""
    inner = blob[:opened] if opened >= 0 else b""
    if len(sig_hex) != 128 or not inner.endswith(SIM_MAGIC):
        raise ValueError("malformed signed .sim")
    payload = bytes.fromhex(inner[len(SIM_MAGIC) + 8:-len(SIM_MAGIC)].decode("ascii"))
    return _deobfuscate(payload), bytes.fromhex(sig_hex.decode("ascii"))


# --------------------------------------------------------------------------- #
# TLS 1.3 with the PSK
# --------------------------------------------------------------------------- #
# The rabbit asks for max_fragment_length 2^12 and holds one record of that
# size plus a TCP segment: never write more than this in one record.
RECORD_BYTES = 4096
# HTTP answers go in smaller records, as Le Terrier writes them.
HTTP_RECORD_BYTES = 1024

# A resource down the session (patch_res_in_session.py): the bytecode's
# XMPP_RES_BUDGET and XMPP_RES_KEEP. A longer one is read and dropped, so it
# goes by URL; a program never names more than the rabbit keeps.
RES_NS = "violet:nabaztag:res"
RES_MAX_BYTES = 200_000
RES_KEEP = 3
# The microphone inside the session (patch_mic_in_session.py), between
# stanzas: <mic xmlns='violet:nabaztag:mic' len='N'/> then N raw IMA-ADPCM
# bytes. A recorder buffer is a few hundred bytes.
MIC_HEAD = b"<mic xmlns='violet:nabaztag:mic' len='"
MIC_MAX_CHUNK = 16384


def tls_context(store: KeyStore) -> tuple[ssl.SSLContext, threading.local]:
    """TLS 1.3 only, an external PSK per rabbit -- confirmed keys only -- and
    `psk_ke`: the rabbit offers no key share (an X25519 per connection is
    2-8 s on its ARM7), which OpenSSL refuses unless SSL_OP_ALLOW_NO_DHE_KEX,
    bit 10, is set. No session tickets: the rabbit never resumes.

    Returns the context and a thread-local where the PSK callback leaves the
    identity it was asked for, read back on the same thread after the
    handshake."""
    if not hasattr(ssl.SSLContext, "set_psk_server_callback"):
        raise RuntimeError("TLS for adopted rabbits needs Python 3.13 or later")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.options |= 0x400
    ctx.num_tickets = 0
    seen = threading.local()

    def psk(identity: str) -> bytes:
        mac = (identity or "").lower()
        seen.mac = mac
        row = store.get(mac) if len(mac) == 12 else None
        if row is None or row["state"] != "confirmed":
            log.warning("TLS: PSK identity %r has no confirmed key, refused", identity)
            # Not None: Python's ssl wants bytes. An empty PSK matches no
            # binder, so the handshake fails as it should.
            return b""
        return tls_psk(row["key"])

    ctx.set_psk_server_callback(psk)
    return ctx, seen
