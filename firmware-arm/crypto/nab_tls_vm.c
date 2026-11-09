/**
 * @file nab_tls_vm.c
 * @brief What the runtime's TLS opcodes call; see nab_tls.h and
 *        ADOPTION-KEY.md.
 *
 * The adoption key is `nab_kx_key`, handed over by the bootloader's kxUse
 * and unreadable from Metal after OPbytecode (adoption_kx). TLS is keyed
 * from it here, in C: the bytecode starts a connection, feeds bytes in and
 * takes bytes out, and never sees the key, the PSK, a traffic secret or a
 * nonce. Without a key every entry point says so and the bytecode connects
 * in the clear, as it does today.
 */
#include <string.h>

#include "net/nab_kx.h"
#include "net/nab_tls.h"

extern uint8_t nab_kx_key[NAB_KX_KEY];
extern uint8_t nab_kx_key_set;
extern void ieee80211_rng_bytes(uint8_t *out, uint32_t len);

/** -1 without a key; otherwise nab_tls_state. */
int nab_tls_vm_state(void)
{
  if (!nab_kx_key_set) return -1;
  return nab_tls_state();
}

/**
 * @brief The ClientHello for this rabbit, or 0 without a key.
 *
 * The client random comes from the entropy pool (nab_rng.h), ungated: in
 * TLS 1.3 with a PSK the random is not a secret, it stops a replayed
 * ServerHello from matching, and a repeat would only let a recorded
 * handshake be replayed against the server's rules. Refusing to connect
 * for want of credit would be worse than a predictable random here.
 */
int nab_tls_vm_start(const uint8_t *identity, uint32_t idlen, uint8_t *out, uint32_t cap)
{
  uint8_t psk[NAB_TLS_PSK], rnd[32];
  int n;
  if (!nab_kx_key_set) return 0;
  nab_tls_psk(psk, nab_kx_key);
  ieee80211_rng_bytes(rnd, sizeof rnd);
  n = nab_tls_start(psk, identity, idlen, rnd, out, cap);
  memset(psk, 0, sizeof psk);
  return n;
}
