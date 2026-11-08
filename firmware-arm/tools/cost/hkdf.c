/* HKDF-SHA256 (RFC 5869) on the tree's hmac_sha256_v, and TLS 1.3's
 * HKDF-Expand-Label (RFC 8446 7.1) for outputs of at most one hash
 * length, which is every key, IV and secret the key schedule derives. */
#include <stdint.h>
#include <string.h>
#include "net/sha256.h"
#include "hkdf.h"

void hkdf_extract(uint8_t prk[32], const uint8_t *salt, uint32_t saltlen,
                  const uint8_t *ikm, uint32_t ikmlen)
{
  static const uint8_t zero[32] = {0};
  if (!salt) { salt = zero; saltlen = 32; }
  hmac_sha256(prk, salt, saltlen, ikm, ikmlen);
}

/* One block: okm = HMAC(prk, info || 0x01)[0..l), l <= 32. */
void hkdf_expand1(uint8_t *okm, uint32_t l, const uint8_t prk[32],
                  const uint8_t *info, uint32_t infolen)
{
  static const uint8_t one = 1;
  const uint8_t *pieces[2] = { info, &one };
  uint32_t lens[2] = { infolen, 1 };
  uint8_t t[32];
  hmac_sha256_v(t, prk, 32, pieces, lens, 2);
  memcpy(okm, t, l);
}

/* HkdfLabel = uint16 length, opaque label<7..255> = "tls13 " + label,
 * opaque context<0..255>. */
void tls13_expand_label(uint8_t *out, uint32_t l, const uint8_t secret[32],
                        const char *label, const uint8_t *ctx, uint32_t ctxlen)
{
  uint8_t info[2 + 1 + 6 + 32 + 1 + 32];
  uint32_t n = 0, ll = (uint32_t)strlen(label);
  info[n++] = (uint8_t)(l >> 8); info[n++] = (uint8_t)l;
  info[n++] = (uint8_t)(6 + ll);
  memcpy(info + n, "tls13 ", 6); n += 6;
  memcpy(info + n, label, ll); n += ll;
  info[n++] = (uint8_t)ctxlen;
  if (ctxlen) { memcpy(info + n, ctx, ctxlen); n += ctxlen; }
  hkdf_expand1(out, l, secret, info, n);
}
