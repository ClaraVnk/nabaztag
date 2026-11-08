/**
 * @file nab_kx.h
 * @brief The per-rabbit key, agreed at adoption.
 *
 * At adoption the rabbit and its warren run one X25519 exchange. Each side
 * derives, from the shared secret and the two public keys, a 32-byte key
 * and a short check value. The key authenticates the bytecode and keys TLS
 * later; the check value is shown on the belly by the rabbit itself and on
 * the warren's HTTPS page, and the owner confirms the two match. A party
 * in the middle of the exchange holds a different secret with each side,
 * so the two check values differ unless it guessed right in one try.
 *
 * The check value is shown by the rabbit's own LEDs and not spoken: the
 * rabbit's voice is audio the warren sends, which a party in the middle
 * could replace.
 *
 * The scalar multiplications are TweetNaCl's crypto_scalarmult, which the
 * x25519_wdt step makes feed the watchdog once per ladder step.
 */
#ifndef _NAB_KX_H_
#define _NAB_KX_H_

#include <stdint.h>

#define NAB_KX_KEY    32
#define NAB_KX_PUB    32
#define NAB_KX_LEDS    5   /* belly LEDs the check value is shown on */
#define NAB_KX_COLORS  8   /* colours per LED: 3 bits each, 15 bits in all */

/**
 * @brief Our key pair: `priv` is 32 random bytes in, clamped in place;
 *        `pub` is X25519(priv, 9).
 */
void nab_kx_keypair(uint8_t pub[NAB_KX_PUB], uint8_t priv[32]);

/**
 * @brief The shared key and check value.
 *
 * `rabbit_pub` and `warren_pub` are named by role, not by "ours" and
 * "theirs", so both sides hash them in the same order.
 *
 * @return 0 if the peer's key gives an all-zero secret (a low-order
 *         point: refused, RFC 7748 6.1), 1 otherwise.
 */
int nab_kx_derive(uint8_t key[NAB_KX_KEY], uint8_t check[NAB_KX_LEDS],
                  const uint8_t priv[32],
                  const uint8_t peer_pub[NAB_KX_PUB],
                  const uint8_t rabbit_pub[NAB_KX_PUB],
                  const uint8_t warren_pub[NAB_KX_PUB]);

/** RFC 5869 HKDF-SHA-256, one Extract and an Expand of up to 255*32 bytes. */
void nab_hkdf(uint8_t *out, uint32_t outlen,
              const uint8_t *salt, uint32_t saltlen,
              const uint8_t *ikm, uint32_t ikmlen,
              const uint8_t *info, uint32_t infolen);

/**
 * @brief HMAC-SHA-256 of a long buffer, calling `tick` every 4 KB.
 *
 * The bytecode is ~106 KB: about 14 M instructions of SHA-256, a second or
 * more on the rabbit, against a 2.09 s watchdog. `tick` may be NULL.
 */
void nab_hmac_long(uint8_t out[32], const uint8_t key[NAB_KX_KEY],
                   const uint8_t *data, uint32_t len, void (*tick)(void));

/** 1 if `mac` is HMAC-SHA-256(key, data), compared in constant time. */
int nab_hmac_check(const uint8_t key[NAB_KX_KEY], const uint8_t *data,
                   uint32_t len, const uint8_t mac[32], void (*tick)(void));

#endif
