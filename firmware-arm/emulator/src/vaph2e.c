/**
 * @file vaph2e.c
 * @brief SAE hash-to-element, the access point's side, written from the
 *        standard and not from the rabbit's code.
 *
 * The virtual AP's SAE used to be ../crypto/sae.c with the roles swapped,
 * and the README says what that proves: the integration, never the
 * arithmetic. For H2E that is not good enough -- the whole exercise exists
 * because an AP may refuse everything but H2E, so an H2E the rabbit and
 * this AP merely agree on is worth nothing. This file therefore derives PT
 * and the PWE on its own: its own 256-bit integers, its own affine curve
 * arithmetic, its own reading of IEEE 802.11-2020 12.4.4.2.3 and 12.4.5.2.
 * Only the hash is shared (../crypto/sha256.c, which has its own OpenSSL
 * vectors). The two derivations then have to agree with each other AND
 * with the published vectors (vap_h2e_selftest(), run before every
 * scenario), and the AP compares its PWE with the station library's for
 * every exchange it runs.
 *
 * Nothing here is fast or constant-time, and it does not need to be: it
 * runs on a build machine, once per authentication. Multiplication mod p
 * is double-and-add over the bits of one operand, which is the slowest
 * correct way there is and the hardest to get wrong.
 */
#include <stdio.h>
#include <string.h>

#include "net/sha256.h"
#include "vaph2e.h"
#include "vectors_h2e.h"

/* --- 256-bit integers, four 64-bit limbs, little-endian ------------------ */

typedef struct { uint64_t v[4]; } bn;

static const bn P = {{ 0xffffffffffffffffull, 0x00000000ffffffffull,
                       0x0000000000000000ull, 0xffffffff00000001ull }};
static const bn N = {{ 0xf3b9cac2fc632551ull, 0xbce6faada7179e84ull,
                       0xffffffffffffffffull, 0xffffffff00000000ull }};
static const bn B = {{ 0x3bce3c3e27d2604bull, 0x651d06b0cc53b0f6ull,
                       0xb3ebbd55769886bcull, 0x5ac635d8aa3a93e7ull }};

static int bn_cmp(const bn *a, const bn *b)
{
  for(int i = 3; i >= 0; i--) {
    if(a->v[i] != b->v[i]) return a->v[i] > b->v[i] ? 1 : -1;
  }
  return 0;
}

static int bn_is_zero(const bn *a)
{
  return (a->v[0] | a->v[1] | a->v[2] | a->v[3]) == 0;
}

/* r = a + b, returns the carry out. */
static uint64_t bn_add(bn *r, const bn *a, const bn *b)
{
  unsigned __int128 c = 0;
  for(int i = 0; i < 4; i++) {
    c += (unsigned __int128)a->v[i] + b->v[i];
    r->v[i] = (uint64_t)c;
    c >>= 64;
  }
  return (uint64_t)c;
}

/* r = a - b, returns the borrow out. */
static uint64_t bn_sub(bn *r, const bn *a, const bn *b)
{
  uint64_t borrow = 0;
  for(int i = 0; i < 4; i++) {
    unsigned __int128 d = (unsigned __int128)a->v[i] - b->v[i] - borrow;
    r->v[i] = (uint64_t)d;
    borrow = (uint64_t)(d >> 64) & 1;
  }
  return borrow;
}

/* r = (a + b) mod m, for a, b < m. */
static void bn_addmod(bn *r, const bn *a, const bn *b, const bn *m)
{
  bn t;
  uint64_t carry = bn_add(&t, a, b);
  if(carry || bn_cmp(&t, m) >= 0) bn_sub(&t, &t, m);
  *r = t;
}

static void bn_submod(bn *r, const bn *a, const bn *b, const bn *m)
{
  bn t;
  if(bn_sub(&t, a, b)) bn_add(&t, &t, m);
  *r = t;
}

