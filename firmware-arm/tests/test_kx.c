/*
 * The adoption key exchange (crypto/nab_kx.c) against an independent
 * implementation: vectors_kx.h is X25519 and HKDF from OpenSSL, through
 * Python's `cryptography`, with the warren's construction on top.
 */
#include <stdio.h>
#include <string.h>

#include "net/nab_kx.h"
#include "vectors_kx.h"

static int fails;

/* TweetNaCl's key-generation entry points want it; nothing here calls them,
   and the firmware strips them. */
void randombytes(unsigned char *x, unsigned long long n);
void randombytes(unsigned char *x, unsigned long long n) { (void)x; (void)n; }

/* The watchdog, as x25519_wdt and verify_wdt wire it: counted here. */
static unsigned long ticks;
void nab_verify_tick(void);
void nab_verify_tick(void) { ticks++; }

static void check(const char *what, const uint8_t *got, const uint8_t *want, size_t n)
{
  if(memcmp(got, want, n) == 0) { printf("  ok    %s\n", what); return; }
  printf("  FAIL  %s\n", what);
  fails++;
}

#define ONE(n) do { \
  uint8_t rpriv[32], wpriv[32], rpub[32], wpub[32]; \
  uint8_t rkey[32], wkey[32], rchk[5], wchk[5]; \
  memcpy(rpriv, kx##n##_rpriv, 32); memcpy(wpriv, kx##n##_wpriv, 32); \
  nab_kx_keypair(rpub, rpriv); nab_kx_keypair(wpub, wpriv); \
  check("exchange " #n ": the rabbit's public key", rpub, kx##n##_rpub, 32); \
  check("exchange " #n ": the warren's public key", wpub, kx##n##_wpub, 32); \
  if(!nab_kx_derive(rkey, rchk, rpriv, wpub, rpub, wpub)) { printf("  FAIL  derive refused\n"); fails++; } \
  if(!nab_kx_derive(wkey, wchk, wpriv, rpub, rpub, wpub)) { printf("  FAIL  derive refused\n"); fails++; } \
  check("exchange " #n ": the rabbit's key", rkey, kx##n##_key, 32); \
  check("exchange " #n ": the warren's key, from its side", wkey, kx##n##_key, 32); \
  check("exchange " #n ": the rabbit's check colours", rchk, kx##n##_check, 5); \
  check("exchange " #n ": the warren's check colours", wchk, kx##n##_check, 5); \
} while(0)

int main(void)
{
  uint8_t okm[42], priv[32], key[32], chk[5], zero[32] = {0};
  uint8_t pub[32], other[32];

  nab_hkdf(okm, sizeof okm, hk_salt, sizeof hk_salt, hk_ikm, sizeof hk_ikm,
           hk_info, sizeof hk_info);
  check("HKDF-SHA-256, RFC 5869 A.1", okm, hk_okm, sizeof okm);

  ticks = 0;
  memcpy(priv, kx0_rpriv, 32);
  nab_kx_keypair(pub, priv);
  if(ticks >= 255) printf("  ok    one X25519 feeds the watchdog %lu times\n", ticks);
  else { printf("  FAIL  one X25519 fed the watchdog only %lu times\n", ticks); fails++; }

  ONE(0); ONE(1); ONE(2);

  /* A party in the middle: the rabbit sees M's key instead of the warren's. */
  {
    uint8_t mpriv[32], mpub[32], k1[32], c1[5], k2[32], c2[5];
    memcpy(priv, kx0_rpriv, 32); nab_kx_keypair(pub, priv);
    memcpy(mpriv, kx1_wpriv, 32); nab_kx_keypair(mpub, mpriv);
    nab_kx_derive(k1, c1, priv, mpub, pub, mpub);       /* rabbit <-> M */
    memcpy(other, kx0_wpriv, 32); nab_kx_keypair(k2, other);
    nab_kx_derive(k2, c2, other, mpub, mpub, kx0_wpub);  /* M <-> warren */
    if(memcmp(c1, c2, 5) != 0 && memcmp(k1, k2, 32) != 0)
      printf("  ok    a party in the middle gets two different check values\n");
    else { printf("  FAIL  middle party not detected\n"); fails++; }
  }

  /* RFC 7748 6.1: an all-zero shared secret is refused. */
  memcpy(priv, kx0_rpriv, 32);
  nab_kx_keypair(pub, priv);
  if(nab_kx_derive(key, chk, priv, zero, pub, zero) == 0)
    printf("  ok    a low-order public key is refused\n");
  else { printf("  FAIL  a low-order public key was accepted\n"); fails++; }

  /* HMAC-SHA-256 over long buffers, ticking, against Python's hmac. */
  {
    static uint8_t data[106508];
    uint8_t mac[32];
    unsigned long before;
#define HM(n) do { \
      uint32_t i; for(i = 0; i < HM##n##_LEN; i++) data[i] = (uint8_t)(i * 7 + n); \
      before = ticks; nab_hmac_long(mac, hm##n##_key, data, HM##n##_LEN, nab_verify_tick); \
      check("HMAC-SHA-256, " #n, mac, hm##n##_mac, 32); \
      if(!nab_hmac_check(hm##n##_key, data, HM##n##_LEN, hm##n##_mac, 0)) { printf("  FAIL  check " #n "\n"); fails++; } \
      mac[0] ^= 1; \
      if(nab_hmac_check(hm##n##_key, data, HM##n##_LEN, mac, 0)) { printf("  FAIL  a wrong MAC passed " #n "\n"); fails++; } \
    } while(0)
    HM(0); HM(1); HM(2); HM(3); HM(4); HM(5); HM(6); HM(7);
    if(ticks - before == 27) printf("  ok    106 508 bytes fed the watchdog 27 times\n");
    else { printf("  FAIL  %lu ticks over 106 508 bytes\n", ticks - before); fails++; }
  }

  printf(fails ? "%d FAILED\n" : "all passed\n", fails);
  return fails != 0;
}
