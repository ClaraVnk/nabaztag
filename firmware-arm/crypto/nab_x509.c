/**
 * @file nab_x509.c
 * @brief The least of X.509 (RFC 5280) a TLS client needs: the signed
 *        part, issuer and subject, validity, key, signature, the
 *        subjectAltName DNS names, basicConstraints. Any other extension
 *        marked critical refuses the certificate, as the RFC requires.
 */
#include <string.h>

#include "net/nab_pki.h"
#include "net/sha256.h"

/* One TLV with the expected tag: its contents in *out, *p past it. */
static int tlv(const uint8_t **p, const uint8_t *end, uint8_t tag, nab_span *out)
{
  const uint8_t *q = *p;
  if (end - q < 2 || q[0] != tag) return -1;
  uint32_t len = q[1];
  q += 2;
  if (len & 0x80) {
    uint32_t k = len & 0x7f;
    if (k < 1 || k > 3 || (uint32_t)(end - q) < k) return -1;
    for (len = 0; k--; ) len = (len << 8) | *q++;
  }
  if ((uint32_t)(end - q) < len) return -1;
  out->p = q;
  out->len = len;
  *p = q + len;
  return 0;
}

static int any(const uint8_t **p, const uint8_t *end)
{
  nab_span s;
  return *p < end ? tlv(p, end, **p, &s) : -1;
}

static int oid3(const nab_span *o, uint8_t last)          /* 2.5.29.<last> */
{ return o->len == 3 && o->p[0] == 0x55 && o->p[1] == 0x1d && o->p[2] == last; }

static int extension(nab_cert *c, nab_span e)
{
  const uint8_t *p = e.p, *end = e.p + e.len;
  nab_span oid, crit = {0, 0}, val;
  if (tlv(&p, end, 0x06, &oid)) return -1;
  if (p < end && *p == 0x01 && tlv(&p, end, 0x01, &crit)) return -1;
  if (tlv(&p, end, 0x04, &val) || p != end) return -1;
  if (oid3(&oid, 17)) { c->san = val; return 0; }                    /* subjectAltName */
  if (oid3(&oid, 19)) {                                              /* basicConstraints */
    const uint8_t *q = val.p, *qe = val.p + val.len;
    nab_span seq, b;
    if (tlv(&q, qe, 0x30, &seq)) return -1;
    q = seq.p; qe = seq.p + seq.len;
    c->ca = q < qe && *q == 0x01 && !tlv(&q, qe, 0x01, &b) && b.len == 1 && b.p[0];
    return 0;
  }
  /* keyUsage, extKeyUsage, certificatePolicies, name and policy
     constraints and the key identifiers: read and ignored here, since
     the chain check below is what this client relies on. */
  if (oid3(&oid, 15) || oid3(&oid, 37) || oid3(&oid, 32) || oid3(&oid, 30)
      || oid3(&oid, 36) || oid3(&oid, 14) || oid3(&oid, 35) || oid3(&oid, 31))
    return 0;
  return crit.len && crit.p[0] ? -1 : 0;
}

