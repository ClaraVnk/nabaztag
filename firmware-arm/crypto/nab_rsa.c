/**
 * @file nab_rsa.c
 * @brief RSA signature checks, e = 65537: PKCS#1 v1.5 (the certificate
 *        chains) and PSS (TLS 1.3's CertificateVerify, RFC 8446 4.2.3),
 *        with SHA-256 or SHA-384. Moduli up to 4096 bits.
 */
#include <string.h>

#include "net/nab_pki.h"
#include "net/sha256.h"

#ifdef __ELF__
#define SECTION_EXTBSS __attribute__((section(".extbss")))
#else
#define SECTION_EXTBSS
#endif

/* 1.5 KB of state: external RAM on the rabbit, like nab_tls.c's. */
static struct {
  nab_bn_ctx k;
  uint32_t s[NAB_BN_LIMBS], m[NAB_BN_LIMBS];
  uint8_t em[NAB_BN_LIMBS * 4];
} R SECTION_EXTBSS;

/* DigestInfo prefixes, RFC 8017 9.2 note 1. */
static const uint8_t DI256[19] = {0x30,0x31,0x30,0x0d,0x06,0x09,0x60,0x86,0x48,0x01,
                                  0x65,0x03,0x04,0x02,0x01,0x05,0x00,0x04,0x20};
static const uint8_t DI384[19] = {0x30,0x41,0x30,0x0d,0x06,0x09,0x60,0x86,0x48,0x01,
                                  0x65,0x03,0x04,0x02,0x02,0x05,0x00,0x04,0x30};
static const uint8_t E65537[3] = {1, 0, 1};

/* EM = sig^e mod n, as mlen bytes; -1 when the signature cannot be one. */
static int open_sig(const uint8_t *mod, uint32_t mlen, const uint8_t *sig,
                    uint32_t slen, nab_pki_tick tick)
{
  while (mlen && !*mod) { mod++; mlen--; }
  if (mlen < 128 || mlen > sizeof R.em || slen != mlen || !(mod[mlen - 1] & 1)) return -1;
  nab_bn_init(&R.k, mod, mlen);
  nab_bn_from_bytes(R.s, R.k.n, sig, slen);
  if (nab_bn_cmp(R.s, R.k.m, R.k.n) >= 0) return -1;
  nab_bn_exp(&R.k, R.m, R.s, E65537, sizeof E65537, tick);
  nab_bn_to_bytes(R.em, mlen, R.m, R.k.n);
  return (int)mlen;
}

static void hash(uint8_t *out, const uint8_t *in, uint32_t len, uint32_t hlen)
{
  if (hlen == 32) sha256(out, in, len);
  else nab_sha384(out, in, len);
}

int nab_rsa_verify_pkcs1(const uint8_t *mod, uint32_t mlen, const uint8_t *sig,
                         uint32_t slen, const uint8_t *h, uint32_t hlen,
                         nab_pki_tick tick)
{
  int k = open_sig(mod, mlen, sig, slen, tick);
  if (k < 0 || (hlen != 32 && hlen != 48)) return 0;
  uint32_t t = 19 + hlen;
  if ((uint32_t)k < t + 11) return 0;
  uint32_t pad = (uint32_t)k - 3 - t;
  /* Rebuilt and compared whole, never parsed: 00 01 FF.. 00 DigestInfo H. */
  uint8_t *em = R.em;
  int bad = em[0] | (em[1] ^ 1) | em[2 + pad];
  for (uint32_t i = 0; i < pad; i++) bad |= em[2 + i] ^ 0xff;
  bad |= memcmp(em + 3 + pad, hlen == 32 ? DI256 : DI384, 19);
  bad |= memcmp(em + 3 + pad + 19, h, hlen);
  return !bad;
}

int nab_rsa_verify_pss(const uint8_t *mod, uint32_t mlen, const uint8_t *sig,
                       uint32_t slen, const uint8_t *mhash, uint32_t hlen,
                       nab_pki_tick tick)
{
  uint8_t cnt[48 + 4], mask[48], mp[8 + 48 + 48];
  int k = open_sig(mod, mlen, sig, slen, tick);
  if (k < 0 || (hlen != 32 && hlen != 48)) return 0;
  /* RFC 8017 9.1.2 with emBits = modBits - 1. The modulus starts at a
     whole byte in practice (2048, 3072, 4096 bits), so EM is one byte
     shorter than the signature and its first byte is zero. */
  const uint8_t *m = mod;
  while (!*m) m++;
  uint32_t modbits = 8 * (uint32_t)k;
  for (uint8_t top = m[0]; !(top & 0x80); top <<= 1) modbits--;
  uint32_t embits = modbits - 1, emlen = (embits + 7) / 8;
  uint8_t *em = R.em + ((uint32_t)k - emlen);
  if ((uint32_t)k != emlen && R.em[0]) return 0;
  if (emlen < 2 * hlen + 2 || em[emlen - 1] != 0xbc) return 0;
  uint32_t dblen = emlen - hlen - 1;
  uint8_t *db = em, *h = em + dblen;
  if (db[0] & (uint8_t)(0xff << (8 - (8 * emlen - embits)))) return 0;
  for (uint32_t i = 0; i < dblen; i += hlen) {         /* MGF1 */
    uint32_t c = i / hlen;
    memcpy(cnt, h, hlen);
    cnt[hlen] = (uint8_t)(c >> 24); cnt[hlen + 1] = (uint8_t)(c >> 16);
    cnt[hlen + 2] = (uint8_t)(c >> 8); cnt[hlen + 3] = (uint8_t)c;
    hash(mask, cnt, hlen + 4, hlen);
    for (uint32_t j = 0; j < hlen && i + j < dblen; j++) db[i + j] ^= mask[j];
  }
  db[0] &= (uint8_t)(0xff >> (8 * emlen - embits));
  uint32_t ps = dblen - hlen - 1;
  for (uint32_t i = 0; i < ps; i++) if (db[i]) return 0;
  if (db[ps] != 1) return 0;
  memset(mp, 0, 8);
  memcpy(mp + 8, mhash, hlen);
  memcpy(mp + 8 + hlen, db + ps + 1, hlen);
  hash(mask, mp, 8 + 2 * hlen, hlen);
  return !memcmp(mask, h, hlen);
}