/* r = (a * b) mod m, double-and-add over the bits of b. */
static void bn_mulmod(bn *r, const bn *a, const bn *b, const bn *m)
{
  bn acc = {{0, 0, 0, 0}};
  for(int i = 255; i >= 0; i--) {
    bn_addmod(&acc, &acc, &acc, m);
    if((b->v[i >> 6] >> (i & 63)) & 1) bn_addmod(&acc, &acc, a, m);
  }
  *r = acc;
}

/* r = a^e mod m, square and multiply. */
static void bn_powmod(bn *r, const bn *a, const bn *e, const bn *m)
{
  bn acc = {{1, 0, 0, 0}};
  for(int i = 255; i >= 0; i--) {
    bn_mulmod(&acc, &acc, &acc, m);
    if((e->v[i >> 6] >> (i & 63)) & 1) bn_mulmod(&acc, &acc, a, m);
  }
  *r = acc;
}

static void bn_from_bytes(bn *r, const uint8_t *in, int len)
{
  /* big-endian, any length up to 32 */
  memset(r, 0, sizeof *r);
  for(int i = 0; i < len; i++) {
    int pos = len - 1 - i;            /* byte significance */
    r->v[pos >> 3] |= (uint64_t)in[i] << (8 * (pos & 7));
  }
}

static void bn_to_bytes(uint8_t out[32], const bn *a)
{
  for(int i = 0; i < 32; i++)
    out[i] = (uint8_t)(a->v[(31 - i) >> 3] >> (8 * ((31 - i) & 7)));
}

/* a mod m for a up to 48 bytes big-endian: fold the top 16 bytes in with
 * shifts, since 2^8 mod m is just repeated doubling. */
static void bn_from_bytes_mod(bn *r, const uint8_t *in, int len, const bn *m)
{
  bn acc = {{0, 0, 0, 0}};
  bn byte;
  for(int i = 0; i < len; i++) {
    for(int k = 0; k < 8; k++) bn_addmod(&acc, &acc, &acc, m);
    memset(&byte, 0, sizeof byte);
    byte.v[0] = in[i];
    bn_addmod(&acc, &acc, &byte, m);
  }
  *r = acc;
}

static void bn_shr1(bn *a)
{
  for(int k = 0; k < 3; k++) a->v[k] = (a->v[k] >> 1) | (a->v[k + 1] << 63);
  a->v[3] >>= 1;
}

/* Halve mod p: a/2 if even, (a+p)/2 otherwise (the carry out of the add
 * is the 257th bit, shifted back in). */
static void bn_half_mod(bn *a, const bn *m)
{
  if(a->v[0] & 1) {
    uint64_t carry = bn_add(a, a, m);
    bn_shr1(a);
    a->v[3] |= carry << 63;
  } else {
    bn_shr1(a);
  }
}

/* The binary inversion algorithm (Hankerson, Menezes, Vanstone 2.22):
 * subtractions and halvings only, which is a few hundred cheap steps where
 * Fermat's a^(p-2) is a few hundred multiplications. inverse(0) = 0, as
 * SSWU needs it. */
static void fe_inv(bn *r, const bn *a)
{
  bn u = *a, v = P, x1 = {{1, 0, 0, 0}}, x2 = {{0, 0, 0, 0}};
  bn one = {{1, 0, 0, 0}};
  if(bn_is_zero(a)) { memset(r, 0, sizeof *r); return; }
  while(bn_cmp(&u, &one) != 0 && bn_cmp(&v, &one) != 0) {
    while(!(u.v[0] & 1)) { bn_shr1(&u); bn_half_mod(&x1, &P); }
    while(!(v.v[0] & 1)) { bn_shr1(&v); bn_half_mod(&x2, &P); }
    if(bn_cmp(&u, &v) >= 0) { bn_sub(&u, &u, &v); bn_submod(&x1, &x1, &x2, &P); }
    else                    { bn_sub(&v, &v, &u); bn_submod(&x2, &x2, &x1, &P); }
  }
  *r = bn_cmp(&u, &one) == 0 ? x1 : x2;
}

