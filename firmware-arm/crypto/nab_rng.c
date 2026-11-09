/**
 * @file nab_rng.c
 * @brief The entropy pool; see nab_rng.h for why and what feeds it.
 *
 * Cost on the ARM7: one SHA-256 compression per 64 bytes fed (four frames)
 * and three per 32 bytes drawn -- well under a percent of the CPU on a
 * busy channel. Nothing here allocates.
 */
#include <string.h>

#include "net/nab_rng.h"
#include "net/sha256.h"

/* The carry survives a soft reset in external RAM on the rabbit (the
   .extbss section the extbss step declares, NOLOAD). On a build machine it
   is an ordinary variable. */
#if defined(__arm__) && !defined(_NAB_SIM)
#define NAB_RNG_RETAINED __attribute__((section(".extbss")))
#else
#define NAB_RNG_RETAINED
#endif
uint8_t nab_rng_carry[SHA256_DIGEST] NAB_RNG_RETAINED;

static uint8_t    pool[SHA256_DIGEST];
/* Everything mixed in since the last draw, hashed as it arrives: one
   compression per 64 bytes of input, and no two inputs ever combine before
   they reach the hash. (A first version XORed frames into a block and
   hashed every sixteenth; four frames whose timers all differed by one bit
   cancelled each other out. test_rng.c keeps that case.) */
static sha256_ctx acc;
static uint32_t   credit;
static uint8_t    seed_was_stored, seed_handed_out, inited;

#define CREDIT_CAP 4096u

/* The accumulator always starts from the current pool, so the next pool is
   SHA-256(pool || everything since). */
static void acc_start(void)
{
  sha256_init(&acc);
  sha256_update(&acc, pool, SHA256_DIGEST);
}

static void mix(void)
{
  sha256_final(&acc, pool);
  acc_start();
}

static void give_credit(uint32_t bits)
{
  credit = (credit + bits > CREDIT_CAP) ? CREDIT_CAP : credit + bits;
}

static int seed_blank(const uint8_t *seed)
{
  uint32_t i, zero = 1, ff = 1;
  for(i = 0; i < NAB_RNG_SEED_LEN; i++) {
    if(seed[i] != 0x00) zero = 0;
    if(seed[i] != 0xFF) ff = 0;
  }
  return zero || ff;
}

void nab_rng_init(const uint8_t *sram, uint32_t len, const uint8_t *seed)
{
  uint8_t d[SHA256_DIGEST];

  memset(pool, 0, sizeof pool);
  credit = 0;
  seed_was_stored = 0;
  seed_handed_out = 0;
  inited = 1;
  acc_start();

  /* The retained RAM: power-up noise, or the previous run. One hash of
     the window, then the digest goes in like anything else. */
  if(sram != NULL && len > 0) {
    sha256(d, sram, len);
    sha256_update(&acc, d, sizeof d);
  }
  sha256_update(&acc, nab_rng_carry, sizeof nab_rng_carry);
  if(seed != NULL && !seed_blank(seed)) {
    sha256_update(&acc, seed, NAB_RNG_SEED_LEN);
    seed_was_stored = 1;
    give_credit(NAB_RNG_MIN_CREDIT);
  }
  mix();
  memset(d, 0, sizeof d);
}

void nab_rng_add(const uint8_t *p, uint32_t len, uint32_t bits)
{
  if(!inited) { inited = 1; acc_start(); }
  sha256_update(&acc, p, len);
  give_credit(bits);
}

void nab_rng_frame(const uint8_t *frame, uint32_t len, int16_t rssi,
                   uint32_t now)
{
  uint8_t s[16];
  uint32_t n = 0, i;

  memcpy(s, &now, 4);          n += 4;
  memcpy(s + n, &rssi, 2);     n += 2;
  /* Sequence control, then the first body bytes: a beacon's timestamp,
     a data frame's LLC, whatever the air carried. Short frames give what
     they have. */
  for(i = 22; i < len && n < sizeof s; i++) s[n++] = frame[i];
  nab_rng_add(s, n, NAB_RNG_FRAME_CREDIT);
}

uint32_t nab_rng_credit(void) { return credit; }

int nab_rng_ready(void) { return inited && credit >= NAB_RNG_MIN_CREDIT; }

void nab_rng_bytes(uint8_t *out, uint32_t len)
{
  uint8_t in[1 + SHA256_DIGEST], d[SHA256_DIGEST];
  uint32_t n;

  /* Whatever is pending goes in first, so a draw never repeats the one
     before it even when nothing else happened in between. */
  mix();
  while(len > 0) {
    in[0] = 0x01;
    memcpy(in + 1, pool, SHA256_DIGEST);
    sha256(d, in, sizeof in);
    n = len < SHA256_DIGEST ? len : SHA256_DIGEST;
    memcpy(out, d, n);
    out += n;
    len -= n;
    /* Ratchet: the state that produced d is gone. */
    in[0] = 0x02;
    sha256(pool, in, sizeof in);
  }
  /* The carry is a function of the new state, never the state itself:
     what a soft reset reads back cannot be run forward to this draw. */
  in[0] = 0x03;
  memcpy(in + 1, pool, SHA256_DIGEST);
  sha256(nab_rng_carry, in, sizeof in);
  memset(in, 0, sizeof in);
  memset(d, 0, sizeof d);
}

int nab_rng_key_bytes(uint8_t *out, uint32_t len)
{
  if(!nab_rng_ready()) return 0;
  nab_rng_bytes(out, len);
  return 1;
}

int nab_rng_seed_to_store(uint8_t out[NAB_RNG_SEED_LEN])
{
  if(seed_was_stored || seed_handed_out || !nab_rng_ready()) return 0;
  seed_handed_out = 1;
  nab_rng_bytes(out, NAB_RNG_SEED_LEN);
  return 1;
}

int nab_rng_seed_stored(void) { return seed_was_stored; }
