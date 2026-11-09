/**
 * @file nab_kx_vm.c
 * @brief What the bootloader's adoption opcodes call; see ADOPTION-KEY.md.
 *
 * The private key never reaches Metal: kxBegin keeps it here and returns
 * only the public key; kxFinish wipes it. Once the bootloader has handed
 * over to a runtime bytecode (OPbytecode), nab_kx_latched is set and every
 * entry point refuses: the runtime is the warren's code, not ours.
 */
#include <string.h>

#include "net/nab_kx.h"

/* The entropy pool (nab_rng.h), through the driver's gate: 0 until the pool
   has accrued its minimum credit, and the bootloader then asks again on its
   next pass rather than drawing a key from a timer. A long-lived key is the
   one thing here that must never come from a state an observer can search. */
extern int ieee80211_rng_key_bytes(uint8_t *out, uint32_t len);

uint8_t nab_kx_latched;
uint8_t nab_kx_key[NAB_KX_KEY];
uint8_t nab_kx_key_set;

static uint8_t kx_priv[32], kx_pub[NAB_KX_PUB], kx_have;

int nab_kx_vm_begin(uint8_t pub[NAB_KX_PUB])
{
  if(nab_kx_latched) return 0;
  if(!ieee80211_rng_key_bytes(kx_priv, sizeof kx_priv)) return 0;
  nab_kx_keypair(kx_pub, kx_priv);
  memcpy(pub, kx_pub, NAB_KX_PUB);
  kx_have = 1;
  return 1;
}

int nab_kx_vm_finish(const uint8_t warren_pub[NAB_KX_PUB],
                     uint8_t key[NAB_KX_KEY], uint8_t check[NAB_KX_LEDS])
{
  int ok;
  if(nab_kx_latched || !kx_have) return 0;
  ok = nab_kx_derive(key, check, kx_priv, warren_pub, kx_pub, warren_pub);
  memset(kx_priv, 0, sizeof kx_priv);
  kx_have = 0;
  return ok;
}

int nab_kx_vm_use(const uint8_t key[NAB_KX_KEY])
{
  if(nab_kx_latched) return 0;
  memcpy(nab_kx_key, key, NAB_KX_KEY);
  nab_kx_key_set = 1;
  return 1;
}

/* The bytecode's HMAC, with the key kxUse handed over. Usable after the
   hand-over too: it says yes or no and never gives the key out. */
extern void nab_verify_tick(void);

int nab_kx_vm_check(const uint8_t *data, uint32_t len, const uint8_t mac[32])
{
  if(!nab_kx_key_set) return 0;
  return nab_hmac_check(nab_kx_key, data, len, mac, nab_verify_tick);
}
