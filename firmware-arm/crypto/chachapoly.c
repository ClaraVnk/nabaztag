/**
 * @file chachapoly.c
 * @brief ChaCha20-Poly1305, RFC 8439. See chachapoly.h.
 */
#include <string.h>

#include "net/chachapoly.h"

static uint32_t le32(const uint8_t *p)
{ return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

static void put_le32(uint8_t *p, uint32_t v)
{ p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }

#define ROTL(v, n) (((v) << (n)) | ((v) >> (32 - (n))))
#define QR(a, b, c, d) \
  a += b; d ^= a; d = ROTL(d, 16); c += d; b ^= c; b = ROTL(b, 12); \
  a += b; d ^= a; d = ROTL(d, 8);  c += d; b ^= c; b = ROTL(b, 7)

/* 2.3: one 64-byte block of key stream. */
static void block(uint8_t out[64], const uint8_t key[32], uint32_t counter,
                  const uint8_t nonce[12])
{
  uint32_t s[16], x[16];
  s[0] = 0x61707865; s[1] = 0x3320646e; s[2] = 0x79622d32; s[3] = 0x6b206574;
  for (int i = 0; i < 8; i++) s[4 + i] = le32(key + 4 * i);
  s[12] = counter;
  for (int i = 0; i < 3; i++) s[13 + i] = le32(nonce + 4 * i);
  memcpy(x, s, sizeof x);
  for (int i = 0; i < 10; i++) {
    QR(x[0], x[4], x[8],  x[12]); QR(x[1], x[5], x[9],  x[13]);
    QR(x[2], x[6], x[10], x[14]); QR(x[3], x[7], x[11], x[15]);
    QR(x[0], x[5], x[10], x[15]); QR(x[1], x[6], x[11], x[12]);
    QR(x[2], x[7], x[8],  x[13]); QR(x[3], x[4], x[9],  x[14]);
  }
  for (int i = 0; i < 16; i++) put_le32(out + 4 * i, x[i] + s[i]);
}

void chacha20_xor(const uint8_t key[32], const uint8_t nonce[12], uint32_t counter,
                  uint8_t *data, uint32_t len)
{
  uint8_t ks[64];
  while (len) {
    uint32_t k = len < 64 ? len : 64;
    block(ks, key, counter++, nonce);
    for (uint32_t i = 0; i < k; i++) data[i] ^= ks[i];
    data += k; len -= k;
  }
}

/* 2.5: Poly1305 in five 26-bit limbs (the poly1305-donna layout). */
typedef struct { uint32_t r[5], h[5], pad[4]; } poly;

static void poly_init(poly *p, const uint8_t key[32])
{
  p->r[0] = (le32(key +  0)     ) & 0x3ffffff;
  p->r[1] = (le32(key +  3) >> 2) & 0x3ffff03;
  p->r[2] = (le32(key +  6) >> 4) & 0x3ffc0ff;
  p->r[3] = (le32(key +  9) >> 6) & 0x3f03fff;
  p->r[4] = (le32(key + 12) >> 8) & 0x00fffff;
  memset(p->h, 0, sizeof p->h);
  for (int i = 0; i < 4; i++) p->pad[i] = le32(key + 16 + 4 * i);
}

/* One 16-byte block; hibit is 1 << 24 for a full block, 0 for the last
   partial one, which carries its own 0x01 byte. */
static void poly_block(poly *p, const uint8_t m[16], uint32_t hibit)
{
  uint32_t r0 = p->r[0], r1 = p->r[1], r2 = p->r[2], r3 = p->r[3], r4 = p->r[4];
  uint32_t s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5;
  uint32_t h0 = p->h[0], h1 = p->h[1], h2 = p->h[2], h3 = p->h[3], h4 = p->h[4], c;
  uint64_t d0, d1, d2, d3, d4;
  h0 += (le32(m +  0)     ) & 0x3ffffff;
  h1 += (le32(m +  3) >> 2) & 0x3ffffff;
  h2 += (le32(m +  6) >> 4) & 0x3ffffff;
  h3 += (le32(m +  9) >> 6) & 0x3ffffff;
  h4 += (le32(m + 12) >> 8) | hibit;
  d0 = (uint64_t)h0 * r0 + (uint64_t)h1 * s4 + (uint64_t)h2 * s3 + (uint64_t)h3 * s2 + (uint64_t)h4 * s1;
  d1 = (uint64_t)h0 * r1 + (uint64_t)h1 * r0 + (uint64_t)h2 * s4 + (uint64_t)h3 * s3 + (uint64_t)h4 * s2;
  d2 = (uint64_t)h0 * r2 + (uint64_t)h1 * r1 + (uint64_t)h2 * r0 + (uint64_t)h3 * s4 + (uint64_t)h4 * s3;
  d3 = (uint64_t)h0 * r3 + (uint64_t)h1 * r2 + (uint64_t)h2 * r1 + (uint64_t)h3 * r0 + (uint64_t)h4 * s4;
  d4 = (uint64_t)h0 * r4 + (uint64_t)h1 * r3 + (uint64_t)h2 * r2 + (uint64_t)h3 * r1 + (uint64_t)h4 * r0;
  c = (uint32_t)(d0 >> 26); h0 = (uint32_t)d0 & 0x3ffffff;
  d1 += c; c = (uint32_t)(d1 >> 26); h1 = (uint32_t)d1 & 0x3ffffff;
  d2 += c; c = (uint32_t)(d2 >> 26); h2 = (uint32_t)d2 & 0x3ffffff;
  d3 += c; c = (uint32_t)(d3 >> 26); h3 = (uint32_t)d3 & 0x3ffffff;
  d4 += c; c = (uint32_t)(d4 >> 26); h4 = (uint32_t)d4 & 0x3ffffff;
  h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffff;
  h1 += c;
  p->h[0] = h0; p->h[1] = h1; p->h[2] = h2; p->h[3] = h3; p->h[4] = h4;
}

static void poly_update(poly *p, const uint8_t *m, uint32_t len)
{
  uint8_t last[16];
  while (len >= 16) { poly_block(p, m, 1u << 24); m += 16; len -= 16; }
  if (len) {
    memset(last, 0, 16);
    memcpy(last, m, len);
    last[len] = 1;
    poly_block(p, last, 0);
  }
}

static void poly_finish(poly *p, uint8_t tag[16])
{
  uint32_t h0 = p->h[0], h1 = p->h[1], h2 = p->h[2], h3 = p->h[3], h4 = p->h[4], c;
  uint32_t g0, g1, g2, g3, g4, mask;
  uint64_t f;
  c = h1 >> 26; h1 &= 0x3ffffff; h2 += c;
  c = h2 >> 26; h2 &= 0x3ffffff; h3 += c;
  c = h3 >> 26; h3 &= 0x3ffffff; h4 += c;
  c = h4 >> 26; h4 &= 0x3ffffff; h0 += c * 5;
  c = h0 >> 26; h0 &= 0x3ffffff; h1 += c;
  /* h - p, kept if it does not borrow */
  g0 = h0 + 5; c = g0 >> 26; g0 &= 0x3ffffff;
  g1 = h1 + c; c = g1 >> 26; g1 &= 0x3ffffff;
  g2 = h2 + c; c = g2 >> 26; g2 &= 0x3ffffff;
  g3 = h3 + c; c = g3 >> 26; g3 &= 0x3ffffff;
  g4 = h4 + c - (1u << 26);
  mask = (g4 >> 31) - 1;
  h0 = (h0 & ~mask) | (g0 & mask); h1 = (h1 & ~mask) | (g1 & mask);
  h2 = (h2 & ~mask) | (g2 & mask); h3 = (h3 & ~mask) | (g3 & mask);
  h4 = (h4 & ~mask) | (g4 & mask);
  /* to 4 x 32 bits, plus the pad */
  h0 = (h0      ) | (h1 << 26);
  h1 = (h1 >>  6) | (h2 << 20);
  h2 = (h2 >> 12) | (h3 << 14);
  h3 = (h3 >> 18) | (h4 <<  8);
  f = (uint64_t)h0 + p->pad[0];             put_le32(tag,      (uint32_t)f);
  f = (uint64_t)h1 + p->pad[1] + (f >> 32); put_le32(tag + 4,  (uint32_t)f);
  f = (uint64_t)h2 + p->pad[2] + (f >> 32); put_le32(tag + 8,  (uint32_t)f);
  f = (uint64_t)h3 + p->pad[3] + (f >> 32); put_le32(tag + 12, (uint32_t)f);
}

void poly1305(uint8_t tag[16], const uint8_t key[32], const uint8_t *m, uint32_t len)
{
  poly p;
  poly_init(&p, key);
  poly_update(&p, m, len);
  poly_finish(&p, tag);
}

/* 2.8: the AEAD. The MAC input is AAD and ciphertext each zero-padded to
   16 bytes -- full blocks, unlike poly_update's last block with its 0x01 --
   then both lengths, little-endian 64-bit. */
static void mac(uint8_t tag[16], const uint8_t otk[32], const uint8_t *aad, uint32_t aadlen,
                const uint8_t *ct, uint32_t len)
{
  const uint32_t aadlen0 = aadlen, len0 = len;
  uint8_t lens[16];
  poly p;
  poly_init(&p, otk);
  while (aadlen >= 16) { poly_block(&p, aad, 1u << 24); aad += 16; aadlen -= 16; }
  if (aadlen) { uint8_t b[16]; memset(b, 0, 16); memcpy(b, aad, aadlen); poly_block(&p, b, 1u << 24); }
  while (len >= 16) { poly_block(&p, ct, 1u << 24); ct += 16; len -= 16; }
  if (len) { uint8_t b[16]; memset(b, 0, 16); memcpy(b, ct, len); poly_block(&p, b, 1u << 24); }
  memset(lens, 0, 16);
  put_le32(lens, aadlen0);
  put_le32(lens + 8, len0);
  poly_block(&p, lens, 1u << 24);
  poly_finish(&p, tag);
}

int chachapoly(const uint8_t key[32], const uint8_t nonce[12],
               const uint8_t *aad, uint32_t aadlen,
               uint8_t *data, uint32_t len, uint8_t tag[16], int encrypt)
{
  uint8_t otk[64], want[16];
  uint8_t diff = 0;
  block(otk, key, 0, nonce);
  if (encrypt) {
    chacha20_xor(key, nonce, 1, data, len);
    mac(tag, otk, aad, aadlen, data, len);
    return 1;
  }
  mac(want, otk, aad, aadlen, data, len);
  for (int i = 0; i < 16; i++) diff |= want[i] ^ tag[i];
  if (diff) return 0;
  chacha20_xor(key, nonce, 1, data, len);
  return 1;
}
