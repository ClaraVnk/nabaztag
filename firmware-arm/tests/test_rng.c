/**
 * @file test_rng.c
 * @brief The entropy pool: what it refuses, what it mixes, what it repeats.
 *
 * No randomness is tested for "looking random" -- SHA-256 output does, and
 * test_sha256.c covers SHA-256. What matters here is the policy around it:
 * the gate holds until the credit is earned, a stored seed opens it, the
 * seed is handed out once and only once, every input (frame RSSI, arrival
 * time, retained RAM, carry, seed) changes the draw and nothing else does,
 * and a draw never exposes the state it came from.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "net/nab_rng.h"
#include "net/sha256.h"

extern uint8_t nab_rng_carry[32];

static int failures;

static void check(int ok, const char *what)
{
  printf("  %-60s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) failures++;
}

/* A beacon-shaped frame: 24-byte header, sequence number n, then a body
   whose first eight bytes are a TSF that advances. */
static void frame(uint8_t *f, uint32_t n)
{
  memset(f, 0, 64);
  f[0] = 0x80;                        /* beacon */
  f[22] = (uint8_t)(n << 4);
  f[23] = (uint8_t)(n >> 4);
  f[24] = (uint8_t)n; f[25] = (uint8_t)(n >> 8); f[26] = 0x12;
}

/* Boot a pool and feed it `frames` frames with the given RSSI pattern. */
static void boot(const uint8_t *sram, const uint8_t *seed, uint32_t frames,
                 int rssi_base, uint32_t noise)
{
  uint8_t f[64];
  uint32_t i, s = noise;
  nab_rng_init(sram, sram ? 4096 : 0, seed);
  for (i = 0; i < frames; i++) {
    int16_t r = (int16_t)rssi_base;
    if (s) { s ^= s << 13; s ^= s >> 17; s ^= s << 5; r = (int16_t)(r + (int)(s % 3) - 1); }
    frame(f, i);
    nab_rng_frame(f, sizeof f, r, 1000 + i * 102);
  }
}

static void test_gate(void)
{
  uint8_t out[32];
  uint32_t i;
  uint8_t f[64];

  printf("== the gate\n");
  nab_rng_init(NULL, 0, NULL);
  memset(nab_rng_carry, 0, sizeof nab_rng_carry);
  check(!nab_rng_ready(), "a cold pool is not ready");
  memset(out, 0xee, sizeof out);
  check(!nab_rng_key_bytes(out, sizeof out) && out[0] == 0xee,
        "key bytes refused, and nothing written");
  for (i = 0; i < NAB_RNG_MIN_CREDIT / NAB_RNG_FRAME_CREDIT - 1; i++) {
    frame(f, i); nab_rng_frame(f, sizeof f, -52, 1000 + i);
  }
  check(!nab_rng_ready(), "one frame short of the credit: still not ready");
  frame(f, i); nab_rng_frame(f, sizeof f, -52, 1000 + i);
  check(nab_rng_ready(), "the 64th frame makes it ready");
  check(nab_rng_credit() == NAB_RNG_MIN_CREDIT, "credit is exactly 128 bits");
  check(nab_rng_key_bytes(out, sizeof out), "key bytes answered once ready");
  check(!nab_rng_seed_stored(), "no seed was stored at init");
  check(nab_rng_seed_to_store(out), "the seed to persist is handed out");
  check(!nab_rng_seed_to_store(out), "...once only");
  nab_rng_bytes(out, 5);
  check(1, "ungated bytes always answer (5 bytes)");
}

static void test_seed(void)
{
  uint8_t seed[32], ff[32], out[32];
  memset(seed, 0x42, sizeof seed);
  memset(ff, 0xff, sizeof ff);

  printf("== the stored seed\n");
  nab_rng_init(NULL, 0, seed);
  check(nab_rng_ready() && nab_rng_seed_stored(), "a stored seed opens the gate at init");
  check(!nab_rng_seed_to_store(out), "and nothing is written again");
  nab_rng_init(NULL, 0, ff);
  check(!nab_rng_ready() && !nab_rng_seed_stored(), "an erased sector (0xFF) is no seed");
  memset(seed, 0, sizeof seed);
  nab_rng_init(NULL, 0, seed);
  check(!nab_rng_ready() && !nab_rng_seed_stored(), "a zeroed page is no seed");
}

