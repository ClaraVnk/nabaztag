/**
 * @file nab_pki.h
 * @brief What a TLS client needs to trust a server it has no shared key
 *        with: X.509 certificates, RSA and ECDSA signature checks, SHA-384.
 *
 * 2026-11-12. The rabbit speaks TLS 1.3 to its warren with a pre-shared key
 * (nab_tls.c); a podcast host has no such key, only a certificate chain.
 * This is the rest: read the chain, check each link's signature, check the
 * last one against an anchor built into the image, check the name.
 *
 * Public data only (certificates, signatures): nothing here is constant
 * time, and nothing here needs to be.
 */
#ifndef NAB_PKI_H
#define NAB_PKI_H

#include <stdint.h>

/* --- big numbers: Montgomery, 32-bit limbs, up to 4096 bits ------------- */

#define NAB_BN_LIMBS 128

typedef struct {
  int n;                               /* limbs in use */
  uint32_t n0;                         /* -m^-1 mod 2^32 */
  uint32_t m[NAB_BN_LIMBS];            /* the modulus, little-endian limbs */
  uint32_t rr[NAB_BN_LIMBS];           /* R^2 mod m */
} nab_bn_ctx;

typedef void (*nab_pki_tick)(void);

void nab_bn_init(nab_bn_ctx *c, const uint8_t *mod, uint32_t len);
void nab_bn_from_bytes(uint32_t *r, int n, const uint8_t *in, uint32_t len);
void nab_bn_to_bytes(uint8_t *out, uint32_t len, const uint32_t *a, int n);
int  nab_bn_cmp(const uint32_t *a, const uint32_t *b, int n);
uint32_t nab_bn_add(uint32_t *r, const uint32_t *a, const uint32_t *b, int n);
uint32_t nab_bn_sub(uint32_t *r, const uint32_t *a, const uint32_t *b, int n);
void nab_bn_mul(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a, const uint32_t *b);
void nab_bn_to_mont(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a);
void nab_bn_from_mont(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a);
/* r = a^e mod m; a and r plain integers below m, e big-endian bytes. */
void nab_bn_exp(const nab_bn_ctx *c, uint32_t *r, const uint32_t *a,
                const uint8_t *e, uint32_t elen, nab_pki_tick tick);

/* --- hashes ---------------------------------------------------------------- */

void nab_sha384(uint8_t out[48], const uint8_t *m, uint32_t n);

/* --- signatures -------------------------------------------------------------
 * The hash is computed by the caller: 32 bytes (SHA-256) or 48 (SHA-384).
 * Each returns 1 for a valid signature, 0 otherwise. */

int nab_rsa_verify_pkcs1(const uint8_t *mod, uint32_t mlen,
                         const uint8_t *sig, uint32_t slen,
                         const uint8_t *hash, uint32_t hlen, nab_pki_tick tick);
/* RSASSA-PSS, MGF1 with the same hash, salt length = hash length (the only
 * one TLS 1.3 allows, RFC 8446 4.2.3). */
int nab_rsa_verify_pss(const uint8_t *mod, uint32_t mlen,
                       const uint8_t *sig, uint32_t slen,
                       const uint8_t *hash, uint32_t hlen, nab_pki_tick tick);

#define NAB_P256 0
#define NAB_P384 1
/* pub: 0x04 || X || Y. sig: the DER ECDSA-Sig-Value. */
int nab_ecdsa_verify(int curve, const uint8_t *pub, uint32_t publen,
                     const uint8_t *sig, uint32_t siglen,
                     const uint8_t *hash, uint32_t hlen, nab_pki_tick tick);

/* --- X.509 ------------------------------------------------------------------ */

typedef struct { const uint8_t *p; uint32_t len; } nab_span;

typedef struct {
  nab_span tbs;                  /* the signed bytes, header included */
  nab_span issuer, subject;      /* Name, contents of the SEQUENCE */
  nab_span not_before, not_after;/* UTCTime or GeneralizedTime contents */
  nab_span key_alg, key_param;   /* OIDs of the SubjectPublicKeyInfo */
  nab_span key;                  /* the BIT STRING contents, unused-bits byte dropped */
  nab_span sig_alg;              /* OID of the outer signatureAlgorithm */
  nab_span sig;                  /* the signature, unused-bits byte dropped */
  nab_span san;                  /* subjectAltName extension value, or empty */
  int ca;                        /* basicConstraints cA */
} nab_cert;

/* 0 when parsed; -1 for anything malformed or an unknown critical extension. */
int nab_x509_parse(nab_cert *c, const uint8_t *der, uint32_t len);
/* now: "YYYYMMDDHHMMSS", UTC, from the warren's clock. */
int nab_x509_valid_at(const nab_cert *c, const char now[14]);
/* Not expired before floor ("YYYYMMDDHHMM"); notBefore unchecked. */
int nab_x509_not_expired(const nab_cert *c, const char floor[12]);
/* A subjectAltName dNSName equal to host, or "*.rest" covering one label. */
int nab_x509_names_host(const nab_cert *c, const char *host);
int nab_x509_issued_by(const nab_cert *child, const nab_cert *parent);
/* The child's signature, checked with a key given as SPKI parts. */
int nab_x509_signed_by(const nab_cert *child, const nab_span *key_alg,
                       const nab_span *key_param, const nab_span *key,
                       nab_pki_tick tick);
/* Any of the pieces above: verify `sig` over `hash` with an SPKI key.
 * scheme: NAB_SIG_PKCS1, NAB_SIG_PSS, NAB_SIG_ECDSA. */
#define NAB_SIG_PKCS1 1
#define NAB_SIG_PSS   2
#define NAB_SIG_ECDSA 3
int nab_pki_verify(const nab_span *key_alg, const nab_span *key_param,
                   const nab_span *key, int scheme,
                   const uint8_t *sig, uint32_t slen,
                   const uint8_t *hash, uint32_t hlen, nab_pki_tick tick);

#endif
