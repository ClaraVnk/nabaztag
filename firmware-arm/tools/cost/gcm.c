/* Minimal AES-128-GCM (NIST SP 800-38D), sized for flash not speed: GHASH
 * is the bit-serial Algorithm 1 with no tables, so H costs nothing to
 * precompute and the state is 32 bytes. 12-byte IV only, which is TLS's. */
#include <stdint.h>
#include <string.h>
#include "gcm.h"

static uint32_t be32(const uint8_t *p)
{ return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static void put32(uint8_t *p, uint32_t v)
{ p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v; }

/* y = (y ^ x) * H in GF(2^128), bit-serial, GCM's bit order. */
static void ghash_block(uint32_t y[4], const uint32_t h[4], const uint8_t x[16])
{
  uint32_t z[4] = {0, 0, 0, 0}, v[4];
  int i;
  y[0] ^= be32(x); y[1] ^= be32(x + 4); y[2] ^= be32(x + 8); y[3] ^= be32(x + 12);
  v[0] = h[0]; v[1] = h[1]; v[2] = h[2]; v[3] = h[3];
  for (i = 0; i < 128; i++) {
    uint32_t mask = (uint32_t)0 - ((y[i >> 5] >> (31 - (i & 31))) & 1);
    uint32_t lsb = v[3] & 1;
    z[0] ^= v[0] & mask; z[1] ^= v[1] & mask; z[2] ^= v[2] & mask; z[3] ^= v[3] & mask;
    v[3] = (v[3] >> 1) | (v[2] << 31); v[2] = (v[2] >> 1) | (v[1] << 31);
    v[1] = (v[1] >> 1) | (v[0] << 31);
    v[0] = (v[0] >> 1) ^ (0xe1000000u & ((uint32_t)0 - lsb));
  }
  y[0] = z[0]; y[1] = z[1]; y[2] = z[2]; y[3] = z[3];
}

static void ghash_bytes(uint32_t y[4], const uint32_t h[4], const uint8_t *p, uint32_t n)
{
  uint8_t last[16];
  while (n >= 16) { ghash_block(y, h, p); p += 16; n -= 16; }
  if (n) { memset(last, 0, 16); memcpy(last, p, n); ghash_block(y, h, last); }
}

void gcm_encrypt(const struct aes128_enc *aes, const uint8_t iv[12],
                 const uint8_t *aad, uint32_t aadlen,
                 uint8_t *buf, uint32_t len, uint8_t tag[16])
{
  uint8_t ctr[16], ks[16], hb[16], lens[16];
  uint32_t h[4], y[4] = {0, 0, 0, 0}, c, i, n = len;
  uint8_t *p = buf;

  memset(hb, 0, 16);
  aes128_enc_block(aes, hb, hb);
  h[0] = be32(hb); h[1] = be32(hb + 4); h[2] = be32(hb + 8); h[3] = be32(hb + 12);

  memcpy(ctr, iv, 12); put32(ctr + 12, 1);
  aes128_enc_block(aes, hb, ctr);          /* E(K, J0), masks the tag */

  ghash_bytes(y, h, aad, aadlen);
  c = 2;
  while (n) {
    uint32_t k = n < 16 ? n : 16;
    put32(ctr + 12, c++);
    aes128_enc_block(aes, ks, ctr);
    for (i = 0; i < k; i++) p[i] ^= ks[i];
    p += k; n -= k;
  }
  ghash_bytes(y, h, buf, len);
  memset(lens, 0, 16);
  put32(lens + 4, aadlen * 8); put32(lens + 12, len * 8);
  ghash_block(y, h, lens);
  for (i = 0; i < 4; i++) put32(tag + 4 * i, y[i]);
  for (i = 0; i < 16; i++) tag[i] ^= hb[i];
}