/* Square root for p == 3 mod 4: a^((p+1)/4). Returns 1 if it squares back. */
static int fe_sqrt(bn *r, const bn *a)
{
  bn e, one = {{1, 0, 0, 0}}, chk;
  bn_add(&e, &P, &one);
  for(int i = 0; i < 2; i++) {
    for(int k = 0; k < 3; k++) e.v[k] = (e.v[k] >> 1) | (e.v[k + 1] << 63);
    e.v[3] >>= 1;
  }
  bn_powmod(r, a, &e, &P);
  bn_mulmod(&chk, r, r, &P);
  return bn_cmp(&chk, a) == 0;
}

/* g(x) = x^3 - 3x + b */
static void curve_g(bn *r, const bn *x)
{
  bn t, three = {{3, 0, 0, 0}};
  bn_mulmod(r, x, x, &P);
  bn_mulmod(r, r, x, &P);
  bn_mulmod(&t, &three, x, &P);
  bn_submod(r, r, &t, &P);
  bn_addmod(r, r, &B, &P);
}

/* --- affine points --------------------------------------------------------- */

typedef struct { bn x, y; int inf; } pt_t;

static int pt_on_curve(const pt_t *q)
{
  bn l, r;
  if(q->inf) return 0;
  bn_mulmod(&l, &q->y, &q->y, &P);
  curve_g(&r, &q->x);
  return bn_cmp(&l, &r) == 0;
}

static void pt_add(pt_t *r, const pt_t *a, const pt_t *b)
{
  bn lam, t, x3, y3;
  if(a->inf) { *r = *b; return; }
  if(b->inf) { *r = *a; return; }
  if(bn_cmp(&a->x, &b->x) == 0) {
    bn sum;
    bn_addmod(&sum, &a->y, &b->y, &P);
    if(bn_is_zero(&sum)) { memset(r, 0, sizeof *r); r->inf = 1; return; }
    /* doubling: lambda = (3x^2 - 3) / 2y */
    {
      bn three = {{3, 0, 0, 0}}, two = {{2, 0, 0, 0}};
      bn_mulmod(&lam, &a->x, &a->x, &P);
      bn_mulmod(&lam, &lam, &three, &P);
      bn_submod(&lam, &lam, &three, &P);
      bn_mulmod(&t, &a->y, &two, &P);
      fe_inv(&t, &t);
      bn_mulmod(&lam, &lam, &t, &P);
    }
  } else {
    bn_submod(&lam, &b->y, &a->y, &P);
    bn_submod(&t, &b->x, &a->x, &P);
    fe_inv(&t, &t);
    bn_mulmod(&lam, &lam, &t, &P);
  }
  bn_mulmod(&x3, &lam, &lam, &P);
  bn_submod(&x3, &x3, &a->x, &P);
  bn_submod(&x3, &x3, &b->x, &P);
  bn_submod(&y3, &a->x, &x3, &P);
  bn_mulmod(&y3, &lam, &y3, &P);
  bn_submod(&y3, &y3, &a->y, &P);
  r->x = x3; r->y = y3; r->inf = 0;
}

static void pt_mul(pt_t *r, const bn *k, const pt_t *q)
{
  pt_t acc;
  memset(&acc, 0, sizeof acc);
  acc.inf = 1;
  for(int i = 255; i >= 0; i--) {
    pt_add(&acc, &acc, &acc);
    if((k->v[i >> 6] >> (i & 63)) & 1) pt_add(&acc, &acc, q);
  }
  *r = acc;
}

/* --- 12.4.4.2.3: SSWU -------------------------------------------------------- */

/*
 * The standard's text, step for step (and RFC 9380 6.6.2 says the same
 * with sgn0 = the low bit). z = -10 for group 19.
 *
 *   m   = (z^2 * u^4 + z * u^2) modulo p
 *   l   = CEQ(m, 0)
 *   t   = inverse(m)                              (inverse(0) = 0)
 *   x1  = CSEL(l, b / (z * a), (-b / a) * (1 + t))
 *   gx1 = x1^3 + a*x1 + b
 *   x2  = z * u^2 * x1
 *   gx2 = x2^3 + a*x2 + b
 *   l   = gx1 is a quadratic residue
 *   v   = CSEL(l, gx1, gx2);  x = CSEL(l, x1, x2);  y = sqrt(v)
 *   P   = CSEL(LSB(u) == LSB(y), (x, y), (x, p - y))
 */
