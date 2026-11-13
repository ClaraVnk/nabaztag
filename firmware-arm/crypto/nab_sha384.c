/**
 * @file nab_sha384.c
 * @brief SHA-384 (FIPS 180-4) on the SHA-512 block function TweetNaCl
 *        already brings for Ed25519: other initial values, 48 bytes out.
 */
#include <string.h>

#include "net/nab_pki.h"

int crypto_hashblocks_sha512_tweet(unsigned char *x, const unsigned char *m,
                                   unsigned long long n);

static const uint8_t IV384[64] = {
  0xcb,0xbb,0x9d,0x5d,0xc1,0x05,0x9e,0xd8, 0x62,0x9a,0x29,0x2a,0x36,0x7c,0xd5,0x07,
  0x91,0x59,0x01,0x5a,0x30,0x70,0xdd,0x17, 0x15,0x2f,0xec,0xd8,0xf7,0x0e,0x59,0x39,
  0x67,0x33,0x26,0x67,0xff,0xc0,0x0b,0x31, 0x8e,0xb4,0x4a,0x87,0x68,0x58,0x15,0x11,
  0xdb,0x0c,0x2e,0x0d,0x64,0xf9,0x8f,0xa7, 0x47,0xb5,0x48,0x1d,0xbe,0xfa,0x4f,0xa4 };

void nab_sha384(uint8_t out[48], const uint8_t *m, uint32_t n)
{
  uint8_t h[64], x[256];
  uint32_t total = n, rest;
  memcpy(h, IV384, 64);
  crypto_hashblocks_sha512_tweet(h, m, n);
  rest = n & 127;
  m += n - rest;
  memset(x, 0, sizeof x);
  memcpy(x, m, rest);
  x[rest] = 0x80;
  n = rest < 112 ? 128 : 256;
  /* The length in bits, big-endian in the last 16 bytes (fits in 35 bits). */
  x[n - 5] = (uint8_t)(total >> 29);
  x[n - 4] = (uint8_t)(total >> 21);
  x[n - 3] = (uint8_t)(total >> 13);
  x[n - 2] = (uint8_t)(total >> 5);
  x[n - 1] = (uint8_t)(total << 3);
  crypto_hashblocks_sha512_tweet(h, x, n);
  memcpy(out, h, 48);
}
