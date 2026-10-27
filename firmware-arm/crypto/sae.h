/**
 * @file sae.h
 * @brief WPA3-SAE (802.11-2020 12.4), the cryptographic half.
 *
 * SAE replaces WPA2's pre-shared key with a password-authenticated key
 * exchange: two peers prove they know the same password without either one
 * sending anything an eavesdropper can run a dictionary against. The result
 * is a PMK, and from there the ordinary four-way handshake runs unchanged --
 * which is why this can be added to a 2006 rabbit at all. The RT2573 still
 * does CCMP for data in hardware.
 *
 * This file is the arithmetic and the framing. It does not touch the radio,
 * the 802.11 state machine or any buffer the driver owns, so it is testable
 * on a build machine, and it is tested there.
 *
 * ## Hunting and pecking, not hash-to-element, and why
 *
 * SAE has two ways to turn a password into a curve point. This implements
 * the original one, hunting and pecking (12.4.4.3.2): hash the password with
 * a counter until the result is a valid x coordinate.
 *
 * Hash-to-element (H2E) is better and is what a new implementation should
 * want -- it is constant time by construction, where hunting and pecking is
 * the basis of the Dragonblood timing attacks. It is not here because it
 * needs the Simplified SWU mapping, which is a few hundred more lines whose
 * correctness cannot be checked against anything in this project: there is
 * no library call for it and no vector set to hand. Writing it blind and
 * flashing it would be the opposite of how the rest of this firmware has
 * been built.
 *
 * So: hunting and pecking, with the mitigation that matters most -- the loop
 * always runs its full count of iterations and keeps the first hit, so the
 * number of rounds does not depend on the password. That closes the coarse
 * leak. It does not make the whole thing constant time, and a determined
 * attacker on the same network can still learn something from the fine
 * timing of the field arithmetic. Stated plainly rather than implied:
 * **this is better than WPA2 and weaker than a modern WPA3 client.** H2E is
 * the follow-up, and `p256_decompress` is already there for it.
 */
#ifndef NAB_SAE_H
#define NAB_SAE_H

#include <stdint.h>

#include "p256.h"
#include "sha256.h"

/** How many hunting-and-pecking rounds. The standard's own recommendation
 *  is at least 40: the chance that 40 rounds all miss is about 2^-40. */
#define SAE_HNP_ROUNDS 40

/** Fills `out` with `len` random bytes. The rabbit passes something built
 *  on its millisecond timer and its MAC; the tests pass something
 *  deterministic so a failure can be replayed. */
typedef void (*sae_rng)(uint8_t *out, uint32_t len, void *arg);

typedef enum {
  SAE_NOTHING = 0,
  SAE_COMMITTED,      /* our commit is built and sent */
  SAE_CONFIRMED,      /* the peer's commit is in and the keys are derived */
  SAE_ACCEPTED        /* the peer's confirm verified; the PMK is usable */
} sae_state;

typedef struct {
  sae_state state;

  uint8_t own_mac[6];
  uint8_t peer_mac[6];

  p256_point pwe;

  uint8_t rand[P256_BYTES];
  uint8_t own_scalar[P256_BYTES];
  p256_point own_element;

  uint8_t peer_scalar[P256_BYTES];
  p256_point peer_element;

  uint8_t kck[SHA256_DIGEST];
  uint8_t pmk[SHA256_DIGEST];
  uint8_t pmkid[16];

  uint16_t send_confirm;
} sae_ctx;

/**
 * @brief Derive the password element and build our commit.
 *
 * Costs SAE_HNP_ROUNDS KDF calls plus one scalar multiplication, which is
 * the slow part; `tick` is called throughout so the watchdog survives it.
 *
 * @return 1 on success, 0 if the password yields no point in
 *         SAE_HNP_ROUNDS rounds (about 2^-40) or the RNG produced an
 *         unusable scalar.
 */
int sae_commit(sae_ctx *c,
               const uint8_t own_mac[6], const uint8_t peer_mac[6],
               const char *password,
               sae_rng rng, void *rng_arg, p256_tick tick);

/** Our commit, as it goes on the wire: 2-byte group (19, little-endian),
 *  then the 32-byte scalar, then the 64-byte element. 98 bytes. */
#define SAE_COMMIT_LEN (2 + P256_BYTES + 2 * P256_BYTES)
void sae_commit_bytes(const sae_ctx *c, uint8_t out[SAE_COMMIT_LEN]);

/**
 * @brief Take the peer's commit and derive KCK, PMK and PMKID.
 *
 * Validates the group, that the scalar is in (1, n) and that the element is
 * a point on the curve -- the checks that stop invalid-curve and
 * small-subgroup attacks, and a reflection of our own commit.
 *
 * @return 1 on success, 0 if the peer's commit is unusable.
 */
int sae_peer_commit(sae_ctx *c, const uint8_t *in, uint32_t len,
                    p256_tick tick);

/** Our confirm: the 2-byte send-confirm counter then a 32-byte MIC. */
#define SAE_CONFIRM_LEN (2 + SHA256_DIGEST)
void sae_confirm_bytes(sae_ctx *c, uint8_t out[SAE_CONFIRM_LEN]);

/**
 * @brief Check the peer's confirm.
 * @return 1 if it verifies, in which case the PMK may be used; 0 otherwise,
 *         and the exchange must be abandoned rather than retried with the
 *         same scalar.
 */
int sae_peer_confirm(sae_ctx *c, const uint8_t *in, uint32_t len);

/** Wipe every secret. Call it on failure as well as on success. */
void sae_clear(sae_ctx *c);

#endif /* NAB_SAE_H */
