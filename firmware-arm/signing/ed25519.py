"""Ed25519 — reference implementation from RFC 8032 Appendix A.

Pure stdlib. Slow but correct (matches official test vectors). Used by
gen_key.py / sign_sim.py / verify_sim.py; never called from the firmware
(the firmware has its own minimal C impl).
"""

import hashlib

_q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _modp_inv(x: int) -> int:
    return pow(x, _q - 2, _q)


# Curve constant d = -121665 * inv(121666) mod q
_d = -121665 * _modp_inv(121666) % _q
# Square root of -1
_modp_sqrt_m1 = pow(2, (_q - 1) // 4, _q)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _q:
        return None
    x2 = (y * y - 1) * _modp_inv(_d * y * y + 1)
    if x2 == 0:
        if sign:
            return None
        return 0
    x = pow(x2, (_q + 3) // 8, _q)
    if (x * x - x2) % _q != 0:
        x = (x * _modp_sqrt_m1) % _q
    if (x * x - x2) % _q != 0:
        return None
    if (x & 1) != sign:
        x = _q - x
    return x


# Base point G
_g_y = 4 * _modp_inv(5) % _q
_g_x = _recover_x(_g_y, 0)
_G = (_g_x, _g_y, 1, _g_x * _g_y % _q)


def _point_add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _q
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _q
    C = 2 * P[3] * Q[3] * _d % _q
    D = 2 * P[2] * Q[2] % _q
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _q, G * H % _q, F * G % _q, E * H % _q)


def _point_mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_equal(P, Q) -> bool:
    if (P[0] * Q[2] - Q[0] * P[2]) % _q != 0:
        return False
    if (P[1] * Q[2] - Q[1] * P[2]) % _q != 0:
        return False
    return True


def _point_compress(P) -> bytes:
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % _q
    y = P[1] * zinv % _q
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s: bytes):
    if len(s) != 32:
        raise ValueError("Invalid point length")
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _q)


def _secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("Bad private key length")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def secret_to_public(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _point_compress(_point_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    A = _point_compress(_point_mul(a, _G))
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    R = _point_mul(r, _G)
    Rs = _point_compress(R)
    h = int.from_bytes(_sha512(Rs + A + msg), "little") % _L
    s = (r + h * a) % _L
    return Rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    if len(public) != 32 or len(sig) != 64:
        return False
    R = _point_decompress(sig[:32])
    if R is None:
        return False
    A = _point_decompress(public)
    if A is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(_sha512(sig[:32] + public + msg), "little") % _L
    sB = _point_mul(s, _G)
    hA = _point_mul(h, A)
    return _point_equal(sB, _point_add(R, hA))
