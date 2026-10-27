/**
 * @file p256.c
 * @brief NIST P-256 arithmetic. See p256.h for why it looks like this.
 *
 * The only numbers written down here are the curve's own: p, b, n and the
 * base point. Everything else -- the Montgomery radix, the exponents for
 * inversion and for the square root -- is derived from p at startup. That is
 * deliberate. A hardcoded R^2 mod p is 64 hex digits nobody will ever check
 * by eye, and this project has already lost four months to one unverifiable
 * sentence about memory layout.
 *
 * The constants that ARE here are checked by the test suite in a way that
 * does not depend on my having typed them correctly: the base point is
 * verified to satisfy the curve equation (which validates p, b, Gx and Gy
 * together, since a slip in any one of them breaks it), and n*G is verified
 * to be infinity (which validates n). The scalar multiplications are then
 * checked against OpenSSL.
 */

#include "net/p256.h"

#include <string.h>

#define S P256_LIMBS

typedef uint32_t fe[S];

/* p = 2^256 - 2^224 + 2^192 + 2^96 - 1, little-endian limbs. */
static const fe P = {
  0xffffffffu, 0xffffffffu, 0xffffffffu, 0x00000000u,
  0x00000000u, 0x00000000u, 0x00000001u, 0xffffffffu
};

/* b, the curve's free coefficient (a is -3 and is never stored). */
static const fe B = {
  0x27d2604bu, 0x3bce3c3eu, 0xcc53b0f6u, 0x651d06b0u,
  0x769886bcu, 0xb3ebbd55u, 0xaa3a93e7u, 0x5ac635d8u
};

/* The base point G. */
static const fe GX = {
  0xd898c296u, 0xf4a13945u, 0x2deb33a0u, 0x77037d81u,
  0x63a440f2u, 0xf8bce6e5u, 0xe12c4247u, 0x6b17d1f2u
};
static const fe GY = {
  0x37bf51f5u, 0xcbb64068u, 0x6b315eceu, 0x2bce3357u,
  0x7c0f9e16u, 0x8ee7eb4au, 0xfe1a7f9bu, 0x4fe342e2u
};

/* n, the order of G. */
static const fe N = {
  0xfc632551u, 0xf3b9cac2u, 0xa7179e84u, 0xbce6faadu,
  0xffffffffu, 0xffffffffu, 0x00000000u, 0xffffffffu
};

/* -p^-1 mod 2^32. p ends in 0xffffffff, so p == -1 mod 2^32, so p^-1 == -1
   and -p^-1 == 1. Written as a named constant rather than folded into the
   loop so the reasoning survives. */
#define N0INV 1u

/* Derived at startup: R = 2^256 mod p and R^2 mod p. */
static fe R1, R2;
/* p-2, for inversion by Fermat, and (p+1)/4, for the square root. */
static fe P_MINUS_2, P_PLUS_1_DIV_4;
static int ready;

/* --- plain limb arithmetic -------------------------------------------- */

static uint32_t limbs_add(fe r, const fe a, const fe b)
{
  uint64_t c = 0;
  int i;
  for (i = 0; i < S; i++) {
    c += (uint64_t)a[i] + b[i];
    r[i] = (uint32_t)c;
    c >>= 32;
  }
  return (uint32_t)c;
}

static uint32_t limbs_sub(fe r, const fe a, const fe b)
{
  uint64_t borrow = 0;
  int i;
  for (i = 0; i < S; i++) {
    uint64_t d = (uint64_t)a[i] - b[i] - borrow;
    r[i] = (uint32_t)d;
    borrow = (d >> 32) & 1;
  }
  return (uint32_t)borrow;
}

/* r = a if flag == 0, b if flag == 1. No branch on flag. */
static void limbs_cmov(fe r, const fe a, const fe b, uint32_t flag)
{
  uint32_t m = (uint32_t)0 - (flag & 1), i;
  for (i = 0; i < S; i++)
    r[i] = (a[i] & ~m) | (b[i] & m);
}

static int limbs_is_zero(const fe a)
{
  uint32_t acc = 0;
  int i;
  for (i = 0; i < S; i++) acc |= a[i];
  return acc == 0;
}

static int limbs_eq(const fe a, const fe b)
{
  uint32_t acc = 0;
  int i;
  for (i = 0; i < S; i++) acc |= a[i] ^ b[i];
  return acc == 0;
}

