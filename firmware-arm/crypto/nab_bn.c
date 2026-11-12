/**
 * @file nab_bn.c
 * @brief Montgomery arithmetic for nab_pki.h: RSA (e = 65537, up to 4096
 *        bits), the ECDSA scalar inverse, the P-384 field. 32-bit limbs,
 *        little-endian, CIOS multiplication. Public data only.
 */
#include <string.h>

#include "net/nab_pki.h"

static uint32_t mont_n0(uint32_t n0)
{
  uint32_t x = 1;
  for (int i = 0; i < 5; i++) x *= 2 - n0 * x;   /* Newton: x = n0^-1 mod 2^32 */
  return (uint32_t)0 - x;
}

/* The watchdog (CLR_WDT on the rabbit, see nab_tls.c). A Montgomery
   product of 4096 bits is ~1.7 M instructions in Thumb (no 64-bit multiply):
   between two ticks there must never be many of them, so the arithmetic
   feeds it itself (tools/cost, 2026-11-12: 21.7 M without a tick before). */
extern void nab_verify_tick(void);

void nab_bn_init(nab_bn_ctx *c, const uint8_t *mod, uint32_t len)
{
  int i, j, start;
  while (len > 1 && !*mod) { mod++; len--; }
  c->n = (int)((len + 3) / 4);
  nab_bn_from_bytes(c->m, c->n, mod, len);
  c->n0 = mont_n0(c->m[0]);
  memset(c->rr, 0, sizeof c->rr);
  /* R^2 mod m by doubling. When m fills its top limb (every modulus and
     prime here), R mod m = R - m is m's two's complement, and only 32n
     doublings are left instead of 64n. */
  if (c->m[c->n - 1] & 0x80000000u) {
    for (i = 0; i < c->n; i++) c->rr[i] = ~c->m[i];
    for (i = 0; i < c->n && ++c->rr[i] == 0; i++) {}
    start = 32 * c->n;
  } else {
    c->rr[0] = 1;
    start = 0;
  }
  for (i = start; i < 64 * c->n; i++) {
    uint32_t carry = 0;
    for (j = 0; j < c->n; j++) {
      uint32_t v = c->rr[j];
      c->rr[j] = (v << 1) | carry;
      carry = v >> 31;
    }
    if (carry || nab_bn_cmp(c->rr, c->m, c->n) >= 0) nab_bn_sub(c->rr, c->rr, c->m, c->n);
    if ((i & 127) == 127) nab_verify_tick();
  }
}

void nab_bn_from_bytes(uint32_t *r, int n, const uint8_t *in, uint32_t len)
{
  memset(r, 0, 4 * (uint32_t)n);
  for (uint32_t i = 0; i < len; i++) {
    uint32_t b = len - 1 - i;
    if (b / 4 < (uint32_t)n) r[b / 4] |= (uint32_t)in[i] << (8 * (b % 4));
  }
}

void nab_bn_to_bytes(uint8_t *out, uint32_t len, const uint32_t *a, int n)
{
  for (uint32_t i = 0; i < len; i++) {
    uint32_t b = len - 1 - i;
    out[i] = b / 4 < (uint32_t)n ? (uint8_t)(a[b / 4] >> (8 * (b % 4))) : 0;
  }
}

int nab_bn_cmp(const uint32_t *a, const uint32_t *b, int n)
{
  for (int i = n - 1; i >= 0; i--)
    if (a[i] != b[i]) return a[i] > b[i] ? 1 : -1;
  return 0;
}

uint32_t nab_bn_sub(uint32_t *r, const uint32_t *a, const uint32_t *b, int n)
{
  uint64_t borrow = 0;
  for (int i = 0; i < n; i++) {
    uint64_t d = (uint64_t)a[i] - b[i] - borrow;
    r[i] = (uint32_t)d;
    borrow = (d >> 32) & 1;
  }
  return (uint32_t)borrow;
}

uint32_t nab_bn_add(uint32_t *r, const uint32_t *a, const uint32_t *b, int n)
{
  uint64_t carry = 0;
  for (int i = 0; i < n; i++) {
    uint64_t s = (uint64_t)a[i] + b[i] + carry;
    r[i] = (uint32_t)s;
    carry = s >> 32;
  }
  return (uint32_t)carry;
}

/* r = a * b / R mod m (CIOS). r may alias a or b. */
void nab_bn_mul(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a, const uint32_t *b)
{
  uint32_t t[NAB_BN_LIMBS + 2];
  int n = c->n;
  memset(t, 0, 4 * (uint32_t)(n + 2));
  for (int i = 0; i < n; i++) {
    uint64_t carry = 0;
    for (int j = 0; j < n; j++) {
      uint64_t v = (uint64_t)a[j] * b[i] + t[j] + carry;
      t[j] = (uint32_t)v;
      carry = v >> 32;
    }
    uint64_t v = (uint64_t)t[n] + carry;
    t[n] = (uint32_t)v;
    t[n + 1] = (uint32_t)(v >> 32);
    uint32_t mq = t[0] * c->n0;
    carry = ((uint64_t)mq * c->m[0] + t[0]) >> 32;
    for (int j = 1; j < n; j++) {
      v = (uint64_t)mq * c->m[j] + t[j] + carry;
      t[j - 1] = (uint32_t)v;
      carry = v >> 32;
    }
    v = (uint64_t)t[n] + carry;
    t[n - 1] = (uint32_t)v;
    t[n] = t[n + 1] + (uint32_t)(v >> 32);
    if (n > 32 && (i & 15) == 15) nab_verify_tick();   /* RSA sizes only */
  }
  if (t[n] || nab_bn_cmp(t, c->m, n) >= 0) nab_bn_sub(t, t, c->m, n);
  memcpy(r, t, 4 * (uint32_t)n);
}

void nab_bn_to_mont(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a) { nab_bn_mul(c, r, a, c->rr); }

void nab_bn_from_mont(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a)
{
  uint32_t one[NAB_BN_LIMBS];
  memset(one, 0, 4 * (uint32_t)c->n);
  one[0] = 1;
  nab_bn_mul(c, r, a, one);
}

/* r = a^e mod m, e big-endian bytes; a, r plain (not Montgomery). */
void nab_bn_exp(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a,
                const uint8_t *e, uint32_t elen, nab_pki_tick tick)
{
  uint32_t x[NAB_BN_LIMBS], am[NAB_BN_LIMBS];
  nab_bn_to_mont(c, am, a);
  memset(x, 0, sizeof x);
  x[0] = 1;
  nab_bn_to_mont(c, x, x);
  for (uint32_t i = 0; i < elen; i++)
    for (int bit = 7; bit >= 0; bit--) {
      nab_bn_mul(c, x, x, x);
      if ((e[i] >> bit) & 1) nab_bn_mul(c, x, x, am);
      if (tick) tick();
    }
  nab_bn_from_mont(c, r, x);
}
