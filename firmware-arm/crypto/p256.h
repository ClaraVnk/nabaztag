/**
 * @file p256.h
 * @brief NIST P-256 arithmetic, sized for a 48 MHz ARM7TDMI.
 *
 * Written for WPA3-SAE, which mandates group 19 (P-256). Nothing here knows
 * about SAE: it is field and curve arithmetic, and it is testable on a build
 * machine against OpenSSL, which is the whole reason it comes first.
 *
 * Two things shape the design, and both come from the rabbit rather than
 * from the mathematics:
 *
 *  - **The watchdog is armed at about 2.09 s.** A scalar multiplication here
 *    takes long enough that it has to be able to pet it. Every long loop
 *    takes a `tick` callback and calls it often enough that CLR_WDT lands
 *    well inside the window. Pass NULL on a host.
 *  - **Flash is the tight budget.** No precomputed tables, no window
 *    methods, no separate code path for the base point. The only constants
 *    are the curve's own, and even the Montgomery radix is derived at
 *    runtime rather than written down -- a constant nobody can check by
 *    eye is a constant that can be wrong for years.
 *
 * Representation: 8 little-endian 32-bit limbs, Montgomery form inside,
 * plain big-endian bytes at the edges. Montgomery rather than the usual
 * P-256 Solinas reduction on purpose: the special-prime reduction is maybe
 * twice as fast and far easier to get subtly wrong, and `-p^-1 mod 2^32` is
 * 1 for this prime, which makes the inner loop cheap enough.
 */
#ifndef NAB_P256_H
#define NAB_P256_H

#include <stdint.h>

#define P256_LIMBS 8
#define P256_BYTES 32

/** Called periodically inside long loops so the caller can clear the
 *  watchdog. May be NULL. */
typedef void (*p256_tick)(void);

/** An affine point, big-endian bytes. Infinity is not representable: the
 *  functions below report it in their return value instead. */
typedef struct {
  uint8_t x[P256_BYTES];
  uint8_t y[P256_BYTES];
} p256_point;

/** 1 if the point is on the curve and not infinity, 0 otherwise. Rejects
 *  the identity and any coordinate >= p, which is what a peer-supplied
 *  element has to survive before it is used (SAE's validation step). */
int p256_point_valid(const p256_point *pt);

/** out = k * pt. Returns 1 on success, 0 if k is zero or >= n, if pt is not
 *  a valid point, or if the result is infinity. */
int p256_mul(p256_point *out, const uint8_t k[P256_BYTES],
             const p256_point *pt, p256_tick tick);

/** out = k * G. Same contract. */
int p256_mul_base(p256_point *out, const uint8_t k[P256_BYTES],
                  p256_tick tick);

/** out = a + b, affine. Returns 1 on success, 0 if the sum is infinity
 *  (b == -a) or either input is invalid. */
int p256_add(p256_point *out, const p256_point *a, const p256_point *b);

/** The curve's own constants, big-endian, for callers that need them (SAE
 *  needs p and n to reduce and to range-check). */
void p256_get_p(uint8_t out[P256_BYTES]);
void p256_get_n(uint8_t out[P256_BYTES]);
void p256_get_base(p256_point *out);

/** Solve y^2 = x^3 - 3x + b for y, picking the root whose least significant
 *  bit is `y_bit`. Returns 1 if x is on the curve, 0 if no root exists.
 *  SAE-H2E needs this to turn a password element's x into a point. */
int p256_decompress(p256_point *out, const uint8_t x[P256_BYTES],
                    int y_bit, p256_tick tick);

#endif /* NAB_P256_H */