static void sswu(pt_t *out, const bn *u)
{
  bn z, a, u2, zu2, m, t, x1, gx1, x2, gx2, y, one = {{1, 0, 0, 0}};
  bn ten = {{10, 0, 0, 0}}, three = {{3, 0, 0, 0}};

  bn_sub(&z, &P, &ten);                  /* z = -10 */
  bn_sub(&a, &P, &three);                /* a = -3 */

  bn_mulmod(&u2, u, u, &P);
  bn_mulmod(&zu2, &z, &u2, &P);
  bn_mulmod(&m, &zu2, &zu2, &P);
  bn_addmod(&m, &m, &zu2, &P);

  if(bn_is_zero(&m)) {
    /* x1 = b / (z*a) */
    bn_mulmod(&t, &z, &a, &P);
    fe_inv(&t, &t);
    bn_mulmod(&x1, &B, &t, &P);
  } else {
    /* x1 = (-b/a) * (1 + 1/m) */
    bn nb;
    fe_inv(&t, &m);
    bn_addmod(&t, &t, &one, &P);
    bn_sub(&nb, &P, &B);
    fe_inv(&x1, &a);
    bn_mulmod(&x1, &x1, &nb, &P);
    bn_mulmod(&x1, &x1, &t, &P);
  }
  curve_g(&gx1, &x1);
  bn_mulmod(&x2, &zu2, &x1, &P);
  curve_g(&gx2, &x2);

  if(fe_sqrt(&y, &gx1)) {
    out->x = x1;
  } else {
    if(!fe_sqrt(&y, &gx2))
      fprintf(stderr, "vaph2e: neither g(x1) nor g(x2) is a square, "
                      "which cannot happen\n");
    out->x = x2;
  }
  if((u->v[0] & 1) != (y.v[0] & 1)) bn_sub(&y, &P, &y);
  out->y = y;
  out->inf = 0;
}

/* --- the hashes ---------------------------------------------------------------- */

/* RFC 5869 HKDF-Expand with HMAC-SHA-256, `len` <= 64. */
static void hkdf_expand(uint8_t *out, int len, const uint8_t prk[32],
                        const char *info)
{
  uint8_t t[32], buf[32 + 64 + 1];
  int tlen = 0, done = 0;
  uint8_t ctr = 1;
  while(done < len) {
    int n = 0;
    memcpy(buf, t, tlen); n += tlen;
    memcpy(buf + n, info, strlen(info)); n += (int)strlen(info);
    buf[n++] = ctr++;
    hmac_sha256(t, prk, 32, buf, (uint32_t)n);
    tlen = 32;
    int take = len - done < 32 ? len - done : 32;
    memcpy(out + done, t, take);
    done += take;
  }
}

int vap_h2e_pt(uint8_t pt_out[64], const char *ssid, const char *password,
               const char *identifier)
{
  uint8_t seed[32], value[48], msg[128];
  bn u;
  pt_t p1, p2, pt;
  int n = 0;

  /* pwd-seed = HKDF-Extract(ssid, password [|| identifier]) -- an HMAC
     keyed with the SSID over the password (and the identifier, if any). */
  memcpy(msg, password, strlen(password)); n += (int)strlen(password);
  if(identifier) { memcpy(msg + n, identifier, strlen(identifier)); n += (int)strlen(identifier); }
  hmac_sha256(seed, (const uint8_t *)ssid, (uint32_t)strlen(ssid), msg, (uint32_t)n);

  /* len = olen(p) + floor(olen(p)/2) = 48 */
  hkdf_expand(value, 48, seed, "SAE Hash to Element u1 P1");
  bn_from_bytes_mod(&u, value, 48, &P);
  sswu(&p1, &u);
  hkdf_expand(value, 48, seed, "SAE Hash to Element u2 P2");
  bn_from_bytes_mod(&u, value, 48, &P);
  sswu(&p2, &u);

  pt_add(&pt, &p1, &p2);
  if(pt.inf || !pt_on_curve(&pt)) return 0;
  bn_to_bytes(pt_out, &pt.x);
  bn_to_bytes(pt_out + 32, &pt.y);
  return 1;
}