/* -1 if a < b, 0 if equal, 1 if a > b. */
static int limbs_cmp(const fe a, const fe b)
{
  int i;
  for (i = S - 1; i >= 0; i--) {
    if (a[i] != b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

static int bit_of(const fe a, int i)
{
  return (a[i >> 5] >> (i & 31)) & 1;
}

/* --- field arithmetic mod p ------------------------------------------- */

static void fe_add(fe r, const fe a, const fe b)
{
  fe t;
  uint32_t carry = limbs_add(r, a, b);
  /* One conditional subtraction: inputs are < p, so the sum is < 2p. */
  uint32_t borrow = limbs_sub(t, r, P);
  limbs_cmov(r, r, t, carry | (borrow ^ 1));
}

static void fe_sub(fe r, const fe a, const fe b)
{
  fe t;
  uint32_t borrow = limbs_sub(r, a, b);
  limbs_add(t, r, P);
  limbs_cmov(r, r, t, borrow);
}

static void fe_dbl(fe r, const fe a) { fe_add(r, a, a); }

/* Montgomery multiplication, CIOS. r = a*b*R^-1 mod p. */
static void fe_mul(fe r, const fe a, const fe b)
{
  uint32_t t[S + 2];
  int i, j;

  memset(t, 0, sizeof t);
  for (i = 0; i < S; i++) {
    uint64_t c = 0, s;
    uint32_t m;
    for (j = 0; j < S; j++) {
      s = (uint64_t)t[j] + (uint64_t)a[j] * b[i] + c;
      t[j] = (uint32_t)s;
      c = s >> 32;
    }
    s = (uint64_t)t[S] + c;
    t[S] = (uint32_t)s;
    t[S + 1] = (uint32_t)(s >> 32);

    m = t[0] * N0INV;
    c = 0;
    s = (uint64_t)t[0] + (uint64_t)m * P[0];
    c = s >> 32;
    for (j = 1; j < S; j++) {
      s = (uint64_t)t[j] + (uint64_t)m * P[j] + c;
      t[j - 1] = (uint32_t)s;
      c = s >> 32;
    }
    s = (uint64_t)t[S] + c;
    t[S - 1] = (uint32_t)s;
    t[S] = t[S + 1] + (uint32_t)(s >> 32);
  }

  {
    fe u, v;
    uint32_t borrow;
    for (i = 0; i < S; i++) u[i] = t[i];
    borrow = limbs_sub(v, u, P);
    /* t[S] is the top word: if it is non-zero the value is >= 2^256 > p. */
    limbs_cmov(r, u, v, (uint32_t)(t[S] != 0) | (borrow ^ 1));
  }
}

static void fe_sqr(fe r, const fe a) { fe_mul(r, a, a); }

static void fe_set_u32(fe r, uint32_t v)
{
  memset(r, 0, sizeof(fe));
  r[0] = v;
}

/* r = a^e mod p, square-and-multiply, most significant bit first. */
static void fe_pow(fe r, const fe a, const fe e, p256_tick tick)
{
  fe acc;
  int i;
  memcpy(acc, R1, sizeof acc);           /* 1 in Montgomery form */
  for (i = 256 - 1; i >= 0; i--) {
    fe t;
    fe_sqr(acc, acc);
    fe_mul(t, acc, a);
    limbs_cmov(acc, acc, t, (uint32_t)bit_of(e, i));
    if (tick && (i & 15) == 0) tick();
  }
  memcpy(r, acc, sizeof(fe));
}

static void fe_inv(fe r, const fe a, p256_tick tick)
{
  fe_pow(r, a, P_MINUS_2, tick);
}

/* --- startup --------------------------------------------------------- */

static void p256_ready(void)
{
  fe two;
  int i;

  if (ready) return;

  /* R = 2^256 mod p. p > 2^255, so this is simply 2^256 - p, which is what
     a borrow-discarding subtraction from zero gives. */
  memset(R1, 0, sizeof R1);
  limbs_sub(R1, R1, P);

  /* R^2 mod p = R * 2^256 mod p: 256 doublings, no multiplication needed and
     therefore no chicken-and-egg with Montgomery form. */
  memcpy(R2, R1, sizeof R2);
  for (i = 0; i < 256; i++) fe_dbl(R2, R2);

  fe_set_u32(two, 2);
  limbs_sub(P_MINUS_2, P, two);

  /* (p+1)/4. p+1 does not overflow: p < 2^256 - 1. */
  {
    fe one;
    uint32_t j;
    fe_set_u32(one, 1);
    limbs_add(P_PLUS_1_DIV_4, P, one);
    for (j = 0; j < 2; j++) {
      int k;
      for (k = 0; k < S - 1; k++)
        P_PLUS_1_DIV_4[k] = (P_PLUS_1_DIV_4[k] >> 1) |
                            (P_PLUS_1_DIV_4[k + 1] << 31);
      P_PLUS_1_DIV_4[S - 1] >>= 1;
    }
  }

  ready = 1;
}

static void to_mont(fe r, const fe a) { fe_mul(r, a, R2); }
static void from_mont(fe r, const fe a)
{
  fe one;
  fe_set_u32(one, 1);
  fe_mul(r, a, one);
}

/* --- bytes <-> limbs (big-endian on the wire) ------------------------- */

static void fe_from_bytes(fe r, const uint8_t in[P256_BYTES])
{
  int i;
  for (i = 0; i < S; i++) {
    const uint8_t *q = in + (S - 1 - i) * 4;
    r[i] = ((uint32_t)q[0] << 24) | ((uint32_t)q[1] << 16) |
           ((uint32_t)q[2] << 8) | q[3];
  }
}

static void fe_to_bytes(uint8_t out[P256_BYTES], const fe a)
{
  int i;
  for (i = 0; i < S; i++) {
    uint8_t *q = out + (S - 1 - i) * 4;
    q[0] = (uint8_t)(a[i] >> 24);
    q[1] = (uint8_t)(a[i] >> 16);
    q[2] = (uint8_t)(a[i] >> 8);
    q[3] = (uint8_t)a[i];
  }
}

/* --- curve arithmetic, Jacobian (X : Y : Z), Montgomery form ---------- */

typedef struct { fe x, y, z; } jac;

static int jac_is_inf(const jac *q) { return limbs_is_zero(q->z); }

/* The a = -3 doubling (dbl-2001-b). */
static void jac_dbl(jac *r, const jac *q)
{
  fe delta, gamma, beta, alpha, t0, t1;

  if (jac_is_inf(q)) { memcpy(r, q, sizeof *r); return; }

  fe_sqr(delta, q->z);
  fe_sqr(gamma, q->y);
  fe_mul(beta, q->x, gamma);

  fe_sub(t0, q->x, delta);
  fe_add(t1, q->x, delta);
  fe_mul(alpha, t0, t1);
  fe_dbl(t0, alpha);
  fe_add(alpha, t0, alpha);             /* alpha = 3*(x-d)*(x+d) */

  fe_sqr(t0, alpha);
  fe_dbl(t1, beta);
  fe_dbl(t1, t1);
  fe_dbl(t1, t1);                       /* 8*beta */
  fe_sub(r->x, t0, t1);

  fe_add(t0, q->y, q->z);
  fe_sqr(t0, t0);
  fe_sub(t0, t0, gamma);
  fe_sub(r->z, t0, delta);

  fe_dbl(t0, beta);
  fe_dbl(t0, t0);                       /* 4*beta */
  fe_sub(t0, t0, r->x);
  fe_mul(t0, alpha, t0);
  fe_sqr(t1, gamma);
  fe_dbl(t1, t1);
  fe_dbl(t1, t1);
  fe_dbl(t1, t1);                       /* 8*gamma^2 */
  fe_sub(r->y, t0, t1);
}

/* Jacobian + affine (madd-2007-bl). `bx`,`by` are Montgomery-form affine.
 *
 * The exceptional cases are handled by branching, which means this is not
 * constant time when the inputs happen to be equal or opposite. For a random
 * scalar that never happens; it is documented rather than hidden, and the
 * scalar loop below is written so that the branch cannot be reached from the
 * bits of the scalar alone. */
static void jac_add_affine(jac *r, const jac *q, const fe bx, const fe by)
{
  fe z1z1, u2, s2, h, hh, i_, j_, rr, v, t0, t1;

  if (jac_is_inf(q)) {
    memcpy(r->x, bx, sizeof(fe));
    memcpy(r->y, by, sizeof(fe));
    memcpy(r->z, R1, sizeof(fe));       /* z = 1 */
    return;
  }

  fe_sqr(z1z1, q->z);
  fe_mul(u2, bx, z1z1);
  fe_mul(s2, by, q->z);
  fe_mul(s2, s2, z1z1);

  fe_sub(h, u2, q->x);
  fe_sub(rr, s2, q->y);

  if (limbs_is_zero(h)) {
    if (limbs_is_zero(rr)) { jac_dbl(r, q); return; }
    memset(r, 0, sizeof *r);            /* opposite points: infinity */
    return;
  }

  fe_sqr(hh, h);
  fe_dbl(i_, hh);
  fe_dbl(i_, i_);                       /* 4*hh */
  fe_mul(j_, h, i_);
  fe_dbl(rr, rr);                       /* 2*(s2-y1) */
  fe_mul(v, q->x, i_);

  fe_sqr(t0, rr);
  fe_sub(t0, t0, j_);
  fe_dbl(t1, v);
  fe_sub(r->x, t0, t1);

  fe_sub(t0, v, r->x);
  fe_mul(t0, rr, t0);
  fe_mul(t1, q->y, j_);
  fe_dbl(t1, t1);
  fe_sub(r->y, t0, t1);

  fe_add(t0, q->z, h);
  fe_sqr(t0, t0);
  fe_sub(t0, t0, z1z1);
  fe_sub(r->z, t0, hh);
}

static int jac_to_affine(p256_point *out, const jac *q, p256_tick tick)
{
  fe zinv, zinv2, zinv3, x, y;

  if (jac_is_inf(q)) return 0;

  fe_inv(zinv, q->z, tick);
  fe_sqr(zinv2, zinv);
  fe_mul(zinv3, zinv2, zinv);
  fe_mul(x, q->x, zinv2);
  fe_mul(y, q->y, zinv3);
  from_mont(x, x);
  from_mont(y, y);
  fe_to_bytes(out->x, x);
  fe_to_bytes(out->y, y);
  return 1;
}

/* y^2 == x^3 - 3x + b, all in Montgomery form. */
static int on_curve(const fe x, const fe y)
{
  fe lhs, rhs, t, three, bm;

  fe_sqr(lhs, y);
  fe_sqr(rhs, x);
  fe_mul(rhs, rhs, x);                  /* x^3 */
  fe_set_u32(three, 3);
  to_mont(three, three);
  fe_mul(t, three, x);
  fe_sub(rhs, rhs, t);                  /* -3x */
  memcpy(bm, B, sizeof bm);
  to_mont(bm, bm);
  fe_add(rhs, rhs, bm);
  return limbs_eq(lhs, rhs);
}

/* --- public ---------------------------------------------------------- */

int p256_point_valid(const p256_point *pt)
{
  fe x, y;
  p256_ready();
  fe_from_bytes(x, pt->x);
  fe_from_bytes(y, pt->y);
  if (limbs_cmp(x, P) >= 0 || limbs_cmp(y, P) >= 0) return 0;
  if (limbs_is_zero(x) && limbs_is_zero(y)) return 0;
  to_mont(x, x);
  to_mont(y, y);
  return on_curve(x, y);
}

int p256_mul(p256_point *out, const uint8_t k[P256_BYTES],
             const p256_point *pt, p256_tick tick)
{
  fe scalar, bx, by;
  jac acc;
  int i;

  p256_ready();
  if (!p256_point_valid(pt)) return 0;

  fe_from_bytes(scalar, k);
  if (limbs_is_zero(scalar) || limbs_cmp(scalar, N) >= 0) return 0;

  fe_from_bytes(bx, pt->x);
  fe_from_bytes(by, pt->y);
  to_mont(bx, bx);
  to_mont(by, by);

  memset(&acc, 0, sizeof acc);          /* infinity */
  for (i = 256 - 1; i >= 0; i--) {
    jac sum;
    jac_dbl(&acc, &acc);
    jac_add_affine(&sum, &acc, bx, by);
    limbs_cmov(acc.x, acc.x, sum.x, (uint32_t)bit_of(scalar, i));
    limbs_cmov(acc.y, acc.y, sum.y, (uint32_t)bit_of(scalar, i));
    limbs_cmov(acc.z, acc.z, sum.z, (uint32_t)bit_of(scalar, i));
    if (tick) tick();
  }
  return jac_to_affine(out, &acc, tick);
}

int p256_mul_base(p256_point *out, const uint8_t k[P256_BYTES],
                  p256_tick tick)
{
  p256_point g;
  p256_get_base(&g);
  return p256_mul(out, k, &g, tick);
}

int p256_add(p256_point *out, const p256_point *a, const p256_point *b)
{
  fe bx, by;
  jac q;

  p256_ready();
  if (!p256_point_valid(a) || !p256_point_valid(b)) return 0;

  fe_from_bytes(q.x, a->x);
  fe_from_bytes(q.y, a->y);
  to_mont(q.x, q.x);
  to_mont(q.y, q.y);
  memcpy(q.z, R1, sizeof q.z);

  fe_from_bytes(bx, b->x);
  fe_from_bytes(by, b->y);
  to_mont(bx, bx);
  to_mont(by, by);

  jac_add_affine(&q, &q, bx, by);
  return jac_to_affine(out, &q, (p256_tick)0);
}

int p256_decompress(p256_point *out, const uint8_t x[P256_BYTES],
                    int y_bit, p256_tick tick)
{
  fe xm, rhs, y, t, three, bm, chk;

  p256_ready();
  fe_from_bytes(xm, x);
  if (limbs_cmp(xm, P) >= 0) return 0;
  to_mont(xm, xm);

  fe_sqr(rhs, xm);
  fe_mul(rhs, rhs, xm);
  fe_set_u32(three, 3);
  to_mont(three, three);
  fe_mul(t, three, xm);
  fe_sub(rhs, rhs, t);
  memcpy(bm, B, sizeof bm);
  to_mont(bm, bm);
  fe_add(rhs, rhs, bm);

  /* p == 3 mod 4, so a square root is a^((p+1)/4) -- and squaring it back is
     the only way to know whether rhs was a square at all. */
  fe_pow(y, rhs, P_PLUS_1_DIV_4, tick);
  fe_sqr(chk, y);
  if (!limbs_eq(chk, rhs)) return 0;

  from_mont(t, y);
  if ((int)(t[0] & 1) != (y_bit & 1)) {
    fe zero;
    memset(zero, 0, sizeof zero);
    fe_sub(t, zero, t);                 /* p - y, in plain form */
  }
  memcpy(out->x, x, P256_BYTES);
  fe_to_bytes(out->y, t);
  return 1;
}

void p256_negate(p256_point *pt)
{
  fe y, zero;
  p256_ready();
  fe_from_bytes(y, pt->y);
  if (limbs_is_zero(y)) return;
  memset(zero, 0, sizeof zero);
  fe_sub(y, zero, y);
  fe_to_bytes(pt->y, y);
}

void p256_scalar_add(uint8_t out[P256_BYTES], const uint8_t a[P256_BYTES],
                     const uint8_t b[P256_BYTES])
{
  fe x, y, t;
  uint32_t carry, borrow;

  p256_ready();
  fe_from_bytes(x, a);
  fe_from_bytes(y, b);
  carry = limbs_add(x, x, y);
  /* mod n, not mod p: one conditional subtraction is enough because both
     inputs are below n, so the sum is below 2n. */
  borrow = limbs_sub(t, x, N);
  limbs_cmov(x, x, t, carry | (borrow ^ 1));
  fe_to_bytes(out, x);
}

int p256_scalar_valid(const uint8_t k[P256_BYTES])
{
  fe x, one;
  p256_ready();
  fe_from_bytes(x, k);
  fe_set_u32(one, 1);
  return limbs_cmp(x, one) > 0 && limbs_cmp(x, N) < 0;
}

int p256_below_p(const uint8_t v[P256_BYTES])
{
  fe x;
  p256_ready();
  fe_from_bytes(x, v);
  return limbs_cmp(x, P) < 0;
}

void p256_get_p(uint8_t out[P256_BYTES]) { p256_ready(); fe_to_bytes(out, P); }
void p256_get_n(uint8_t out[P256_BYTES]) { p256_ready(); fe_to_bytes(out, N); }

void p256_get_base(p256_point *out)
{
  p256_ready();
  fe_to_bytes(out->x, GX);
  fe_to_bytes(out->y, GY);
}
