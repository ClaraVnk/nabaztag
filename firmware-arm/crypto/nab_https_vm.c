/**
 * @file nab_https_vm.c
 * @brief What the runtime's https* opcodes call: session 1 of nab_tls.c,
 *        a host with a certificate (NAB_TLS_CERTS).
 *
 * Every entry selects session 1 and gives session 0, the warren's, back
 * before it returns: the tls* opcodes of tls_psk never select, so they act
 * on the warren's session as they always did. The VM runs one opcode at a
 * time, so nothing can see session 1 selected.
 *
 * The roots come from the warren with the order to play (2026-11-12): the
 * one or two that anchor that host's real chain, DER, one after another.
 * They are copied here, out of the VM heap, which the GC moves.
 */
#include <string.h>

#include "net/nab_tls.h"
#include "net/nab_pki.h"

extern void ieee80211_rng_bytes(uint8_t *out, uint32_t len);

#ifdef __ELF__
#define SECTION_EXTBSS __attribute__((section(".extbss")))
#else
#define SECTION_EXTBSS
#endif

#define ROOTS_MAX  2048
#define ANCHORS_MAX 3
static uint8_t roots[ROOTS_MAX] SECTION_EXTBSS;
static struct nab_tls_anchor anchors[ANCHORS_MAX] SECTION_EXTBSS;
static nab_cert parsed SECTION_EXTBSS;

/** The ClientHello, or 0: no host, no clock, no usable root. */
int nab_https_vm_start(const uint8_t *host, uint32_t hostlen, const uint8_t *now, uint32_t nowlen,
                       const uint8_t *der, uint32_t derlen, uint8_t *out, uint32_t cap)
{
  char h[64], t[14];
  uint8_t rnd[32], secret[32];
  uint32_t p = 0, n = 0;
  int r;
  if (!hostlen || hostlen >= sizeof h || nowlen != 14 || !derlen || derlen > ROOTS_MAX) return 0;
  memcpy(h, host, hostlen); h[hostlen] = 0;
  memcpy(t, now, 14);
  memcpy(roots, der, derlen);
  while (p + 4 <= derlen && n < ANCHORS_MAX) {      /* SEQUENCE, 2-byte length */
    uint32_t len = roots[p + 1] == 0x82 ? 4u + (((uint32_t)roots[p + 2] << 8) | roots[p + 3])
                 : roots[p + 1] == 0x81 ? 3u + roots[p + 2] : 0;
    if (roots[p] != 0x30 || !len || p + len > derlen) return 0;
    if (nab_x509_parse(&parsed, roots + p, len)) return 0;
    anchors[n].subject = parsed.subject.p; anchors[n].subject_len = parsed.subject.len;
    anchors[n].alg = parsed.key_alg.p;     anchors[n].alg_len = parsed.key_alg.len;
    anchors[n].param = parsed.key_param.p; anchors[n].param_len = parsed.key_param.len;
    anchors[n].key = parsed.key.p;         anchors[n].key_len = parsed.key.len;
    n++;
    p += len;
  }
  if (!n) return 0;
  nab_tls_anchors(anchors, n);
  /* The random is public; the X25519 secret is not -- both from the
     entropy pool (nab_rng.h), as the adoption key exchange draws its own. */
  ieee80211_rng_bytes(rnd, sizeof rnd);
  ieee80211_rng_bytes(secret, sizeof secret);
  nab_tls_select(1);
  r = nab_tls_start_cert(h, t, rnd, secret, out, cap);
  nab_tls_select(0);
  memset(secret, 0, sizeof secret);
  return r;
}

int nab_https_vm_in(const uint8_t *data, uint32_t len)
{
  int r;
  nab_tls_select(1);
  if (len) nab_tls_in(data, len);
  r = nab_tls_next();
  nab_tls_select(0);
  return r;
}

void nab_https_vm_take(uint8_t *dst)
{
  nab_tls_select(1); nab_tls_take(dst); nab_tls_select(0);
}

int nab_https_vm_pending(uint8_t *out, uint32_t cap)
{
  int r;
  nab_tls_select(1); r = nab_tls_pending(out, cap); nab_tls_select(0);
  return r;
}

int nab_https_vm_out(const uint8_t *in, uint32_t len, uint8_t *out, uint32_t cap)
{
  int r;
  nab_tls_select(1); r = nab_tls_out(in, len, out, cap); nab_tls_select(0);
  return r;
}

int nab_https_vm_state(void)
{
  int r;
  nab_tls_select(1); r = nab_tls_state(); nab_tls_select(0);
  return r;
}

int nab_https_vm_close(uint8_t *out, uint32_t cap)
{
  int r;
  nab_tls_select(1); r = nab_tls_close(out, cap); nab_tls_select(0);
  return r;
}
