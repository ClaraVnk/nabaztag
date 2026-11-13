/**
 * @file nab_ecdsa.c
 * @brief ECDSA verification on P-256 and P-384 (a = -3), Jacobian
 *        coordinates on nab_bn.c's Montgomery field, u1*G + u2*Q by
 *        Shamir's trick. p256.c is shaped for SAE and stays as it is.
 */
#include <string.h>

#include "net/nab_pki.h"

#ifdef __ELF__
#define SECTION_EXTBSS __attribute__((section(".extbss")))
#else
#define SECTION_EXTBSS
#endif

#define L 12                                  /* limbs, enough for P-384 */
typedef struct { uint32_t x[L], y[L], z[L]; } jac;

static struct {
  nab_bn_ctx p, n;
  int bytes;
  jac g, q, gq, r;
} E SECTION_EXTBSS;

static const uint8_t P256[4][32] = {
  {0xff,0xff,0xff,0xff,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff},
  {0xff,0xff,0xff,0xff,0,0,0,0,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xbc,0xe6,0xfa,0xad,0xa7,0x17,0x9e,0x84,0xf3,0xb9,0xca,0xc2,0xfc,0x63,0x25,0x51},
  {0x6b,0x17,0xd1,0xf2,0xe1,0x2c,0x42,0x47,0xf8,0xbc,0xe6,0xe5,0x63,0xa4,0x40,0xf2,0x77,0x03,0x7d,0x81,0x2d,0xeb,0x33,0xa0,0xf4,0xa1,0x39,0x45,0xd8,0x98,0xc2,0x96},
  {0x4f,0xe3,0x42,0xe2,0xfe,0x1a,0x7f,0x9b,0x8e,0xe7,0xeb,0x4a,0x7c,0x0f,0x9e,0x16,0x2b,0xce,0x33,0x57,0x6b,0x31,0x5e,0xce,0xcb,0xb6,0x40,0x68,0x37,0xbf,0x51,0xf5}};
static const uint8_t P384[4][48] = {
  {0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xfe,0xff,0xff,0xff,0xff,0,0,0,0,0,0,0,0,0xff,0xff,0xff,0xff},
  {0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xc7,0x63,0x4d,0x81,0xf4,0x37,0x2d,0xdf,0x58,0x1a,0x0d,0xb2,0x48,0xb0,0xa7,0x7a,0xec,0xec,0x19,0x6a,0xcc,0xc5,0x29,0x73},
  {0xaa,0x87,0xca,0x22,0xbe,0x8b,0x05,0x37,0x8e,0xb1,0xc7,0x1e,0xf3,0x20,0xad,0x74,0x6e,0x1d,0x3b,0x62,0x8b,0xa7,0x9b,0x98,0x59,0xf7,0x41,0xe0,0x82,0x54,0x2a,0x38,0x55,0x02,0xf2,0x5d,0xbf,0x55,0x29,0x6c,0x3a,0x54,0x5e,0x38,0x72,0x76,0x0a,0xb7},
  {0x36,0x17,0xde,0x4a,0x96,0x26,0x2c,0x6f,0x5d,0x9e,0x98,0xbf,0x92,0x92,0xdc,0x29,0xf8,0xf4,0x1d,0xbd,0x28,0x9a,0x14,0x7c,0xe9,0xda,0x31,0x13,0xb5,0xf0,0xb8,0xc0,0x0a,0x60,0xb1,0xce,0x1d,0x7e,0x81,0x9d,0x7a,0x43,0x1d,0x7c,0x90,0xea,0x0e,0x5f}};

static int nl(void) { return E.p.n; }
static void fadd(uint32_t *r, const uint32_t *a, const uint32_t *b)
{ if (nab_bn_add(r, a, b, nl()) || nab_bn_cmp(r, E.p.m, nl()) >= 0) nab_bn_sub(r, r, E.p.m, nl()); }
static void fsub(uint32_t *r, const uint32_t *a, const uint32_t *b)
{ if (nab_bn_sub(r, a, b, nl())) nab_bn_add(r, r, E.p.m, nl()); }
static void fmul(uint32_t *r, const uint32_t *a, const uint32_t *b) { nab_bn_mul(&E.p, r, a, b); }
static int zero(const uint32_t *a) { for (int i = 0; i < nl(); i++) if (a[i]) return 0; return 1; }

