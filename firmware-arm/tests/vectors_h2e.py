"""Print the SAE known-answer vectors as C: RFC 9380's SSWU vectors and the
IEEE 802.11-2020 Annex J.10 SAE vectors, hunting-and-pecking and
hash-to-element both.

Where they come from, because that is the whole point of this file:

  - RFC 9380 Appendix J.1.1 (P256_XMD:SHA-256_SSWU_RO_). Each vector gives
    the two field elements u[0], u[1], the two curve points Q0 = SSWU(u[0]),
    Q1 = SSWU(u[1]), and P = Q0 + Q1. That isolates the Simplified SWU map
    -- the part of H2E with the most arithmetic in it -- from everything
    around it. The numbers are a machine copy of the CFRG vector file, not
    typed.
  - IEEE 802.11-2020 Annex J.10, as hostapd carries it in
    src/common/common_module_tests.c: a hunting-and-pecking commit from a
    given rand and mask, the peer's commit, and the KCK, PMK and PMKID that
    follow; and for H2E, the PWE for a given SSID, password, password
    identifier and pair of MAC addresses. Also a machine copy.

Everything below is first recomputed by a Python implementation written from
the standard's text, independently of the C. If the Python disagrees with a
published number this script fails and prints nothing, so a transcription
slip in the copies above cannot become a wrong expected value in the test.
The intermediate values the standard does not publish (PT, the
hunting-and-pecking PWE) are emitted from the Python and marked as such:
they let test_sae.c say *which* step is wrong, not only that one is.

Until this file existed the SAE test suite's peer was the SAE code itself,
and sae.c's header said so at length. The hunting-and-pecking vector here
is the first time that code has been compared with anything outside this
repository.
"""
import hashlib
import hmac
import sys

# --- the published numbers ------------------------------------------------

