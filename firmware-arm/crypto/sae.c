/**
 * @file sae.c
 * @brief WPA3-SAE, 802.11-2020 12.4. See sae.h for the design notes.
 */

#include "sae.h"

#include <string.h>

#define GROUP_19 19

/* max(A,B) || min(A,B) over the two MAC addresses, which is how both peers
   arrive at the same key for the password hash without agreeing who is who. */
static void mac_order(uint8_t out[12], const uint8_t a[6], const uint8_t b[6])
{
  int cmp = memcmp(a, b, 6);
  const uint8_t *hi = cmp >= 0 ? a : b;
  const uint8_t *lo = cmp >= 0 ? b : a;
  memcpy(out, hi, 6);
  memcpy(out + 6, lo, 6);
}

/*
 * Hunting and pecking, 12.4.4.3.2.
 *
 * The loop runs all SAE_HNP_ROUNDS rounds whatever happens and keeps the
 * first hit. That is not decoration: stopping early makes the number of
 * rounds a function of the password, which is the coarse half of
 * Dragonblood. The fine half -- timing inside the field arithmetic -- is
 * not addressed here and cannot be without H2E.
 */
static int derive_pwe(p256_point *pwe, const uint8_t own[6],
                      const uint8_t peer[6], const char *password,
                      p256_tick tick)
{
  uint8_t key[12], p_bytes[P256_BYTES];
  uint8_t seed[SHA256_DIGEST], value[P256_BYTES];
  const uint8_t *pieces[2];
  uint32_t lens[2], pwlen = 0;
  p256_point found;
  int have = 0, round;

  while (password[pwlen]) pwlen++;
  mac_order(key, own, peer);
  p256_get_p(p_bytes);

  for (round = 1; round <= SAE_HNP_ROUNDS; round++) {
    uint8_t counter = (uint8_t)round;
    p256_point cand;

    pieces[0] = (const uint8_t *)password; lens[0] = pwlen;
    pieces[1] = &counter;                  lens[1] = 1;
    hmac_sha256_v(seed, key, sizeof key, pieces, lens, 2);

    /* pwd-value = KDF-256(pwd-seed, "SAE Hunting and Pecking", p) */
    sha256_kdf(value, sizeof value, seed, sizeof seed,
               "SAE Hunting and Pecking", p_bytes, sizeof p_bytes);

    /* A value at or above p is not a field element: reject it rather than
       reduce it, or the two peers can disagree about the point. */
    if (p256_below_p(value) &&
        p256_decompress(&cand, value, seed[SHA256_DIGEST - 1] & 1, tick)) {
      if (!have) {
        memcpy(&found, &cand, sizeof found);
        have = 1;
      }
    }
    if (tick) tick();
  }

  memset(seed, 0, sizeof seed);
  memset(value, 0, sizeof value);
  if (!have) return 0;
  memcpy(pwe, &found, sizeof *pwe);
  return 1;
}

int sae_commit(sae_ctx *c,
               const uint8_t own_mac[6], const uint8_t peer_mac[6],
               const char *password,
               sae_rng rng, void *rng_arg, p256_tick tick)
{
  uint8_t mask[P256_BYTES];
  int tries;

  memset(c, 0, sizeof *c);
  memcpy(c->own_mac, own_mac, 6);
  memcpy(c->peer_mac, peer_mac, 6);

  if (!derive_pwe(&c->pwe, own_mac, peer_mac, password, tick)) return 0;

  /* rand and mask both in (1, n); commit-scalar = (rand + mask) mod n, and
     the standard requires that to be greater than 1 as well. Three tries is
     generous: each one fails with probability about 2^-128. */
  for (tries = 0; tries < 3; tries++) {
    rng(c->rand, sizeof c->rand, rng_arg);
    rng(mask, sizeof mask, rng_arg);
    if (!p256_scalar_valid(c->rand) || !p256_scalar_valid(mask)) continue;
    p256_scalar_add(c->own_scalar, c->rand, mask);
    if (p256_scalar_valid(c->own_scalar)) break;
  }
  if (tries == 3) { sae_clear(c); return 0; }

  /* commit-element = inverse(mask * PWE) */
  if (!p256_mul(&c->own_element, mask, &c->pwe, tick)) {
    memset(mask, 0, sizeof mask);
    sae_clear(c);
    return 0;
  }
  p256_negate(&c->own_element);

  memset(mask, 0, sizeof mask);
  c->state = SAE_COMMITTED;
  return 1;
}

void sae_commit_bytes(const sae_ctx *c, uint8_t out[SAE_COMMIT_LEN])
{
  out[0] = GROUP_19 & 0xff;
  out[1] = (uint8_t)(GROUP_19 >> 8);
  memcpy(out + 2, c->own_scalar, P256_BYTES);
  memcpy(out + 2 + P256_BYTES, c->own_element.x, P256_BYTES);
  memcpy(out + 2 + 2 * P256_BYTES, c->own_element.y, P256_BYTES);
}

