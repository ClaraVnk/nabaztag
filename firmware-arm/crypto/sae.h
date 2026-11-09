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
 * on a build machine, and it is tested there -- since 2026-11-08 against
 * the vectors of IEEE 802.11-2020 Annex J.10 and RFC 9380 J.1.1, not only
 * against itself.
 *
 * ## Two ways to the password element
 *
 * SAE turns the password into a curve point, the PWE, and everything else
 * is arithmetic on it. The standard has two derivations and this file does
 * both; the caller picks, from what the access point advertises:
 *
 *  - **Hunting and pecking** (12.4.4.2.2, `sae_commit`): hash the password
 *    and the two MAC addresses with a counter until the result is the x of
 *    a point. The original method, the only one an AP from before 2019
 *    knows, and the basis of the Dragonblood timing attacks: the loop here
 *    always runs its full SAE_HNP_ROUNDS and keeps the first hit, so the
 *    number of rounds does not depend on the password, but the arithmetic
 *    inside each round still does. Better than WPA2, weaker than H2E.
 *
 *  - **Hash-to-element** (12.4.4.2.3, `sae_derive_pt` then `sae_commit_pt`):
 *    the password and SSID are mapped to a point PT once, by the Simplified
 *    SWU map, with no data-dependent control flow; the PWE is then a fixed
 *    scalar multiple of PT per pair of MAC addresses. An AP advertises it
 *    with bit 5 of its RSN Extension element and some (hostapd's
 *    `sae_pwe=1`) accept nothing else. PT depends on the SSID and the
 *    password only, so the caller computes it once and keeps it -- 64
 *    bytes -- across associations; what is left per association is one
 *    scalar multiplication.
 *
 * On the wire the two differ in the Authentication frame's status code --
 * 0 for hunting and pecking, 126 (SAE_HASH_TO_ELEMENT) for H2E, in both
 * directions -- in where an anti-clogging token goes, and in the RSNXE the
 * station then carries in its association request and in message 2 of the
 * four-way handshake. `sae_ctx.h2e` records which one a context is.
 */
#ifndef NAB_SAE_H
#define NAB_SAE_H

#include <stdint.h>

#include "net/p256.h"
#include "net/sha256.h"

/** How many hunting-and-pecking rounds. The standard's own recommendation
 *  is at least 40: the chance that 40 rounds all miss is about 2^-40. */
#define SAE_HNP_ROUNDS 40

/** The status code of an H2E commit, both directions (802.11-2020 Table
 *  9-50, SAE_HASH_TO_ELEMENT). A hunting-and-pecking commit carries 0. */
#define SAE_STATUS_H2E 126

/** The RSN Extension element (802.11-2020 9.4.2.241): element ID 244, one
 *  octet of capabilities in which bit 5 is "SAE hash-to-element". The AP
 *  advertises it in beacons and probe responses; a station that used H2E
 *  repeats it in its association request and in EAPOL message 2. */
#define SAE_RSNXE_ID      244
#define SAE_RSNXE_H2E_BIT 0x20
/** The whole element a station sends: ID, length 1, the capability byte. */
#define SAE_RSNXE_LEN     3

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
  /* 1 if the PWE came from a PT (hash-to-element), 0 if it was hunted and
     pecked. Decides the status code and the token framing. */
  uint8_t h2e;

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
 * @brief Derive the password element by hunting and pecking and build our
 *        commit.
 *
 * Costs SAE_HNP_ROUNDS KDF calls and square roots plus one scalar
 * multiplication; `tick` is called throughout so the watchdog survives it.
 *
 * @return 1 on success, 0 if the password yields no point in
 *         SAE_HNP_ROUNDS rounds (about 2^-40) or the RNG produced an
 *         unusable scalar.
 */
int sae_commit(sae_ctx *c,
               const uint8_t own_mac[6], const uint8_t peer_mac[6],
               const char *password,
               sae_rng rng, void *rng_arg, p256_tick tick);