# RFC 9380 Appendix J.1.1, P256_XMD:SHA-256_SSWU_RO_ (machine copy of the
# CFRG vector file; Z = -10 for P-256). msg, u[0], u[1], Q0, Q1, P.
RFC9380_P256 = [
    dict(msg='',
         u0='ad5342c66a6dd0ff080df1da0ea1c04b96e0330dd89406465eeba11582515009',
         u1='8c0f1d43204bd6f6ea70ae8013070a1518b43873bcd850aafa0a9e220e2eea5a',
         q0x='ab640a12220d3ff283510ff3f4b1953d09fad35795140b1c5d64f313967934d5', q0y='dccb558863804a881d4fff3455716c836cef230e5209594ddd33d85c565b19b1',
         q1x='51cce63c50d972a6e51c61334f0f4875c9ac1cd2d3238412f84e31da7d980ef5', q1y='b45d1a36d00ad90e5ec7840a60a4de411917fbe7c82c3949a6e699e5a1b66aac',
         px='2c15230b26dbc6fc9a37051158c95b79656e17a1a920b11394ca91c44247d3e4', py='8a7a74985cc5c776cdfe4b1f19884970453912e9d31528c060be9ab5c43e8415'),
    dict(msg='abc',
         u0='afe47f2ea2b10465cc26ac403194dfb68b7f5ee865cda61e9f3e07a537220af1',
         u1='379a27833b0bfe6f7bdca08e1e83c760bf9a338ab335542704edcd69ce9e46e0',
         q0x='5219ad0ddef3cc49b714145e91b2f7de6ce0a7a7dc7406c7726c7e373c58cb48', q0y='7950144e52d30acbec7b624c203b1996c99617d0b61c2442354301b191d93ecf',
         q1x='019b7cb4efcfeaf39f738fe638e31d375ad6837f58a852d032ff60c69ee3875f', q1y='589a62d2b22357fed5449bc38065b760095ebe6aeac84b01156ee4252715446e',
         px='0bb8b87485551aa43ed54f009230450b492fead5f1cc91658775dac4a3388a0f', py='5c41b3d0731a27a7b14bc0bf0ccded2d8751f83493404c84a88e71ffd424212e'),
    dict(msg='abcdef0123456789',
         u0='0fad9d125a9477d55cf9357105b0eb3a5c4259809bf87180aa01d651f53d312c',
         u1='b68597377392cd3419d8fcc7d7660948c8403b19ea78bbca4b133c9d2196c0fb',
         q0x='a17bdf2965eb88074bc01157e644ed409dac97cfcf0c61c998ed0fa45e79e4a2', q0y='4f1bc80c70d411a3cc1d67aeae6e726f0f311639fee560c7f5a664554e3c9c2e',
         q1x='7da48bb67225c1a17d452c983798113f47e438e4202219dd0715f8419b274d66', q1y='b765696b2913e36db3016c47edb99e24b1da30e761a8a3215dc0ec4d8f96e6f9',
         px='65038ac8f2b1def042a5df0b33b1f4eca6bff7cb0f9c6c1526811864e544ed80', py='cad44d40a656e7aff4002a8de287abc8ae0482b5ae825822bb870d6df9b56ca3'),
    dict(msg='q128_' + 'q' * 128,
         u0='3bbc30446f39a7befad080f4d5f32ed116b9534626993d2cc5033f6f8d805919',
         u1='76bb02db019ca9d3c1e02f0c17f8baf617bbdae5c393a81d9ce11e3be1bf1d33',
         q0x='c76aaa823aeadeb3f356909cb08f97eee46ecb157c1f56699b5efebddf0e6398', q0y='776a6f45f528a0e8d289a4be12c4fab80762386ec644abf2bffb9b627e4352b1',
         q1x='418ac3d85a5ccc4ea8dec14f750a3a9ec8b85176c95a7022f391826794eb5a75', q1y='fd6604f69e9d9d2b74b072d14ea13050db72c932815523305cb9e807cc900aff',
         px='4be61ee205094282ba8a2042bcb48d88dfbb609301c49aa8b078533dc65a0b5d', py='98f8df449a072c4721d241a3b1236d3caccba603f916ca680f4539d2bfb3c29e'),
    dict(msg='a512_' + 'a' * 512,
         u0='4ebc95a6e839b1ae3c63b847798e85cb3c12d3817ec6ebc10af6ee51adb29fec',
         u1='4e21af88e22ea80156aff790750121035b3eefaa96b425a8716e0d20b4e269ee',
         q0x='d88b989ee9d1295df413d4456c5c850b8b2fb0f5402cc5c4c7e815412e926db8', q0y='bb4a1edeff506cf16def96afff41b16fc74f6dbd55c2210e5b8f011ba32f4f40',
         q1x='a281e34e628f3a4d2a53fa87ff973537d68ad4fbc28d3be5e8d9f6a2571c5a4b', q1y='f6ed88a7aab56a488100e6f1174fa9810b47db13e86be999644922961206e184',
         px='457ae2981f70ca85d8e24c308b14db22f3e3862c5ea0f652ca38b5e49cd64bc5', py='ecb9f0eadc9aeed232dabc53235368c1394c78de05dd96893eefa62b0f4757dc'),
]

# IEEE Std 802.11-2020 Annex J.10, as hostapd's common_module_tests.c
# carries them (machine copy).
ANNEXJ_ADDR1 = '4d3f2fffe387'
ANNEXJ_ADDR2 = 'a5d8aa958e3c'
ANNEXJ_LOCAL_RAND = '992465fd3daa3c60aa6565b7f62a2a7f2e12dd12f198faf4fbed89d7ff1ace94'
ANNEXJ_LOCAL_MASK = '9507a90f777a044d6a0830b91ea3d5dd70bece44e1acffb86983b5e1bf9fb322'
ANNEXJ_LOCAL_COMMIT = '13002e2c0f0db52440ad146d967114ce005ce1eab0aa2c2e5c2871b774f6c2575c65d5ad9e00829707aa36ba8b859738fc961d08243505f47c035376d7ac4bc8d7b95083bf43827d0fc31ed778dd3671fd21a46d1091d64b6f9a1e1272621325dbe1'
ANNEXJ_PEER_COMMIT = '1300591b96f3397fb945100848e7b550543b6720d88337ee93fc49fd6df7e08b5223e71b9bb048d3873f20556953a96c91536fd8ee6ca9b4a68a148b056a909be03e83ae208f60f8ef5537858074db06687032399862999b511e0a1552a5fea317c2'
ANNEXJ_KCK = '1e733f6d9bd53256287304338831b09a39406d121017073a5c30db36f36cb81a'
ANNEXJ_PMK = '4e4dfab1a2dd8ac1a91790f953faaa452ae5c6873ab75b63605ba663f8a7fe59'
ANNEXJ_PMKID = '8747a600eea3f9f22475df58ca1e5498'
ANNEXJ_PWE_19_X = 'c93049b9e64000f848201649e999f2b5c22dea69b5632c9df4d633b8aa1f6c1e'
ANNEXJ_PWE_19_Y = '73634e94b53d82e7383a8d258199d9dc1a5ee8269d060382ccbf33e614ff59a0'
ANNEXJ_ADDR1B = '00095b66ec1e'
ANNEXJ_ADDR2B = '000b6bd90246'
ANNEXJ_SSID = 'byteme'
ANNEXJ_PW = 'mekmitasdigoat'
ANNEXJ_PWID = 'psk4internet'