int sae_peer_commit(sae_ctx *c, const uint8_t *in, uint32_t len,
                    p256_tick tick)
{
  p256_point k_point, tmp;
  uint8_t k[P256_BYTES], keyseed[SHA256_DIGEST], context[P256_BYTES];
  uint8_t kck_pmk[2 * SHA256_DIGEST], zeros[SHA256_DIGEST];

  if (c->state != SAE_COMMITTED) return 0;
  if (len < SAE_COMMIT_LEN) return 0;
  if (in[0] != (GROUP_19 & 0xff) || in[1] != (uint8_t)(GROUP_19 >> 8)) return 0;

  memcpy(c->peer_scalar, in + 2, P256_BYTES);
  memcpy(c->peer_element.x, in + 2 + P256_BYTES, P256_BYTES);
  memcpy(c->peer_element.y, in + 2 + 2 * P256_BYTES, P256_BYTES);

  /* The three checks that stop a hostile peer: a scalar outside (1, n), an
     element that is not on the curve (invalid-curve and small-subgroup
     attacks start there), and a reflection of our own commit back at us. */
  if (!p256_scalar_valid(c->peer_scalar)) return 0;
  if (!p256_point_valid(&c->peer_element)) return 0;
  if (memcmp(c->peer_scalar, c->own_scalar, P256_BYTES) == 0 &&
      memcmp(&c->peer_element, &c->own_element, sizeof c->own_element) == 0)
    return 0;

  /* K = rand * (peer-scalar * PWE + peer-element), and k is its x. */
  if (!p256_mul(&tmp, c->peer_scalar, &c->pwe, tick)) return 0;
  if (!p256_add(&tmp, &tmp, &c->peer_element)) return 0;
  if (!p256_mul(&k_point, c->rand, &tmp, tick)) return 0;
  memcpy(k, k_point.x, P256_BYTES);

  /* keyseed = HMAC-SHA256(0^32, k) */
  memset(zeros, 0, sizeof zeros);
  hmac_sha256(keyseed, zeros, sizeof zeros, k, sizeof k);

  /* context = (own-scalar + peer-scalar) mod n; KCK || PMK = KDF-512. */
  p256_scalar_add(context, c->own_scalar, c->peer_scalar);
  sha256_kdf(kck_pmk, sizeof kck_pmk, keyseed, sizeof keyseed,
             "SAE KCK and PMK", context, sizeof context);
  memcpy(c->kck, kck_pmk, SHA256_DIGEST);
  memcpy(c->pmk, kck_pmk + SHA256_DIGEST, SHA256_DIGEST);
  memcpy(c->pmkid, context, sizeof c->pmkid);

  memset(k, 0, sizeof k);
  memset(keyseed, 0, sizeof keyseed);
  memset(kck_pmk, 0, sizeof kck_pmk);
  memset(&k_point, 0, sizeof k_point);
  memset(&tmp, 0, sizeof tmp);

  c->state = SAE_CONFIRMED;
  return 1;
}

/* confirm = HMAC-SHA256(KCK, send-confirm || scalar1 || element1 ||
 *                            scalar2 || element2)
 *
 * Each side puts its own commit first. `mine` selects whose. */
static void confirm_mic(const sae_ctx *c, uint8_t out[SHA256_DIGEST],
                        uint16_t send_confirm, int mine)
{
  uint8_t sc[2];
  const uint8_t *p[9];
  uint32_t l[9];
  const p256_point *e1 = mine ? &c->own_element : &c->peer_element;
  const p256_point *e2 = mine ? &c->peer_element : &c->own_element;
  const uint8_t *s1 = mine ? c->own_scalar : c->peer_scalar;
  const uint8_t *s2 = mine ? c->peer_scalar : c->own_scalar;

  sc[0] = (uint8_t)(send_confirm & 0xff);
  sc[1] = (uint8_t)(send_confirm >> 8);

  p[0] = sc;      l[0] = 2;
  p[1] = s1;      l[1] = P256_BYTES;
  p[2] = e1->x;   l[2] = P256_BYTES;
  p[3] = e1->y;   l[3] = P256_BYTES;
  p[4] = s2;      l[4] = P256_BYTES;
  p[5] = e2->x;   l[5] = P256_BYTES;
  p[6] = e2->y;   l[6] = P256_BYTES;

  hmac_sha256_v(out, c->kck, SHA256_DIGEST, p, l, 7);
}

void sae_confirm_bytes(sae_ctx *c, uint8_t out[SAE_CONFIRM_LEN])
{
  out[0] = (uint8_t)(c->send_confirm & 0xff);
  out[1] = (uint8_t)(c->send_confirm >> 8);
  confirm_mic(c, out + 2, c->send_confirm, 1);
}

int sae_peer_confirm(sae_ctx *c, const uint8_t *in, uint32_t len)
{
  uint8_t want[SHA256_DIGEST];
  uint16_t sc;
  uint32_t diff = 0, i;

  if (c->state != SAE_CONFIRMED) return 0;
  if (len < SAE_CONFIRM_LEN) return 0;

  sc = (uint16_t)(in[0] | ((uint16_t)in[1] << 8));
  confirm_mic(c, want, sc, 0);

  /* Compared without an early exit. A MIC comparison that returns on the
     first differing byte tells an attacker how much of a forgery was
     right, one byte at a time. */
  for (i = 0; i < SHA256_DIGEST; i++) diff |= (uint32_t)(want[i] ^ in[2 + i]);
  memset(want, 0, sizeof want);
  if (diff) return 0;

  c->state = SAE_ACCEPTED;
  return 1;
}

void sae_clear(sae_ctx *c)
{
  memset(c, 0, sizeof *c);
}
