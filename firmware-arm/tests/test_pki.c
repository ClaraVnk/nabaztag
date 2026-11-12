/* nab_pki: SHA-384, RSA, ECDSA and X.509 against OpenSSL's output
 * (vectors_pki.py), and every tampered variant refused. */
#include <stdio.h>
#include <string.h>

#include "net/nab_pki.h"
#include "vectors_pki.h"

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("  FAIL  " __VA_ARGS__); printf("\n"); } } while (0)

static uint8_t buf[2048];

static void sha384_vectors(void)
{
  uint8_t out[48];
  for (unsigned i = 0; i < sizeof SHA384_LENS / sizeof *SHA384_LENS; i++) {
    nab_sha384(out, SHA384_MSG, SHA384_LENS[i]);
    CHECK(!memcmp(out, SHA384_OUT + 48 * i, 48), "sha384 of %u bytes", (unsigned)SHA384_LENS[i]);
  }
}

static void rsa_vectors(void)
{
  for (unsigned i = 0; i < sizeof RSA_CASES / sizeof *RSA_CASES; i++) {
    const unsigned nl = RSA_CASES[i].nl, sl = RSA_CASES[i].sl, hl = RSA_CASES[i].hl;
    int pss = RSA_CASES[i].pss;
    int (*v)(const uint8_t *, uint32_t, const uint8_t *, uint32_t, const uint8_t *, uint32_t, nab_pki_tick)
      = pss ? nab_rsa_verify_pss : nab_rsa_verify_pkcs1;
    CHECK(v(RSA_CASES[i].n, nl, RSA_CASES[i].s, sl, RSA_CASES[i].h, hl, 0),
          "rsa %u bits %s sha%u valid", nl * 8, pss ? "pss" : "pkcs1", hl == 32 ? 256 : 384);
    memcpy(buf, RSA_CASES[i].s, sl); buf[sl / 2] ^= 1;
    CHECK(!v(RSA_CASES[i].n, nl, buf, sl, RSA_CASES[i].h, hl, 0), "rsa %u tampered signature accepted", i);
    memcpy(buf, RSA_CASES[i].h, hl); buf[0] ^= 0x80;
    CHECK(!v(RSA_CASES[i].n, nl, RSA_CASES[i].s, sl, buf, hl, 0), "rsa %u wrong hash accepted", i);
    /* the other padding never passes for this signature */
    CHECK(!(pss ? nab_rsa_verify_pkcs1 : nab_rsa_verify_pss)(RSA_CASES[i].n, nl, RSA_CASES[i].s, sl,
          RSA_CASES[i].h, hl, 0), "rsa %u accepted under the other padding", i);
  }
}

static void ec_vectors(void)
{
  for (unsigned i = 0; i < sizeof EC_CASES / sizeof *EC_CASES; i++) {
    const unsigned ql = EC_CASES[i].ql, sl = EC_CASES[i].sl, hl = EC_CASES[i].hl;
    int c = EC_CASES[i].curve;
    CHECK(nab_ecdsa_verify(c, EC_CASES[i].q, ql, EC_CASES[i].s, sl, EC_CASES[i].h, hl, 0),
          "ecdsa P-%d sha%u #%u valid", c ? 384 : 256, hl == 32 ? 256 : 384, i);
    /* the first byte: P-256 keeps only the leftmost 32 bytes of a SHA-384 */
    memcpy(buf, EC_CASES[i].h, hl); buf[0] ^= 1;
    CHECK(!nab_ecdsa_verify(c, EC_CASES[i].q, ql, EC_CASES[i].s, sl, buf, hl, 0), "ecdsa %u wrong hash accepted", i);
    memcpy(buf, EC_CASES[i].s, sl); buf[sl - 3] ^= 4;
    CHECK(!nab_ecdsa_verify(c, EC_CASES[i].q, ql, buf, sl, EC_CASES[i].h, hl, 0), "ecdsa %u tampered signature accepted", i);
    memcpy(buf, EC_CASES[i].q, ql); buf[ql - 1] ^= 1;
    CHECK(!nab_ecdsa_verify(c, buf, ql, EC_CASES[i].s, sl, EC_CASES[i].h, hl, 0), "ecdsa %u other key accepted", i);
    CHECK(!nab_ecdsa_verify(!c, EC_CASES[i].q, ql, EC_CASES[i].s, sl, EC_CASES[i].h, hl, 0), "ecdsa %u other curve accepted", i);
  }
}

