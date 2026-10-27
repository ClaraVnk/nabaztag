/**
 * @file test_p256.c
 * @brief P-256 against OpenSSL, and against the curve itself.
 *
 * Two independent kinds of check, because each catches what the other
 * cannot:
 *
 *  - **Against OpenSSL** (vectors_p256.h). Catches a wrong result. Note
 *    that the first base vector is k = 1, so this also validates the base
 *    point compiled into p256.c against OpenSSL's -- the constants are not
 *    taken on trust.
 *  - **Against the curve's own algebra.** Catches a consistently wrong
 *    implementation that a vector set might not reach: that (k1+k2)*G is
 *    k1*G + k2*G, that (n-1)*G is -G, that a decompressed point squares
 *    back. These need no external data and would survive my having
 *    mistyped every vector.
 *
 * Plus the timing, which is not decoration: the rabbit's watchdog fires at
 * about 2.09 s and a scalar multiplication here is the longest single
 * computation this firmware would ever do.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "p256.h"
#include "vectors_p256.h"

static int failures;
static long ticks;

static void count_tick(void) { ticks++; }

static void check(int ok, const char *what)
{
  printf("  %-54s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) failures++;
}

static int same(const uint8_t *a, const uint8_t *b)
{
  return memcmp(a, b, 32) == 0;
}

static void dump(const char *label, const uint8_t *v)
{
  int i;
  printf("    %s ", label);
  for (i = 0; i < 32; i++) printf("%02x", v[i]);
  printf("\n");
}

/* --- OpenSSL's answers ------------------------------------------------ */

static void test_base_vectors(void)
{
  size_t i, n = sizeof P256_BASE_VECS / sizeof P256_BASE_VECS[0];
  int all = 1;

  printf("== k*G against OpenSSL (%zu vectors)\n", n);
  for (i = 0; i < n; i++) {
    const struct p256_base_vec *v = &P256_BASE_VECS[i];
    p256_point out;
    if (!p256_mul_base(&out, v->k, 0)) { all = 0; printf("    vector %zu: refused\n", i); continue; }
    if (!same(out.x, v->x) || !same(out.y, v->y)) {
      all = 0;
      printf("    vector %zu mismatch\n", i);
      dump("got  x", out.x); dump("want x", v->x);
      dump("got  y", out.y); dump("want y", v->y);
    }
  }
  check(all, "every k*G matches");
  /* The first vector is k=1, so this says the compiled-in G is OpenSSL's. */
  {
    p256_point g;
    p256_get_base(&g);
    check(same(g.x, P256_BASE_VECS[0].x) && same(g.y, P256_BASE_VECS[0].y),
          "the base point in p256.c is OpenSSL's base point");
  }
}

static void test_point_vectors(void)
{
  size_t i, n = sizeof P256_POINT_VECS / sizeof P256_POINT_VECS[0];
  int all = 1;

  printf("== k*P for a point that is not the generator (%zu vectors)\n", n);
  for (i = 0; i < n; i++) {
    const struct p256_point_vec *v = &P256_POINT_VECS[i];
    p256_point p, out;
    memcpy(p.x, v->px, 32);
    memcpy(p.y, v->py, 32);
    if (!p256_mul(&out, v->k, &p, 0)) { all = 0; printf("    vector %zu: refused\n", i); continue; }
    if (!same(out.x, v->x) || !same(out.y, v->y)) {
      all = 0;
      printf("    vector %zu mismatch\n", i);
      dump("got  x", out.x); dump("want x", v->x);
    }
  }
  check(all, "every k*P matches");
}

/* --- the curve's own algebra ------------------------------------------ */

static void test_algebra(void)
{
  p256_point g, a, b, sum, direct;
  uint8_t k1[32], k2[32], k3[32], p[32], nm1[32];
  int i, borrow;

  printf("== the curve, with no external data\n");
  p256_get_base(&g);
  check(p256_point_valid(&g), "G satisfies y^2 = x^3 - 3x + b");

  /* (n-1)*G == -G, i.e. y == p - Gy. Validates n as compiled in. */
  p256_get_n(nm1);
  nm1[31] -= 1;
  p256_get_p(p);
  check(p256_mul_base(&a, nm1, 0), "(n-1)*G is computable");
  {
    uint8_t negy[32];
    int bw = 0;
    for (i = 31; i >= 0; i--) {
      int d = (int)p[i] - g.y[i] - bw;
      bw = d < 0;
      negy[i] = (uint8_t)(d + (bw ? 256 : 0));
    }
    check(same(a.x, g.x) && same(a.y, negy), "(n-1)*G == -G");
  }

  /* k1*G + k2*G == (k1+k2)*G, with k1+k2 chosen not to wrap n. */
  memset(k1, 0, 32); memset(k2, 0, 32); memset(k3, 0, 32);
  k1[31] = 0x11; k1[30] = 0x22;
  k2[31] = 0x33; k2[30] = 0x44;
  borrow = 0;
  for (i = 31; i >= 0; i--) {
    int s = k1[i] + k2[i] + borrow;
    k3[i] = (uint8_t)s;
    borrow = s >> 8;
  }
  check(p256_mul_base(&a, k1, 0) && p256_mul_base(&b, k2, 0) &&
        p256_add(&sum, &a, &b) && p256_mul_base(&direct, k3, 0) &&
        same(sum.x, direct.x) && same(sum.y, direct.y),
        "k1*G + k2*G == (k1+k2)*G");

  /* Doubling through addition agrees with the scalar path. */
  {
    uint8_t two[32];
    memset(two, 0, 32); two[31] = 2;
    check(p256_add(&sum, &g, &g) && p256_mul_base(&direct, two, 0) &&
          same(sum.x, direct.x) && same(sum.y, direct.y),
          "G + G == 2*G (the add falls through to doubling)");
  }
}