static void dbl(jac *r, const jac *p)
{
  uint32_t d[L], s[L], m[L], t[L], y2[L];
  if (zero(p->z)) { *r = *p; return; }
  fmul(d, p->z, p->z);                                  /* m = 3 (X - Z^2)(X + Z^2) */
  fsub(t, p->x, d); fadd(s, p->x, d); fmul(m, t, s); fadd(t, m, m); fadd(m, t, m);
  fmul(y2, p->y, p->y); fmul(s, p->x, y2); fadd(s, s, s); fadd(s, s, s);   /* s = 4 X Y^2 */
  fmul(t, p->y, p->z); fadd(r->z, t, t);
  fmul(t, m, m); fsub(t, t, s); fsub(r->x, t, s);
  fmul(y2, y2, y2); fadd(y2, y2, y2); fadd(y2, y2, y2); fadd(y2, y2, y2);   /* 8 Y^4 */
  fsub(t, s, r->x); fmul(t, m, t); fsub(r->y, t, y2);
}

static void add(jac *r, const jac *p, const jac *q)
{
  uint32_t z1[L], z2[L], u1[L], u2[L], s1[L], s2[L], h[L], rr[L], h2[L], h3[L], t[L];
  if (zero(p->z)) { *r = *q; return; }
  if (zero(q->z)) { *r = *p; return; }
  fmul(z1, p->z, p->z); fmul(z2, q->z, q->z);
  fmul(u1, p->x, z2); fmul(u2, q->x, z1);
  fmul(s1, p->y, z2); fmul(s1, s1, q->z); fmul(s2, q->y, z1); fmul(s2, s2, p->z);
  fsub(h, u2, u1); fsub(rr, s2, s1);
  if (zero(h)) {
    if (zero(rr)) dbl(r, p); else memset(r, 0, sizeof *r);
    return;
  }
  fmul(h2, h, h); fmul(h3, h2, h); fmul(u1, u1, h2);
  fmul(t, rr, rr); fsub(t, t, h3); fsub(t, t, u1); fsub(t, t, u1);
  fsub(u2, u1, t); fmul(u2, rr, u2); fmul(s1, s1, h3); fsub(r->y, u2, s1);
  memcpy(r->x, t, sizeof t);
  fmul(t, p->z, q->z); fmul(r->z, t, h);
}

/* One INTEGER of the DER signature, left-padded to `bytes`. */
static int der_int(const uint8_t **p, const uint8_t *end, uint8_t *out, int bytes)
{
  const uint8_t *q = *p;
  if (end - q < 2 || q[0] != 0x02 || q[1] & 0x80) return -1;
  int len = q[1];
  q += 2;
  if (end - q < len || len < 1) return -1;
  while (len > 1 && !*q) { q++; len--; }
  if (len > bytes) return -1;
  memset(out, 0, (size_t)(bytes - len));
  memcpy(out + bytes - len, q, (size_t)len);
  *p = q + len;
  return 0;
}

