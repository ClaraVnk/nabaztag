/**
 * @file sha256.h
 * @brief SHA-256, HMAC-SHA-256 and 802.11's KDF, for WPA3-SAE.
 *
 * This tree already has MD5 and SHA-1 in `src/net/hash.c`, which is what
 * WPA2 needs. SAE needs SHA-256: for the hash-to-element password
 * derivation, for the confirm exchange, and for the KDF that turns the
 * shared secret into a PMK. It lives here rather than next to the others
 * because everything in `crypto/` is compiled and tested on a build
 * machine, and a hash with no test is a hash nobody should trust.
 *
 * ~1.8 KB of ARM7TDMI flash with no tables -- the round constants are the
 * only data, and the message schedule is computed in place rather than
 * kept as 64 words, which costs a few cycles and saves 192 bytes of stack
 * on a chip with 16 KB of RAM.
 */
#ifndef NAB_SHA256_H
#define NAB_SHA256_H

#include <stdint.h>

#define SHA256_DIGEST 32
#define SHA256_BLOCK  64

typedef struct {
  uint32_t h[8];
  uint8_t  buf[SHA256_BLOCK];
  uint32_t buflen;
  uint32_t total_lo, total_hi;   /* message length in bits */
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const uint8_t *data, uint32_t len);
void sha256_final(sha256_ctx *c, uint8_t out[SHA256_DIGEST]);

/** One-shot. */
void sha256(uint8_t out[SHA256_DIGEST], const uint8_t *data, uint32_t len);

/** HMAC-SHA-256 (RFC 2104). Any key length. */
void hmac_sha256(uint8_t out[SHA256_DIGEST],
                 const uint8_t *key, uint32_t keylen,
                 const uint8_t *data, uint32_t datalen);

/**
 * @brief HMAC-SHA-256 over several pieces, without joining them first.
 *
 * SAE hashes concatenations of things that live in different places --
 * two MAC addresses, two scalars, two field elements -- and a 16 KB RAM has
 * no room to copy them into one buffer. Up to 8 pieces; NULL pieces with
 * zero length are skipped.
 */
void hmac_sha256_v(uint8_t out[SHA256_DIGEST],
                   const uint8_t *key, uint32_t keylen,
                   const uint8_t *const *pieces, const uint32_t *lens,
                   int n);

/**
 * @brief 802.11 KDF-Hash-Length, the counter-mode KDF (IEEE 802.11-2020
 *        12.7.1.6.2), with HMAC-SHA-256.
 *
 * out = HMAC(key, i || label || context || length) for i = 1, 2, ...,
 * truncated to `outlen` bytes. `length` is the output length in *bits*,
 * little-endian, and so is the counter -- both are easy to get backwards,
 * which is why the test for this one compares against an implementation
 * written separately from the same paragraph.
 */
void sha256_kdf(uint8_t *out, uint32_t outlen,
                const uint8_t *key, uint32_t keylen,
                const char *label,
                const uint8_t *context, uint32_t ctxlen);

#endif /* NAB_SHA256_H */