static void test_decompress(void)
{
  p256_point g, got;
  uint8_t bad[32];
  int bit;

  printf("== decompression, which SAE-H2E needs\n");
  p256_get_base(&g);
  bit = g.y[31] & 1;
  check(p256_decompress(&got, g.x, bit, 0) && same(got.y, g.y),
        "G's x and y's parity give G's y back");
  check(p256_decompress(&got, g.x, bit ^ 1, 0) && !same(got.y, g.y) &&
        p256_point_valid(&got),
        "the other parity gives the other root, also on the curve");

  /* An x with no square root must be refused rather than silently wrong.
     x = 1 is not on this curve: 1 - 3 + b is not a quadratic residue. */
  memset(bad, 0, 32); bad[31] = 1;
  check(!p256_decompress(&got, bad, 0, 0), "an x off the curve is refused");
}

static void test_refusals(void)
{
  p256_point g, out, bogus;
  uint8_t k[32];

  printf("== what it refuses\n");
  p256_get_base(&g);

  memset(k, 0, 32);
  check(!p256_mul_base(&out, k, 0), "k = 0 is refused");

  p256_get_n(k);
  check(!p256_mul_base(&out, k, 0), "k = n is refused");

  p256_get_p(k);
  check(!p256_mul_base(&out, k, 0), "k = p is refused (p > n)");

  /* A point that is not on the curve, which is exactly what a hostile peer
     sends to try small-subgroup and invalid-curve attacks. */
  memcpy(&bogus, &g, sizeof bogus);
  bogus.y[31] ^= 1;
  check(!p256_point_valid(&bogus), "a point off the curve is rejected");
  memset(k, 0, 32); k[31] = 5;
  check(!p256_mul(&out, k, &bogus, 0), "and multiplying it is refused");

  /* Coordinates at or above p. */
  memcpy(&bogus, &g, sizeof bogus);
  p256_get_p(bogus.x);
  check(!p256_point_valid(&bogus), "x == p is rejected");
}

/* --- cost ------------------------------------------------------------- */

static void test_cost(void)
{
  p256_point out;
  uint8_t k[32];
  clock_t t0;
  int i, rounds = 20;
  double us, ms_arm;

  printf("== cost\n");
  memset(k, 0, 32);
  memcpy(k, P256_BASE_VECS[sizeof P256_BASE_VECS / sizeof P256_BASE_VECS[0] - 1].k, 32);

  ticks = 0;
  t0 = clock();
  for (i = 0; i < rounds; i++)
    if (!p256_mul_base(&out, k, count_tick)) failures++;
  us = (double)(clock() - t0) / CLOCKS_PER_SEC * 1e6 / rounds;

  printf("    %.0f us per scalar multiplication on this host\n", us);
  printf("    %ld watchdog ticks per multiplication\n", ticks / rounds);

  /* The rabbit is a 48 MHz ARM7TDMI with a 32x32->32 multiplier and no
     cache, running from internal flash. Against this host -- out-of-order,
     GHz, 64-bit multiplies -- a factor of 150 is the optimistic end of what
     this kind of limb arithmetic loses. Printed as an order of magnitude,
     not a promise: the real number comes from the emulator or a stopwatch
     on the rabbit, and this line exists to say whether we are anywhere
     near the 2.09 s watchdog. */
  ms_arm = us * 150 / 1000.0;
  printf("    order of magnitude on the rabbit: ~%.0f ms (watchdog at 2090 ms)\n",
         ms_arm);
  check(ticks / rounds >= 256, "the watchdog is offered a tick per scalar bit");
}

int main(void)
{
  test_base_vectors();
  test_point_vectors();
  test_algebra();
  test_decompress();
  test_refusals();
  test_cost();

  printf("\n%s\n", failures ? "FAILURES" : "all good");
  return failures ? 1 : 0;
}