# --- P-256, written out in integers ----------------------------------------

P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
Z = P - 10          # RFC 9380 8.2 and 802.11-2020 12.4.4.2.3: z = -10 for group 19


def inv(x):
    return pow(x, P - 2, P)


def sqrt(v):
    """p == 3 mod 4. The caller checks the square."""
    return pow(v, (P + 1) // 4, P)


def on_curve(pt):
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * inv(2 * y1) % P
    else:
        lam = (y2 - y1) * inv((x2 - x1) % P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def mul(k, pt):
    acc = None
    for i in range(255, -1, -1):
        acc = add(acc, acc)
        if (k >> i) & 1:
            acc = add(acc, pt)
    return acc


def neg(pt):
    return pt[0], (P - pt[1]) % P


def i2b(v, n=32):
    return v.to_bytes(n, "big")


def b2i(b):
    return int.from_bytes(b, "big")


# --- the hash side -----------------------------------------------------------

def H(key, *pieces):
    return hmac.new(key, b"".join(pieces), hashlib.sha256).digest()


def kdf(key, label, context, nbytes):
    """802.11-2020 12.7.1.6.2, KDF-Hash-Length with HMAC-SHA-256."""
    nbits = nbytes * 8
    out, i = b"", 1
    while len(out) < nbytes:
        out += H(key, i.to_bytes(2, "little"), label.encode(), context,
                 nbits.to_bytes(2, "little"))
        i += 1
    return out[:nbytes]


def hkdf_expand(prk, info, nbytes):
    """RFC 5869 2.3."""
    out, t, i = b"", b"", 1
    while len(out) < nbytes:
        t = H(prk, t, info.encode(), bytes([i]))
        out += t
        i += 1
    return out[:nbytes]


# --- 12.4.4.2.3: SSWU and the password token PT -----------------------------

def sswu(u):
    """Simplified SWU, as 802.11-2020 12.4.4.2.3 spells it out (and RFC 9380
    6.6.2 with sgn0 = LSB)."""
    m = (Z * Z * pow(u, 4, P) + Z * u * u) % P
    t = inv(m)                                    # inverse(0) is 0
    x1 = (B * inv(Z * A % P)) % P if m == 0 else ((P - B) * inv(A) * (1 + t)) % P
    gx1 = (x1 ** 3 + A * x1 + B) % P
    x2 = (Z * u * u * x1) % P
    gx2 = (x2 ** 3 + A * x2 + B) % P
    y1 = sqrt(gx1)
    if y1 * y1 % P == gx1:
        x, y = x1, y1
    else:
        x, y = x2, sqrt(gx2)
    assert y * y % P == (x ** 3 + A * x + B) % P
    if (u & 1) != (y & 1):
        y = P - y
    return x, y


def derive_pt(ssid, password, identifier=None):
    pieces = [password.encode()]
    if identifier:
        pieces.append(identifier.encode())
    pwd_seed = H(ssid.encode(), *pieces)          # HKDF-Extract(ssid, pw [|| id])
    pts = []
    for label in ("SAE Hash to Element u1 P1", "SAE Hash to Element u2 P2"):
        pwd_value = hkdf_expand(pwd_seed, label, 48)   # olen(p) + olen(p)/2
        u = b2i(pwd_value) % P
        pts.append(sswu(u))
    return add(pts[0], pts[1])


def pwe_from_pt(pt, mac_a, mac_b):
    """12.4.5.2: val = H(0^32, max || min) mod (q-1) + 1; PWE = val * PT."""
    hi, lo = max(mac_a, mac_b), min(mac_a, mac_b)
    val = b2i(H(bytes(32), hi, lo)) % (N - 1) + 1
    return mul(val, pt)


# --- 12.4.4.2.2: hunting and pecking ----------------------------------------

def hunt_and_peck(mac_a, mac_b, password, rounds=40):
    hi, lo = max(mac_a, mac_b), min(mac_a, mac_b)
    found = None
    for counter in range(1, rounds + 1):
        seed = H(hi + lo, password.encode(), bytes([counter]))
        value = b2i(kdf(seed, "SAE Hunting and Pecking", i2b(P), 32))
        if value >= P:
            continue
        x = value
        gx = (x ** 3 + A * x + B) % P
        y = sqrt(gx)
        if y * y % P != gx:
            continue
        if (seed[-1] & 1) != (y & 1):
            y = P - y
        if found is None:
            found = (x, y)
    return found


# --- 12.4.5: commit and keys --------------------------------------------------

def commit(pwe, rand, mask):
    scalar = (rand + mask) % N
    element = neg(mul(mask, pwe))
    return scalar, element


def commit_bytes(scalar, element):
    return b"\x13\x00" + i2b(scalar) + i2b(element[0]) + i2b(element[1])


def keys(pwe, rand, own_scalar, peer_scalar, peer_element):
    k_point = mul(rand, add(mul(peer_scalar, pwe), peer_element))
    keyseed = H(bytes(32), i2b(k_point[0]))
    context = i2b((own_scalar + peer_scalar) % N)
    kck_pmk = kdf(keyseed, "SAE KCK and PMK", context, 64)
    return kck_pmk[:32], kck_pmk[32:], context[:16]


# --- recompute everything, refuse to print on any disagreement ---------------

def check(cond, what):
    if not cond:
        sys.exit(f"vectors_h2e.py: the Python oracle disagrees with {what}")


for v in RFC9380_P256:
    q0 = sswu(int(v["u0"], 16))
    q1 = sswu(int(v["u1"], 16))
    check(q0 == (int(v["q0x"], 16), int(v["q0y"], 16)), f"RFC 9380 Q0 for msg {v['msg'][:12]!r}")
    check(q1 == (int(v["q1x"], 16), int(v["q1y"], 16)), f"RFC 9380 Q1 for msg {v['msg'][:12]!r}")
    check(add(q0, q1) == (int(v["px"], 16), int(v["py"], 16)), f"RFC 9380 P for msg {v['msg'][:12]!r}")

addr1, addr2 = bytes.fromhex(ANNEXJ_ADDR1), bytes.fromhex(ANNEXJ_ADDR2)
rand, mask = int(ANNEXJ_LOCAL_RAND, 16), int(ANNEXJ_LOCAL_MASK, 16)
hnp_pwe = hunt_and_peck(addr1, addr2, ANNEXJ_PW)
check(hnp_pwe is not None, "hunting and pecking finding a point at all")
own_scalar, own_element = commit(hnp_pwe, rand, mask)
check(commit_bytes(own_scalar, own_element).hex() == ANNEXJ_LOCAL_COMMIT, "the Annex J.10 commit")
peer = bytes.fromhex(ANNEXJ_PEER_COMMIT)
peer_scalar = b2i(peer[2:34])
peer_element = (b2i(peer[34:66]), b2i(peer[66:98]))
check(on_curve(peer_element), "the Annex J.10 peer element being on the curve")
kck, pmk, pmkid = keys(hnp_pwe, rand, own_scalar, peer_scalar, peer_element)
check(kck.hex() == ANNEXJ_KCK, "the Annex J.10 KCK")
check(pmk.hex() == ANNEXJ_PMK, "the Annex J.10 PMK")
check(pmkid.hex() == ANNEXJ_PMKID, "the Annex J.10 PMKID")

pt = derive_pt(ANNEXJ_SSID, ANNEXJ_PW, ANNEXJ_PWID)
pwe = pwe_from_pt(pt, bytes.fromhex(ANNEXJ_ADDR1B), bytes.fromhex(ANNEXJ_ADDR2B))
check(pwe == (int(ANNEXJ_PWE_19_X, 16), int(ANNEXJ_PWE_19_Y, 16)), "the Annex J.10 H2E PWE")

# One more H2E case without a password identifier, which is what a rabbit on
# a home network sends. Oracle-derived: the standard publishes none.
pt_noid = derive_pt(ANNEXJ_SSID, ANNEXJ_PW)
pwe_noid = pwe_from_pt(pt_noid, addr1, addr2)

# --- print -------------------------------------------------------------------


def arr(name, b):
    body = ", ".join(f"0x{x:02x}" for x in b) if b else "0"
    print(f"static const uint8_t {name}[] = {{ {body} }};")


def hx(name, s):
    arr(name, bytes.fromhex(s))


print("/* Generated by vectors_h2e.py -- do not edit. RFC 9380 J.1.1 and IEEE")
print(" * 802.11-2020 Annex J.10 vectors, each recomputed by the generator's own")
print(" * implementation before being printed; values marked oracle-derived are")
print(" * the generator's and not published anywhere. */")
print()
print("#ifndef NAB_VECTORS_H2E_H")
print("#define NAB_VECTORS_H2E_H")
print()
print("#include <stdint.h>")
print()
print("struct sswu_vec { const uint8_t *u0, *q0x, *q0y, *u1, *q1x, *q1y, *px, *py; };")
print()
for i, v in enumerate(RFC9380_P256):
    for k in ("u0", "q0x", "q0y", "u1", "q1x", "q1y", "px", "py"):
        hx(f"sw{i}_{k}", v[k])
print()
print("static const struct sswu_vec SSWU_VECS[] = {")
for i in range(len(RFC9380_P256)):
    print(f"  {{ sw{i}_u0, sw{i}_q0x, sw{i}_q0y, sw{i}_u1, sw{i}_q1x, sw{i}_q1y, sw{i}_px, sw{i}_py }},")
print("};")
print()
print("/* Annex J.10, hunting and pecking: STA-A, STA-B, the password, rand and")
print(" * mask, and what must come out. */")
hx("AJ_ADDR1", ANNEXJ_ADDR1)
hx("AJ_ADDR2", ANNEXJ_ADDR2)
print(f'static const char AJ_PW[] = "{ANNEXJ_PW}";')
hx("AJ_RAND", ANNEXJ_LOCAL_RAND)
hx("AJ_MASK", ANNEXJ_LOCAL_MASK)
hx("AJ_COMMIT", ANNEXJ_LOCAL_COMMIT)
hx("AJ_PEER_COMMIT", ANNEXJ_PEER_COMMIT)
hx("AJ_KCK", ANNEXJ_KCK)
hx("AJ_PMK", ANNEXJ_PMK)
hx("AJ_PMKID", ANNEXJ_PMKID)
print("/* oracle-derived: the hunting-and-pecking PWE behind that commit */")
arr("AJ_HNP_PWE_X", i2b(hnp_pwe[0]))
arr("AJ_HNP_PWE_Y", i2b(hnp_pwe[1]))
print()
print("/* Annex J.10, hash-to-element: SSID, password, identifier, two other")
print(" * addresses, and the PWE. */")
print(f'static const char AJ_SSID[] = "{ANNEXJ_SSID}";')
print(f'static const char AJ_PWID[] = "{ANNEXJ_PWID}";')
hx("AJ_ADDR1B", ANNEXJ_ADDR1B)
hx("AJ_ADDR2B", ANNEXJ_ADDR2B)
hx("AJ_H2E_PWE_X", ANNEXJ_PWE_19_X)
hx("AJ_H2E_PWE_Y", ANNEXJ_PWE_19_Y)
print("/* oracle-derived: the PT that PWE comes from */")
arr("AJ_H2E_PT_X", i2b(pt[0]))
arr("AJ_H2E_PT_Y", i2b(pt[1]))
print("/* oracle-derived: the same without a password identifier, for STA-A and")
print(" * STA-B of the hunting-and-pecking case */")
arr("AJ_H2E_NOID_PT_X", i2b(pt_noid[0]))
arr("AJ_H2E_NOID_PT_Y", i2b(pt_noid[1]))
arr("AJ_H2E_NOID_PWE_X", i2b(pwe_noid[0]))
arr("AJ_H2E_NOID_PWE_Y", i2b(pwe_noid[1]))
print()
print("#endif")