int nab_x509_parse(nab_cert *c, const uint8_t *der, uint32_t len)
{
  const uint8_t *p = der, *end = der + len, *t;
  nab_span top, tbs, v, spki, alg, bits;
  memset(c, 0, sizeof *c);
  if (tlv(&p, end, 0x30, &top) || p != end) return -1;
  p = top.p; end = top.p + top.len;
  t = p;
  if (tlv(&p, end, 0x30, &tbs)) return -1;
  c->tbs.p = t; c->tbs.len = (uint32_t)(p - t);
  { nab_span sa; const uint8_t *q;
    if (tlv(&p, end, 0x30, &sa)) return -1;
    q = sa.p;
    if (tlv(&q, sa.p + sa.len, 0x06, &c->sig_alg)) return -1; }
  if (tlv(&p, end, 0x03, &bits) || p != end || bits.len < 2 || bits.p[0]) return -1;
  c->sig.p = bits.p + 1; c->sig.len = bits.len - 1;

  p = tbs.p; end = tbs.p + tbs.len;
  if (p < end && *p == 0xa0 && any(&p, end)) return -1;           /* version */
  if (any(&p, end) || any(&p, end)) return -1;                    /* serial, signature */
  if (tlv(&p, end, 0x30, &c->issuer) || tlv(&p, end, 0x30, &v)) return -1;
  { const uint8_t *q = v.p, *qe = v.p + v.len;
    if (q >= qe || tlv(&q, qe, *q, &c->not_before) || q >= qe
        || tlv(&q, qe, *q, &c->not_after)) return -1; }
  if (tlv(&p, end, 0x30, &c->subject) || tlv(&p, end, 0x30, &spki)) return -1;
  { const uint8_t *q = spki.p, *qe = spki.p + spki.len, *a;
    if (tlv(&q, qe, 0x30, &alg) || tlv(&q, qe, 0x03, &bits) || bits.len < 2 || bits.p[0])
      return -1;
    c->key.p = bits.p + 1; c->key.len = bits.len - 1;
    a = alg.p;
    if (tlv(&a, alg.p + alg.len, 0x06, &c->key_alg)) return -1;
    if (a < alg.p + alg.len && *a == 0x06 && tlv(&a, alg.p + alg.len, 0x06, &c->key_param))
      return -1; }
  while (p < end && *p != 0xa3) if (any(&p, end)) return -1;     /* unique ids */
  if (p < end) {
    nab_span ex, seq, e;
    const uint8_t *q;
    if (tlv(&p, end, 0xa3, &ex)) return -1;
    q = ex.p;
    if (tlv(&q, ex.p + ex.len, 0x30, &seq)) return -1;
    q = seq.p;
    while (q < seq.p + seq.len)
      if (tlv(&q, seq.p + seq.len, 0x30, &e) || extension(c, e)) return -1;
  }
  return p == end ? 0 : -1;
}

/* UTCTime YYMMDDHHMMSSZ (50..99 = 19xx) or GeneralizedTime YYYYMMDDHHMMSSZ. */
static int when(const nab_span *s, char out[14])
{
  if (s->len == 13 && s->p[12] == 'Z') {
    out[0] = s->p[0] < '5' ? '2' : '1';
    out[1] = s->p[0] < '5' ? '0' : '9';
    memcpy(out + 2, s->p, 12);
    return 0;
  }
  if (s->len == 15 && s->p[14] == 'Z') { memcpy(out, s->p, 14); return 0; }
  return -1;
}

int nab_x509_valid_at(const nab_cert *c, const char now[14])
{
  char a[14], b[14];
  if (when(&c->not_before, a) || when(&c->not_after, b)) return 0;
  return memcmp(a, now, 14) <= 0 && memcmp(now, b, 14) <= 0;
}

static int same_host(const uint8_t *a, uint32_t n, const char *b)
{
  for (uint32_t i = 0; i < n; i++) {
    uint8_t x = a[i], y = (uint8_t)b[i];
    if (!y) return 0;
    if (x >= 'A' && x <= 'Z') x |= 0x20;
    if (y >= 'A' && y <= 'Z') y |= 0x20;
    if (x != y) return 0;
  }
  return b[n] == 0;
}

int nab_x509_names_host(const nab_cert *c, const char *host)
{
  const uint8_t *p = c->san.p, *end;
  nab_span seq, n;
  if (!c->san.len || tlv(&p, c->san.p + c->san.len, 0x30, &seq)) return 0;
  p = seq.p; end = seq.p + seq.len;
  while (p < end) {
    uint8_t tag = *p;
    if (tlv(&p, end, tag, &n)) return 0;
    if (tag != 0x82 || !n.len) continue;                              /* dNSName */
    if (same_host(n.p, n.len, host)) return 1;
    if (n.len > 2 && n.p[0] == '*' && n.p[1] == '.') {                /* one label */
      const char *dot = strchr(host, '.');
      if (dot && dot != host && same_host(n.p + 2, n.len - 2, dot + 1)) return 1;
    }
  }
  return 0;
}

int nab_x509_issued_by(const nab_cert *child, const nab_cert *parent)
{
  return child->issuer.len == parent->subject.len
      && !memcmp(child->issuer.p, parent->subject.p, child->issuer.len);
}

/* --- signatures over certificates and over the TLS transcript ------------ */