/**
 * @brief The hash-to-element password token PT (802.11-2020 12.4.4.2.3).
 *
 *   pwd-seed  = HKDF-Extract(ssid, password [|| identifier])
 *   u1        = HKDF-Expand(pwd-seed, "SAE Hash to Element u1 P1", 48) mod p
 *   u2        = HKDF-Expand(pwd-seed, "SAE Hash to Element u2 P2", 48) mod p
 *   PT        = SSWU(u1) + SSWU(u2)
 *
 * Depends on nothing per association, so compute it once per (SSID,
 * password) and keep it. `identifier` is the optional SAE password
 * identifier; a home network has none, pass NULL. Costs two SSWU maps
 * (three exponentiations each) and one point addition.
 *
 * @return 1 on success, 0 if the two points cancel (probability 2^-256).
 */
int sae_derive_pt(p256_point *pt,
                  const uint8_t *ssid, uint32_t ssid_len,
                  const char *password, const char *identifier,
                  p256_tick tick);

/**
 * @brief The PWE from a PT and the two addresses (12.4.5.2), then our
 *        commit, exactly as sae_commit builds it.
 *
 *   val = HMAC-SHA-256(0^32, MAX(mac) || MIN(mac)) mod (n - 1) + 1
 *   PWE = val * PT
 *
 * Costs two scalar multiplications. The context is marked `h2e`, so the
 * caller sends the commit with status SAE_STATUS_H2E.
 */
int sae_commit_pt(sae_ctx *c,
                  const uint8_t own_mac[6], const uint8_t peer_mac[6],
                  const p256_point *pt,
                  sae_rng rng, void *rng_arg, p256_tick tick);

/** Our commit, as it goes on the wire: 2-byte group (19, little-endian),
 *  then the 32-byte scalar, then the 64-byte element. 98 bytes. */
#define SAE_COMMIT_LEN (2 + P256_BYTES + 2 * P256_BYTES)
void sae_commit_bytes(const sae_ctx *c, uint8_t out[SAE_COMMIT_LEN]);

/** The longest anti-clogging token we will echo back. The standard does not
 *  fix a length -- it is whatever the AP chose -- and 256 is far beyond what
 *  any implementation sends. */
#define SAE_TOKEN_MAX 256
#define SAE_COMMIT_TOKEN_LEN (SAE_COMMIT_LEN + 3 + SAE_TOKEN_MAX)

/**
 * @brief Our commit with an anti-clogging token spliced in.
 *
 * An access point under load answers a commit with status 76 and a token,
 * and expects the commit again with that token echoed. It is not an error
 * and it is not optional: a client that treats status 76 as a refusal
 * cannot associate with a busy AP at all.
 *
 * Hunting and pecking puts the token between the group and the scalar
 * (802.11-2020 12.4.7.4). With H2E it goes *after* the element, inside an
 * Anti-Clogging Token Container element: ID 255, length, extension ID 93,
 * the token (9.4.2.244; hostapd's sae_write_commit does the same). The
 * context's `h2e` picks. Returns the number of bytes written.
 */
uint32_t sae_commit_bytes_token(const sae_ctx *c, uint8_t *out,
                                const uint8_t *token, uint32_t tlen);

/** The extension element IDs the H2E commit may carry after the element.
 *  Only the token container is ever sent by this code; a peer's Rejected
 *  Groups or Password Identifier element is skipped, not parsed. */
#define SAE_EID_EXTENSION      255
#define SAE_EID_EXT_TOKEN       93

/**
 * @brief Take the peer's commit and derive KCK, PMK and PMKID.
 *
 * Validates the group, that the scalar is in (1, n) and that the element is
 * a point on the curve -- the checks that stop invalid-curve and
 * small-subgroup attacks, and a reflection of our own commit. Anything
 * after the element (H2E's optional elements) is ignored.
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