int vap_h2e_pwe(uint8_t pwe_out[64], const uint8_t pt_in[64],
                const uint8_t *mac_a, const uint8_t *mac_b)
{
  uint8_t macs[12], zeros[32], hash[32];
  bn val, nm1, one = {{1, 0, 0, 0}};
  pt_t pt, pwe;

  /* val = H(0^32, MAX(a, b) || MIN(a, b)) */
  if(memcmp(mac_a, mac_b, 6) > 0) { memcpy(macs, mac_a, 6); memcpy(macs + 6, mac_b, 6); }
  else                            { memcpy(macs, mac_b, 6); memcpy(macs + 6, mac_a, 6); }
  memset(zeros, 0, sizeof zeros);
  hmac_sha256(hash, zeros, 32, macs, 12);

  /* val = (val modulo (q - 1)) + 1 */
  bn_sub(&nm1, &N, &one);
  bn_from_bytes_mod(&val, hash, 32, &nm1);
  bn_add(&val, &val, &one);

  bn_from_bytes(&pt.x, pt_in, 32);
  bn_from_bytes(&pt.y, pt_in + 32, 32);
  pt.inf = 0;
  if(!pt_on_curve(&pt)) return 0;

  pt_mul(&pwe, &val, &pt);
  if(pwe.inf) return 0;
  bn_to_bytes(pwe_out, &pwe.x);
  bn_to_bytes(pwe_out + 32, &pwe.y);
  return 1;
}

/* --- the vectors ------------------------------------------------------------- */

int vap_h2e_selftest(void)
{
  int failures = 0;
  size_t i, n = sizeof SSWU_VECS / sizeof SSWU_VECS[0];
  uint8_t pt[64], pwe[64];

  /* RFC 9380 J.1.1: u -> Q, and Q0 + Q1 = P */
  for(i = 0; i < n; i++) {
    const struct sswu_vec *v = &SSWU_VECS[i];
    bn u; pt_t q0, q1, p; uint8_t x[32], y[32];
    bn_from_bytes(&u, v->u0, 32); sswu(&q0, &u);
    bn_to_bytes(x, &q0.x); bn_to_bytes(y, &q0.y);
    if(memcmp(x, v->q0x, 32) || memcmp(y, v->q0y, 32)) {
      fprintf(stderr, "vaph2e: SSWU(u0) of RFC 9380 vector %u is wrong\n", (unsigned)i);
      failures++;
    }
    bn_from_bytes(&u, v->u1, 32); sswu(&q1, &u);
    bn_to_bytes(x, &q1.x); bn_to_bytes(y, &q1.y);
    if(memcmp(x, v->q1x, 32) || memcmp(y, v->q1y, 32)) {
      fprintf(stderr, "vaph2e: SSWU(u1) of RFC 9380 vector %u is wrong\n", (unsigned)i);
      failures++;
    }
    pt_add(&p, &q0, &q1);
    bn_to_bytes(x, &p.x); bn_to_bytes(y, &p.y);
    if(p.inf || memcmp(x, v->px, 32) || memcmp(y, v->py, 32)) {
      fprintf(stderr, "vaph2e: Q0 + Q1 of RFC 9380 vector %u is wrong\n", (unsigned)i);
      failures++;
    }
  }

  /* IEEE 802.11-2020 Annex J.10: the H2E PWE */
  if(!vap_h2e_pt(pt, AJ_SSID, AJ_PW, AJ_PWID) ||
     !vap_h2e_pwe(pwe, pt, AJ_ADDR1B, AJ_ADDR2B) ||
     memcmp(pwe, AJ_H2E_PWE_X, 32) || memcmp(pwe + 32, AJ_H2E_PWE_Y, 32)) {
    fprintf(stderr, "vaph2e: the Annex J.10 PWE does not come out\n");
    failures++;
  }
  return failures;
}
