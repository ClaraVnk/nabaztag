/*
 * sha256.c -- SHA-256 (FIPS 180-4) and HMAC-SHA-256 (RFC 2104) for the loader,
 * written for the PIC18 and SDCC's pic16 port. Measured on 2026-11-24: the
 * round as one C expression cost 2.7 KB (the port keeps every temporary of
 * a compound 32-bit expression in RAM), four-byte helper functions with
 * pointer arguments 4.3 KB for the whole file; what is small is one 32-bit
 * operation per statement on static words (an add is 8 instructions) and
 * a rotation that works in place on one static word with its count in WREG
 * (`__wparam`). The message schedule is a 16-word ring, the length is kept
 * in bytes, the watchdog is cleared in every block (a 44 KB image takes
 * seconds on this core). Own code from the standard's text; nothing taken.
 */
#ifdef __SDCC
#include "board.h"
#else
#include <stdint.h>              /* tools/sha256-host.c: the same code on the host */
#endif
#include "sha256.h"


static const __code uint32_t K[64] = {
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

static const __code uint32_t H0[8] = {
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
};

static uint32_t h[8], w[16];
static uint32_t a, b, c, d, e, f, g, hh, t1, t2, x, y;
static uint8_t  blk[64];
static uint8_t  blen;
static uint32_t total;                  /* bytes hashed */
static uint8_t  key[64];                /* the HMAC key, zero-padded */

/* x rotated right by n bits, in place: bytes first, then bits. The bit step
 * is the PIC's own: bit 0 into the carry (the rotate of W is thrown away),
 * then rrcf down the four bytes -- 5 instructions where the C form was 18. */
static void rorx(uint8_t n) __wparam
{
  while(n >= 8) { x = (x >> 8) | (x << 24); n -= 8; }
#ifdef __SDCC
  while(n--) __asm__("banksel _x\n\trrcf _x, W, B\n\trrcf _x+3, F, B\n\trrcf _x+2, F, B\n\trrcf _x+1, F, B\n\trrcf _x, F, B");
#else
  while(n--) x = (x >> 1) | (x << 31);  /* the host build of tools/sha256-host.c */
#endif
}

/* y = ror(x, r1) ^ ror(x, r2); x is left rotated by r2 for the third term. */
static void sigma(uint8_t r1, uint8_t r2)
{
  rorx(r1); y = x;
  rorx(r2 - r1); y ^= x;
}

/* w[idx] without the 16-bit multiply the port calls for an indexed 32-bit
 * array (a 314-byte library routine): the offset is 8-bit arithmetic. */
#define WP(idx) (*(__data uint32_t *)((__data uint8_t *)w + (uint8_t)(((idx) & 15) << 2)))

static void compress(void)
{
  uint8_t i, j;
  __data uint8_t *p = blk;
  for(i = 0; i < 16; i++) {
    x = *p++; x <<= 8; x |= *p++; x <<= 8; x |= *p++; x <<= 8; x |= *p++;
    WP(i) = x;
  }
  a = h[0]; b = h[1]; c = h[2]; d = h[3]; e = h[4]; f = h[5]; g = h[6]; hh = h[7];
  for(i = 0; i < 64; i++) {
    j = i & 15;
    if(i >= 16) {
      x = WP(j + 1); t2 = x;
      sigma(7, 18); t2 >>= 3; y ^= t2;                /* sigma0: ror7 ^ ror18 ^ shr3 */
      t1 = y;
      x = WP(j + 14); t2 = x;
      sigma(17, 19); t2 >>= 10; y ^= t2;              /* sigma1: ror17 ^ ror19 ^ shr10 */
      t1 += y;
      t1 += WP(j + 9);
      WP(j) += t1;
    }
    x = e; sigma(6, 11); rorx(25 - 11); y ^= x; t1 = y;   /* Sigma1(e) */
    t1 += hh; t1 += K[i]; t1 += WP(j);
    x = e; x &= f; y = ~e; y &= g; x ^= y; t1 += x;  /* Ch(e,f,g) */
    x = a; sigma(2, 13); rorx(22 - 13); y ^= x; t2 = y;   /* Sigma0(a) */
    x = a; x &= b; y = a; y &= c; x ^= y; y = b; y &= c; x ^= y; t2 += x;   /* Maj(a,b,c) */
    hh = g; g = f; f = e; e = d; e += t1; d = c; c = b; b = a; a = t1; a += t2;
  }
  h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  clrwdt();
}

/* --- SHA-256 --------------------------------------------------------------- */

static void sha_init(void)
{
  uint8_t i;
  __data uint8_t *d = (__data uint8_t *)h;
  const __code uint8_t *s = (const __code uint8_t *)H0;
  for(i = 0; i < 32; i++) d[i] = s[i];
  blen = 0; total = 0;
}

static void sha_byte(uint8_t v) __wparam
{
  blk[blen++] = v;
  if(blen == 64) { compress(); blen = 0; }
}

static void sha_update(__data const uint8_t *p, uint16_t n)
{
  total += n;
  while(n--) sha_byte(*p++);
}

static void sha_final(__data uint8_t *out)
{
  uint8_t i;
  x = total << 3;                       /* the length in bits, big-endian, last */
  sha_byte(0x80);
  while(blen != 56) sha_byte(0);
  sha_byte(0); sha_byte(0); sha_byte(0); sha_byte((uint8_t)(total >> 29));
  sha_byte((uint8_t)(x >> 24)); sha_byte((uint8_t)(x >> 16)); sha_byte((uint8_t)(x >> 8)); sha_byte((uint8_t)x);
  {
    __data uint8_t *d = (__data uint8_t *)h;             /* little-endian words, big-endian out */
    for(i = 0; i < 32; i += 4) { out[i] = d[i + 3]; out[i + 1] = d[i + 2]; out[i + 2] = d[i + 1]; out[i + 3] = d[i]; }
  }
}

/* --- HMAC with a key of up to 64 bytes (ours is 32) ----------------------- */

void hmac_init(__data const uint8_t *k, uint8_t klen)
{
  uint8_t i;
  for(i = 0; i < 64; i++) key[i] = (uint8_t)((i < klen ? k[i] : 0) ^ 0x36);
  sha_init();
  sha_update(key, 64);
  for(i = 0; i < 64; i++) key[i] ^= 0x36 ^ 0x5C;      /* ready for the outer pass */
}

void hmac_update(__data const uint8_t *p, uint16_t n) { sha_update(p, n); }

void hmac_final(__data uint8_t *out)
{
  uint8_t i;
  sha_final(out);                       /* the inner digest */
  sha_init();
  sha_update(key, 64);
  sha_update(out, 32);
  sha_final(out);
  for(i = 0; i < 64; i++) key[i] = 0;
}