static void chains(void)
{
  nab_cert root, iec, lec, irsa, lrsa, crit;
  CHECK(!nab_x509_parse(&root, ROOT, ROOT_len), "parse root");
  CHECK(!nab_x509_parse(&iec, INTER_EC, INTER_EC_len), "parse EC intermediate");
  CHECK(!nab_x509_parse(&lec, LEAF_EC, LEAF_EC_len), "parse EC leaf");
  CHECK(!nab_x509_parse(&irsa, INTER_RSA, INTER_RSA_len), "parse RSA intermediate");
  CHECK(!nab_x509_parse(&lrsa, LEAF_RSA, LEAF_RSA_len), "parse RSA leaf");
  CHECK(nab_x509_parse(&crit, LEAF_CRIT, LEAF_CRIT_len) == -1, "an unknown critical extension refuses the certificate");
  CHECK(root.ca && iec.ca && irsa.ca && !lec.ca && !lrsa.ca, "basicConstraints read");

  CHECK(nab_x509_names_host(&lec, "podcast.example.test"), "exact name");
  CHECK(nab_x509_names_host(&lec, "PODCAST.Example.test"), "names are not case-sensitive");
  CHECK(nab_x509_names_host(&lec, "eu.cdn.example.test"), "wildcard, one label");
  CHECK(!nab_x509_names_host(&lec, "a.eu.cdn.example.test"), "wildcard covers one label only");
  CHECK(!nab_x509_names_host(&lec, "cdn.example.test"), "wildcard needs a label");
  CHECK(!nab_x509_names_host(&lec, "podcast.example.test.evil"), "suffix is not a match");
  CHECK(!nab_x509_names_host(&lec, "radio.example.test"), "another name");
  CHECK(!nab_x509_names_host(&root, "Root"), "no SAN, no name");

  CHECK(nab_x509_valid_at(&lec, "20260612120000"), "inside validity");
  CHECK(!nab_x509_valid_at(&lec, "20251231235959"), "before validity");
  CHECK(!nab_x509_valid_at(&lec, "20270101000001"), "after validity");

  CHECK(nab_x509_issued_by(&lec, &iec) && nab_x509_issued_by(&iec, &root), "issuer names chain");
  CHECK(!nab_x509_issued_by(&lec, &irsa), "issuer of another chain");

  CHECK(nab_x509_signed_by(&lec, &iec.key_alg, &iec.key_param, &iec.key, 0), "P-256 leaf signed by P-384 (sha384)");
  CHECK(nab_x509_signed_by(&iec, &root.key_alg, &root.key_param, &root.key, 0), "P-384 intermediate signed by RSA-4096 (sha384)");
  CHECK(nab_x509_signed_by(&root, &root.key_alg, &root.key_param, &root.key, 0), "root self-signed");
  CHECK(nab_x509_signed_by(&lrsa, &irsa.key_alg, &irsa.key_param, &irsa.key, 0), "RSA leaf signed by RSA-2048 (sha256)");
  CHECK(nab_x509_signed_by(&irsa, &root.key_alg, &root.key_param, &root.key, 0), "RSA intermediate signed by root (sha256)");
  CHECK(!nab_x509_signed_by(&lec, &irsa.key_alg, &irsa.key_param, &irsa.key, 0), "leaf with another chain's key");
  CHECK(!nab_x509_signed_by(&lrsa, &iec.key_alg, &iec.key_param, &iec.key, 0), "RSA leaf with an EC key");

  /* One byte changed anywhere in the signed part breaks the signature. */
  memcpy(buf, LEAF_EC, LEAF_EC_len);
  for (uint32_t off = 20; off < (uint32_t)(lec.tbs.p - LEAF_EC) + lec.tbs.len; off += 37) {
    nab_cert t;
    buf[off] ^= 0x20;
    if (!nab_x509_parse(&t, buf, LEAF_EC_len))
      CHECK(!nab_x509_signed_by(&t, &iec.key_alg, &iec.key_param, &iec.key, 0), "byte %u changed, still signed", (unsigned)off);
    buf[off] ^= 0x20;
  }
  /* Truncated and garbage input never crashes, never parses as the whole. */
  for (uint32_t n = 0; n < LEAF_RSA_len; n += 13) {
    nab_cert t;
    CHECK(nab_x509_parse(&t, LEAF_RSA, n) == -1, "truncated to %u parsed", (unsigned)n);
  }
}

int main(void)
{
  sha384_vectors();
  rsa_vectors();
  ec_vectors();
  chains();
  printf("  %s  %d checks, %d failed\n", fails ? "FAIL" : "ok  ", checks, fails);
  return fails != 0;
}

/* tweetnacl.o wants one; nothing here draws from it. */
void randombytes(unsigned char *x, unsigned long long n) { memset(x, 0, (size_t)n); }