static const uint8_t OID_RSA[9]     = {0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x01,0x01};
static const uint8_t OID_EC[7]      = {0x2a,0x86,0x48,0xce,0x3d,0x02,0x01};
static const uint8_t OID_P256[8]    = {0x2a,0x86,0x48,0xce,0x3d,0x03,0x01,0x07};
static const uint8_t OID_P384[5]    = {0x2b,0x81,0x04,0x00,0x22};
static const uint8_t OID_RSA256[9]  = {0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x01,0x0b};
static const uint8_t OID_RSA384[9]  = {0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x01,0x0c};
static const uint8_t OID_ECDSA256[8] = {0x2a,0x86,0x48,0xce,0x3d,0x04,0x03,0x02};
static const uint8_t OID_ECDSA384[8] = {0x2a,0x86,0x48,0xce,0x3d,0x04,0x03,0x03};

static int is(const nab_span *s, const uint8_t *oid, uint32_t n)
{ return s->len == n && !memcmp(s->p, oid, n); }

/* The modulus inside an RSAPublicKey SEQUENCE { INTEGER n, INTEGER e }. */
static int rsa_modulus(const nab_span *key, nab_span *mod)
{
  const uint8_t *p = key->p, *end = key->p + key->len;
  nab_span seq, e;
  if (tlv(&p, end, 0x30, &seq)) return -1;
  p = seq.p; end = seq.p + seq.len;
  if (tlv(&p, end, 0x02, mod) || tlv(&p, end, 0x02, &e)) return -1;
  /* e = 65537 only: every public CA uses it, and it is all nab_rsa does. */
  while (e.len && !*e.p) { e.p++; e.len--; }
  return e.len == 3 && e.p[0] == 1 && e.p[1] == 0 && e.p[2] == 1 ? 0 : -1;
}

int nab_pki_verify(const nab_span *key_alg, const nab_span *key_param,
                   const nab_span *key, int scheme,
                   const uint8_t *sig, uint32_t slen,
                   const uint8_t *hash, uint32_t hlen, nab_pki_tick tick)
{
  if (is(key_alg, OID_RSA, sizeof OID_RSA)) {
    nab_span mod;
    if (rsa_modulus(key, &mod)) return 0;
    if (scheme == NAB_SIG_PKCS1)
      return nab_rsa_verify_pkcs1(mod.p, mod.len, sig, slen, hash, hlen, tick);
    if (scheme == NAB_SIG_PSS)
      return nab_rsa_verify_pss(mod.p, mod.len, sig, slen, hash, hlen, tick);
    return 0;
  }
  if (is(key_alg, OID_EC, sizeof OID_EC) && scheme == NAB_SIG_ECDSA) {
    int curve = is(key_param, OID_P256, sizeof OID_P256) ? NAB_P256
              : is(key_param, OID_P384, sizeof OID_P384) ? NAB_P384 : -1;
    if (curve < 0) return 0;
    return nab_ecdsa_verify(curve, key->p, key->len, sig, slen, hash, hlen, tick);
  }
  return 0;
}

int nab_x509_signed_by(const nab_cert *child, const nab_span *key_alg,
                       const nab_span *key_param, const nab_span *key,
                       nab_pki_tick tick)
{
  uint8_t h[48];
  uint32_t hl;
  int scheme;
  if (is(&child->sig_alg, OID_RSA256, 9)) { hl = 32; scheme = NAB_SIG_PKCS1; }
  else if (is(&child->sig_alg, OID_RSA384, 9)) { hl = 48; scheme = NAB_SIG_PKCS1; }
  else if (is(&child->sig_alg, OID_ECDSA256, 8)) { hl = 32; scheme = NAB_SIG_ECDSA; }
  else if (is(&child->sig_alg, OID_ECDSA384, 8)) { hl = 48; scheme = NAB_SIG_ECDSA; }
  else return 0;
  if (hl == 32) sha256(h, child->tbs.p, child->tbs.len);
  else nab_sha384(h, child->tbs.p, child->tbs.len);
  return nab_pki_verify(key_alg, key_param, key, scheme,
                        child->sig.p, child->sig.len, h, hl, tick);
}