int nab_ecdsa_verify(int curve, const uint8_t *pub, uint32_t publen,
                     const uint8_t *sig, uint32_t siglen,
                     const uint8_t *hash, uint32_t hlen, nab_pki_tick tick)
{
  const uint8_t (*c)[48] = NULL;
  uint8_t rb[48], sb[48], ex[48], u1b[48], u2b[48];
  uint32_t r[L], s[L], e[L], w[L], u1[L], u2[L], one[L], x[L], zi[L];
  int bytes = curve == NAB_P384 ? 48 : 32, n;

  if (curve == NAB_P384) c = P384;
  if (publen != 1u + 2u * (uint32_t)bytes || pub[0] != 4) return 0;
  /* SEQUENCE { INTEGER r, INTEGER s }, nothing after. */
  const uint8_t *p = sig, *end = sig + siglen;
  if (siglen < 8 || p[0] != 0x30) return 0;
  if (p[1] & 0x80) { if (p[1] != 0x81 || siglen < 3) return 0; p += 3; } else p += 2;
  if (der_int(&p, end, rb, bytes) || der_int(&p, end, sb, bytes) || p != end) return 0;

  nab_bn_init(&E.p, c ? c[0] : P256[0], (uint32_t)bytes);
  nab_bn_init(&E.n, c ? c[1] : P256[1], (uint32_t)bytes);
  n = E.n.n;
  nab_bn_from_bytes(r, n, rb, (uint32_t)bytes);
  nab_bn_from_bytes(s, n, sb, (uint32_t)bytes);
  if (zero(r) || zero(s) || nab_bn_cmp(r, E.n.m, n) >= 0 || nab_bn_cmp(s, E.n.m, n) >= 0) return 0;

  /* e: the leftmost bits of the hash, as many as the order has. */
  memset(ex, 0, sizeof ex);
  memcpy(ex + (hlen < (uint32_t)bytes ? (uint32_t)bytes - hlen : 0), hash,
         hlen < (uint32_t)bytes ? hlen : (uint32_t)bytes);
  nab_bn_from_bytes(e, n, ex, (uint32_t)bytes);
  if (nab_bn_cmp(e, E.n.m, n) >= 0) nab_bn_sub(e, e, E.n.m, n);

  /* w = s^(n-2) mod n; u1 = e w, u2 = r w. */
  memset(one, 0, sizeof one); one[0] = 2;
  nab_bn_sub(w, E.n.m, one, n);
  nab_bn_to_bytes(u1b, (uint32_t)bytes, w, n);
  nab_bn_exp(&E.n, w, s, u1b, (uint32_t)bytes, tick);
  nab_bn_to_mont(&E.n, w, w); nab_bn_to_mont(&E.n, e, e); nab_bn_to_mont(&E.n, r, r);
  nab_bn_mul(&E.n, u1, e, w); nab_bn_mul(&E.n, u2, r, w);
  nab_bn_from_mont(&E.n, u1, u1); nab_bn_from_mont(&E.n, u2, u2);
  nab_bn_from_mont(&E.n, r, r);
  nab_bn_to_bytes(u1b, (uint32_t)bytes, u1, n);
  nab_bn_to_bytes(u2b, (uint32_t)bytes, u2, n);

  memset(one, 0, sizeof one); one[0] = 1;
  memset(&E.g, 0, sizeof E.g); memset(&E.q, 0, sizeof E.q);
  nab_bn_from_bytes(E.g.x, n, c ? c[2] : P256[2], (uint32_t)bytes);
  nab_bn_from_bytes(E.g.y, n, c ? c[3] : P256[3], (uint32_t)bytes);
  nab_bn_from_bytes(E.q.x, n, pub + 1, (uint32_t)bytes);
  nab_bn_from_bytes(E.q.y, n, pub + 1 + bytes, (uint32_t)bytes);
  if (nab_bn_cmp(E.q.x, E.p.m, n) >= 0 || nab_bn_cmp(E.q.y, E.p.m, n) >= 0) return 0;
  nab_bn_to_mont(&E.p, E.g.x, E.g.x); nab_bn_to_mont(&E.p, E.g.y, E.g.y); nab_bn_to_mont(&E.p, E.g.z, one);
  nab_bn_to_mont(&E.p, E.q.x, E.q.x); nab_bn_to_mont(&E.p, E.q.y, E.q.y); nab_bn_to_mont(&E.p, E.q.z, one);
  add(&E.gq, &E.g, &E.q);
  memset(&E.r, 0, sizeof E.r);
  for (int i = 0; i < bytes * 8; i++) {
    int a = (u1b[i / 8] >> (7 - i % 8)) & 1, b = (u2b[i / 8] >> (7 - i % 8)) & 1;
    dbl(&E.r, &E.r);
    if (a || b) add(&E.r, &E.r, a && b ? &E.gq : a ? &E.g : &E.q);
    if (tick && !(i & 1)) tick();          /* ~0.45 M instructions a bit on P-384 */
  }
  if (zero(E.r.z)) return 0;

  /* x = X / Z^2, reduced mod n, against r. */
  fmul(zi, E.r.z, E.r.z);
  nab_bn_from_mont(&E.p, zi, zi);
  memset(one, 0, sizeof one); one[0] = 2;
  nab_bn_sub(w, E.p.m, one, n);
  nab_bn_to_bytes(u1b, (uint32_t)bytes, w, n);
  nab_bn_exp(&E.p, zi, zi, u1b, (uint32_t)bytes, tick);
  nab_bn_to_mont(&E.p, zi, zi);
  fmul(x, E.r.x, zi);
  nab_bn_from_mont(&E.p, x, x);
  if (nab_bn_cmp(x, E.n.m, n) >= 0) nab_bn_sub(x, x, E.n.m, n);
  return !nab_bn_cmp(x, r, n);
}