static void test_inputs(void)
{
  static uint8_t sram0[4096], sram1[4096];
  uint8_t seed[32], a[32], b[32];
  uint32_t i;

  printf("== every input changes the draw; nothing else does\n");
  memset(sram1, 0x5a, sizeof sram1);
  for (i = 0; i < sizeof sram1; i += 97) sram1[i] = (uint8_t)i;
  memset(seed, 0x42, sizeof seed);

  memset(nab_rng_carry, 0, 32); boot(sram0, NULL, 80, -52, 0); nab_rng_key_bytes(a, 32);
  memset(nab_rng_carry, 0, 32); boot(sram0, NULL, 80, -52, 0); nab_rng_key_bytes(b, 32);
  check(memcmp(a, b, 32) == 0, "same frames, same RAM, same carry, no seed: same draw");

  memset(nab_rng_carry, 0, 32); boot(sram0, NULL, 80, -52, 1); nab_rng_key_bytes(b, 32);
  check(memcmp(a, b, 32) != 0, "RSSI noise of -1..+1 dB on the frames changes it");

  memset(nab_rng_carry, 0, 32); boot(sram1, NULL, 80, -52, 0); nab_rng_key_bytes(b, 32);
  check(memcmp(a, b, 32) != 0, "the retained RAM's contents change it");

  memset(nab_rng_carry, 1, 32); boot(sram0, NULL, 80, -52, 0); nab_rng_key_bytes(b, 32);
  check(memcmp(a, b, 32) != 0, "the carry changes it");

  memset(nab_rng_carry, 0, 32); boot(sram0, seed, 80, -52, 0); nab_rng_key_bytes(b, 32);
  check(memcmp(a, b, 32) != 0, "a stored seed changes it");

  /* Arrival time: the same frames one millisecond later. Every timer is
     even, so this flips one bit in each of 80 inputs -- the case an
     XOR-accumulating pool cancelled four at a time. */
  {
    uint8_t f[64];
    memset(nab_rng_carry, 0, 32);
    nab_rng_init(sram0, 4096, NULL);
    for (i = 0; i < 80; i++) { frame(f, i); nab_rng_frame(f, sizeof f, -52, 1001 + i * 102); }
    nab_rng_key_bytes(b, 32);
    check(memcmp(a, b, 32) != 0, "the frames' arrival times change it");
  }
}

static void test_draws(void)
{
  uint8_t a[32], b[32], big[100], carry[32];
  static uint8_t sram[4096];

  printf("== draws\n");
  memset(nab_rng_carry, 0, 32);
  boot(sram, NULL, 80, -52, 0);
  nab_rng_bytes(a, 32);
  memcpy(carry, nab_rng_carry, 32);
  nab_rng_bytes(b, 32);
  check(memcmp(a, b, 32) != 0, "two draws with nothing in between differ");
  check(memcmp(carry, a, 32) != 0, "the carry is not the output");
  check(memcmp(carry, nab_rng_carry, 32) != 0, "the carry moves with every draw");
  nab_rng_bytes(big, sizeof big);
  check(memcmp(big, big + 32, 32) != 0 && memcmp(big + 32, big + 64, 32) != 0,
        "a 100-byte draw is three distinct chunks");

  /* A soft reset: the carry alone, same frames, must not replay the stream. */
  {
    uint8_t c1[32], c2[32];
    memcpy(carry, nab_rng_carry, 32);
    boot(sram, NULL, 80, -52, 0);
    nab_rng_key_bytes(c1, 32);
    boot(sram, NULL, 80, -52, 0);          /* carry has moved since */
    nab_rng_key_bytes(c2, 32);
    check(memcmp(c1, c2, 32) != 0, "a soft reset (carry carried over) draws afresh");
  }
}

int main(void)
{
  test_gate();
  test_seed();
  test_inputs();
  test_draws();
  printf("\n%s\n", failures ? "FAILURES" : "all ok");
  return failures ? 1 : 0;
}
