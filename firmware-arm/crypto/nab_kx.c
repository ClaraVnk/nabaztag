/**
 * @file nab_kx.c
 * @brief The adoption key exchange; see nab_kx.h.
 *
 * X25519 is TweetNaCl's crypto_scalarmult. The x25519_wdt step makes its
 * ladder feed the watchdog once per step: it is 63.6 M instructions in one
 * piece otherwise (tools/cost, 2026-11-08), against a 2.09 s watchdog.
 */
#include <string.h>

#include "net/nab_kx.h"
#include "net/sha256.h"
#include "tweetnacl.h"

static const uint8_t nab_kx_base[32] = { 9 };
static const char nab_kx_salt[] = "nabaztag adoption v1";

void nab_hkdf(uint8_t *out, uint32_t outlen,
              const uint8_t *salt, uint32_t saltlen,
              const uint8_t *ikm, uint32_t ikmlen,
              const uint8_t *info, uint32_t infolen)
{
  uint8_t prk[SHA256_DIGEST], t[SHA256_DIGEST], ctr = 0;
  uint32_t done = 0, tlen = 0, n;

  hmac_sha256(prk, salt, saltlen, ikm, ikmlen);
  while(done < outlen) {
    const uint8_t *pieces[3];
    uint32_t lens[3];
    ctr++;
    pieces[0] = t;     lens[0] = tlen;
    pieces[1] = info;  lens[1] = infolen;
    pieces[2] = &ctr;  lens[2] = 1;
    hmac_sha256_v(t, prk, sizeof prk, pieces, lens, 3);
    tlen = SHA256_DIGEST;
    n = outlen - done;
    if(n > SHA256_DIGEST) n = SHA256_DIGEST;
    memcpy(out + done, t, n);
    done += n;
  }
  memset(prk, 0, sizeof prk);
  memset(t, 0, sizeof t);
}

void nab_kx_keypair(uint8_t pub[NAB_KX_PUB], uint8_t priv[32])
{
  priv[0] &= 248;
  priv[31] &= 127;
  priv[31] |= 64;
  crypto_scalarmult(pub, priv, nab_kx_base);
}

int nab_kx_derive(uint8_t key[NAB_KX_KEY], uint8_t check[NAB_KX_LEDS],
                  const uint8_t priv[32],
                  const uint8_t peer_pub[NAB_KX_PUB],
                  const uint8_t rabbit_pub[NAB_KX_PUB],
                  const uint8_t warren_pub[NAB_KX_PUB])
{
  uint8_t shared[32], info[5 + 2 * NAB_KX_PUB], okm[NAB_KX_KEY + 4];
  uint8_t zero = 0;
  uint32_t i, bits;

  crypto_scalarmult(shared, priv, peer_pub);
  for(i = 0; i < sizeof shared; i++) zero |= shared[i];
  if(!zero) return 0;

  /* One Expand gives both: the key, then four bytes for the check value.
     Both public keys are in the info so the check value commits to the
     exact exchange each side saw. */
  memcpy(info, "nabkx", 5);
  memcpy(info + 5, rabbit_pub, NAB_KX_PUB);
  memcpy(info + 5 + NAB_KX_PUB, warren_pub, NAB_KX_PUB);
  nab_hkdf(okm, sizeof okm, (const uint8_t *)nab_kx_salt, sizeof nab_kx_salt - 1,
           shared, sizeof shared, info, sizeof info);
  memcpy(key, okm, NAB_KX_KEY);

  /* Fifteen bits, three per LED, most significant first. */
  bits = ((uint32_t)okm[NAB_KX_KEY] << 8) | okm[NAB_KX_KEY + 1];
  for(i = 0; i < NAB_KX_LEDS; i++)
    check[i] = (uint8_t)((bits >> (3 * (NAB_KX_LEDS - 1 - i))) & 7);

  memset(shared, 0, sizeof shared);
  memset(okm, 0, sizeof okm);
  return 1;
}

void nab_hmac_long(uint8_t out[32], const uint8_t key[NAB_KX_KEY],
                   const uint8_t *data, uint32_t len, void (*tick)(void))
{
  uint8_t pad[64], inner[SHA256_DIGEST];
  sha256_ctx c;
  uint32_t i, n;

  /* RFC 2104 with a 32-byte key: no pre-hash, zero-padded to the block. */
  memset(pad, 0x36, sizeof pad);
  for(i = 0; i < NAB_KX_KEY; i++) pad[i] ^= key[i];
  sha256_init(&c);
  sha256_update(&c, pad, sizeof pad);
  for(i = 0; i < len; i += n) {
    n = len - i;
    if(n > 4096) n = 4096;
    sha256_update(&c, data + i, n);
    if(tick) tick();
  }
  sha256_final(&c, inner);

  memset(pad, 0x5c, sizeof pad);
  for(i = 0; i < NAB_KX_KEY; i++) pad[i] ^= key[i];
  sha256_init(&c);
  sha256_update(&c, pad, sizeof pad);
  sha256_update(&c, inner, sizeof inner);
  sha256_final(&c, out);
  memset(pad, 0, sizeof pad);
  memset(inner, 0, sizeof inner);
}

int nab_hmac_check(const uint8_t key[NAB_KX_KEY], const uint8_t *data,
                   uint32_t len, const uint8_t mac[32], void (*tick)(void))
{
  uint8_t want[32], diff = 0;
  uint32_t i;
  nab_hmac_long(want, key, data, len, tick);
  for(i = 0; i < sizeof want; i++) diff |= want[i] ^ mac[i];
  memset(want, 0, sizeof want);
  return diff == 0;
}
