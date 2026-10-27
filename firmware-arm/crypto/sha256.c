/**
 * @file sha256.c
 * @brief FIPS 180-4 SHA-256, RFC 2104 HMAC, and 802.11's counter-mode KDF.
 */

#include "sha256.h"

#include <string.h>

static const uint32_t K[64] = {
  0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
  0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
  0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
  0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
  0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
  0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
  0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
  0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
  0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
  0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
  0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
  0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
  0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
  0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
  0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
  0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x, y, z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BS0(x) (ROR(x, 2) ^ ROR(x, 13) ^ ROR(x, 22))
#define BS1(x) (ROR(x, 6) ^ ROR(x, 11) ^ ROR(x, 25))
#define SS0(x) (ROR(x, 7) ^ ROR(x, 18) ^ ((x) >> 3))
#define SS1(x) (ROR(x, 17) ^ ROR(x, 19) ^ ((x) >> 10))

/* The message schedule is kept as a rolling window of 16 words rather than
   all 64: w[i] only ever needs i-2, i-7, i-15 and i-16. 192 bytes of stack
   saved on a chip that has 16 KB of RAM, at the cost of one index mask per
   round. */
static void sha256_block(uint32_t h[8], const uint8_t block[SHA256_BLOCK])
{
  uint32_t w[16], a, b, c, d, e, f, g, hh, t1, t2;
  int i;

  for (i = 0; i < 16; i++)
    w[i] = ((uint32_t)block[i * 4] << 24) | ((uint32_t)block[i * 4 + 1] << 16) |
           ((uint32_t)block[i * 4 + 2] << 8) | block[i * 4 + 3];

  a = h[0]; b = h[1]; c = h[2]; d = h[3];
  e = h[4]; f = h[5]; g = h[6]; hh = h[7];

  for (i = 0; i < 64; i++) {
    if (i >= 16) {
      w[i & 15] += SS0(w[(i + 1) & 15]) + w[(i + 9) & 15] +
                   SS1(w[(i + 14) & 15]);
    }
    t1 = hh + BS1(e) + CH(e, f, g) + K[i] + w[i & 15];
    t2 = BS0(a) + MAJ(a, b, c);
    hh = g; g = f; f = e; e = d + t1;
    d = c; c = b; b = a; a = t1 + t2;
  }

  h[0] += a; h[1] += b; h[2] += c; h[3] += d;
  h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

void sha256_init(sha256_ctx *c)
{
  c->h[0] = 0x6a09e667u; c->h[1] = 0xbb67ae85u;
  c->h[2] = 0x3c6ef372u; c->h[3] = 0xa54ff53au;
  c->h[4] = 0x510e527fu; c->h[5] = 0x9b05688cu;
  c->h[6] = 0x1f83d9abu; c->h[7] = 0x5be0cd19u;
  c->buflen = 0;
  c->total_lo = c->total_hi = 0;
}

static void add_bits(sha256_ctx *c, uint32_t bytes)
{
  uint32_t lo = c->total_lo + (bytes << 3);
  if (lo < c->total_lo) c->total_hi++;
  c->total_hi += bytes >> 29;
  c->total_lo = lo;
}

void sha256_update(sha256_ctx *c, const uint8_t *data, uint32_t len)
{
  add_bits(c, len);

  if (c->buflen) {
    uint32_t want = SHA256_BLOCK - c->buflen;
    uint32_t take = len < want ? len : want;
    memcpy(c->buf + c->buflen, data, take);
    c->buflen += take;
    data += take;
    len -= take;
    if (c->buflen < SHA256_BLOCK) return;
    sha256_block(c->h, c->buf);
    c->buflen = 0;
  }
  while (len >= SHA256_BLOCK) {
    sha256_block(c->h, data);
    data += SHA256_BLOCK;
    len -= SHA256_BLOCK;
  }
  if (len) {
    memcpy(c->buf, data, len);
    c->buflen = len;
  }
}

void sha256_final(sha256_ctx *c, uint8_t out[SHA256_DIGEST])
{
  uint8_t tail[SHA256_BLOCK * 2];
  uint32_t n = c->buflen, pad, i;

  memcpy(tail, c->buf, n);
  tail[n++] = 0x80;
  pad = (n <= SHA256_BLOCK - 8) ? (SHA256_BLOCK - 8 - n)
                                : (SHA256_BLOCK * 2 - 8 - n);
  memset(tail + n, 0, pad);
  n += pad;
  tail[n++] = (uint8_t)(c->total_hi >> 24);
  tail[n++] = (uint8_t)(c->total_hi >> 16);
  tail[n++] = (uint8_t)(c->total_hi >> 8);
  tail[n++] = (uint8_t)c->total_hi;
  tail[n++] = (uint8_t)(c->total_lo >> 24);
  tail[n++] = (uint8_t)(c->total_lo >> 16);
  tail[n++] = (uint8_t)(c->total_lo >> 8);
  tail[n++] = (uint8_t)c->total_lo;

  sha256_block(c->h, tail);
  if (n > SHA256_BLOCK) sha256_block(c->h, tail + SHA256_BLOCK);

  for (i = 0; i < 8; i++) {
    out[i * 4]     = (uint8_t)(c->h[i] >> 24);
    out[i * 4 + 1] = (uint8_t)(c->h[i] >> 16);
    out[i * 4 + 2] = (uint8_t)(c->h[i] >> 8);
    out[i * 4 + 3] = (uint8_t)c->h[i];
  }
}

void sha256(uint8_t out[SHA256_DIGEST], const uint8_t *data, uint32_t len)
{
  sha256_ctx c;
  sha256_init(&c);
  sha256_update(&c, data, len);
  sha256_final(&c, out);
}

void hmac_sha256_v(uint8_t out[SHA256_DIGEST],
                   const uint8_t *key, uint32_t keylen,
                   const uint8_t *const *pieces, const uint32_t *lens,
                   int n)
{
  uint8_t k[SHA256_BLOCK], pad[SHA256_BLOCK], inner[SHA256_DIGEST];
  sha256_ctx c;
  int i;
  uint32_t j;

  if (keylen > SHA256_BLOCK) {
    sha256(k, key, keylen);
    memset(k + SHA256_DIGEST, 0, SHA256_BLOCK - SHA256_DIGEST);
  } else {
    memcpy(k, key, keylen);
    memset(k + keylen, 0, SHA256_BLOCK - keylen);
  }

  for (j = 0; j < SHA256_BLOCK; j++) pad[j] = k[j] ^ 0x36;
  sha256_init(&c);
  sha256_update(&c, pad, SHA256_BLOCK);
  for (i = 0; i < n; i++)
    if (pieces[i] && lens[i]) sha256_update(&c, pieces[i], lens[i]);
  sha256_final(&c, inner);

  for (j = 0; j < SHA256_BLOCK; j++) pad[j] = k[j] ^ 0x5c;
  sha256_init(&c);
  sha256_update(&c, pad, SHA256_BLOCK);
  sha256_update(&c, inner, SHA256_DIGEST);
  sha256_final(&c, out);

  memset(k, 0, sizeof k);
  memset(pad, 0, sizeof pad);
}

void hmac_sha256(uint8_t out[SHA256_DIGEST],
                 const uint8_t *key, uint32_t keylen,
                 const uint8_t *data, uint32_t datalen)
{
  const uint8_t *p[1];
  uint32_t l[1];
  p[0] = data;
  l[0] = datalen;
  hmac_sha256_v(out, key, keylen, p, l, 1);
}

void sha256_kdf(uint8_t *out, uint32_t outlen,
                const uint8_t *key, uint32_t keylen,
                const char *label,
                const uint8_t *context, uint32_t ctxlen)
{
  uint8_t digest[SHA256_DIGEST];
  uint8_t counter[2], bits[2];
  const uint8_t *p[4];
  uint32_t l[4], done = 0;
  uint16_t i = 1;
  uint32_t nbits = outlen * 8;

  /* Both the counter and the length go out little-endian. 802.11-2020
     12.7.1.6.2 says so, and getting either backwards produces a KDF that
     is self-consistent and interoperates with nothing. */
  bits[0] = (uint8_t)(nbits & 0xff);
  bits[1] = (uint8_t)((nbits >> 8) & 0xff);

  while (done < outlen) {
    uint32_t take;
    counter[0] = (uint8_t)(i & 0xff);
    counter[1] = (uint8_t)((i >> 8) & 0xff);

    p[0] = counter;            l[0] = 2;
    p[1] = (const uint8_t *)label;
    l[1] = 0;
    while (label[l[1]]) l[1]++;
    p[2] = context;            l[2] = ctxlen;
    p[3] = bits;               l[3] = 2;

    hmac_sha256_v(digest, key, keylen, p, l, 4);

    take = outlen - done;
    if (take > SHA256_DIGEST) take = SHA256_DIGEST;
    memcpy(out + done, digest, take);
    done += take;
    i++;
  }
  memset(digest, 0, sizeof digest);
}
