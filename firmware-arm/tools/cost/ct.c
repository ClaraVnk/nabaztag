/* Constant-time check (lapin11: first point at round 1, lapin10: round 12;
 * same length, so the HMACs cost the same) for SAE hunting-and-pecking: count instructions for
 * inputs that take different paths in a branchy implementation. -DCT=<case>.
 * Returns 1 = the result was right, so a count is never for wrong work. */
#include <stdint.h>
#include <string.h>
#include "sae.c"

static const uint8_t own[6]  = {2,0,0,0,0,1};
static const uint8_t peer[6] = {2,0,0,0,0,2};
static const uint8_t gx[32] = {0x6b,0x17,0xd1,0xf2,0xe1,0x2c,0x42,0x47,0xf8,0xbc,0xe6,0xe5,0x63,0xa4,0x40,0xf2,0x77,0x03,0x7d,0x81,0x2d,0xeb,0x33,0xa0,0xf4,0xa1,0x39,0x45,0xd8,0x98,0xc2,0x96};
static const uint8_t x1[32] = {0x39,0x98,0x8f,0xc2,0x5a,0x36,0x43,0xfb,0x4a,0xa5,0xc3,0x8e,0x4d,0x67,0x29,0x10,0x7a,0xd8,0xb8,0x47,0x0c,0x98,0x8b,0xc1,0x2d,0x39,0x12,0x1e,0xbc,0x76,0x7c,0x07};
static const uint8_t x12[32] = {0x5b,0x88,0x2d,0xad,0x84,0xa4,0x11,0xb9,0xcd,0x1b,0x31,0x7b,0xe9,0xcb,0xfb,0xb8,0x38,0x04,0x6a,0xdb,0x73,0x2d,0xbb,0x3e,0xa6,0xb7,0x32,0xf5,0xa5,0xe2,0xa0,0xcf};

#define dec_valid 1
#define dec_invalid 2
#define pwe_round1 3
#define pwe_round12 4

/* The two cases of a pair run the same code on different data, so the
   binaries differ only in .rodata and a per-function diff lines up. */
static const uint8_t two[32] = {[31] = 2};
#if CT == dec_valid
static const uint8_t *const IN = gx; static const int WANT = 1;
#elif CT == dec_invalid
static const uint8_t *const IN = two; static const int WANT = 0;
#elif CT == pwe_round1
static const char PW[] = "lapin11"; static const uint8_t *const WANT_X = x1;
#elif CT == pwe_round12
static const char PW[] = "lapin10"; static const uint8_t *const WANT_X = x12;
#endif

int bench(void)
{
  p256_point pt;
#if CT == dec_valid || CT == dec_invalid
  return p256_decompress(&pt, IN, 0, 0) == WANT;
#else
  return derive_pwe(&pt, own, peer, PW, 0) && !memcmp(pt.x, WANT_X, 32);
#endif
}
