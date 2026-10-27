/**
 * @file test_sha256.c
 * @brief SHA-256, HMAC-SHA-256 and the 802.11 KDF against OpenSSL.
 *
 * The lengths in the vector list are chosen around the padding boundary --
 * 55, 56 and 64 bytes -- because that is where a hash implementation goes
 * wrong, and where a vector set that only hashes "abc" tells you nothing.
 * The streaming path is checked separately by feeding the same message one
 * byte at a time: a one-shot that works and an update() that does not is a
 * real and quiet failure mode.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "net/sha256.h"
#include "vectors_sha256.h"

static int failures;

static void check(int ok, const char *what)
{
  printf("  %-54s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) failures++;
}

static void test_sha(void)
{
  size_t i, n = sizeof SHA256_VECS / sizeof SHA256_VECS[0];
  int all = 1, stream = 1;

  printf("== SHA-256 against OpenSSL (%zu vectors)\n", n);
  for (i = 0; i < n; i++) {
    uint8_t md[SHA256_DIGEST];
    sha256(md, SHA256_VECS[i].msg, SHA256_VECS[i].len);
    if (memcmp(md, SHA256_VECS[i].md, SHA256_DIGEST) != 0) {
      all = 0;
      printf("    vector %zu (%u bytes) mismatch\n", i, SHA256_VECS[i].len);
    }
  }
  check(all, "every digest matches");

  /* Same messages, one byte per update. */
  for (i = 0; i < n; i++) {
    uint8_t md[SHA256_DIGEST];
    sha256_ctx c;
    uint32_t j;
    sha256_init(&c);
    for (j = 0; j < SHA256_VECS[i].len; j++)
      sha256_update(&c, SHA256_VECS[i].msg + j, 1);
    sha256_final(&c, md);
    if (memcmp(md, SHA256_VECS[i].md, SHA256_DIGEST) != 0) {
      stream = 0;
      printf("    streamed vector %zu mismatch\n", i);
    }
  }
  check(stream, "one byte at a time gives the same digest");

  /* And in two uneven halves, which exercises the buffered path with a
     partial block already present. */
  {
    int halves = 1;
    for (i = 0; i < n; i++) {
      uint8_t md[SHA256_DIGEST];
      sha256_ctx c;
      uint32_t cut = SHA256_VECS[i].len / 3;
      sha256_init(&c);
      sha256_update(&c, SHA256_VECS[i].msg, cut);
      sha256_update(&c, SHA256_VECS[i].msg + cut, SHA256_VECS[i].len - cut);
      sha256_final(&c, md);
      if (memcmp(md, SHA256_VECS[i].md, SHA256_DIGEST) != 0) halves = 0;
    }
    check(halves, "split at a third gives the same digest");
  }
}

static void test_hmac(void)
{
  size_t i, n = sizeof HMAC256_VECS / sizeof HMAC256_VECS[0];
  int all = 1, split = 1;

  printf("== HMAC-SHA-256 against OpenSSL (%zu vectors)\n", n);
  for (i = 0; i < n; i++) {
    uint8_t md[SHA256_DIGEST];
    hmac_sha256(md, HMAC256_VECS[i].key, HMAC256_VECS[i].keylen,
                HMAC256_VECS[i].msg, HMAC256_VECS[i].msglen);
    if (memcmp(md, HMAC256_VECS[i].md, SHA256_DIGEST) != 0) {
      all = 0;
      printf("    vector %zu mismatch (keylen %u)\n", i, HMAC256_VECS[i].keylen);
    }
  }
  check(all, "every MAC matches, including a key longer than a block");

  /* The scatter form is what SAE actually calls: the same message, handed
     over in pieces, must give the same MAC. */
  for (i = 0; i < n; i++) {
    uint8_t md[SHA256_DIGEST];
    const uint8_t *p[3];
    uint32_t l[3], cut = HMAC256_VECS[i].msglen / 2;
    p[0] = HMAC256_VECS[i].msg;              l[0] = cut;
    p[1] = 0;                                l[1] = 0;
    p[2] = HMAC256_VECS[i].msg + cut;        l[2] = HMAC256_VECS[i].msglen - cut;
    hmac_sha256_v(md, HMAC256_VECS[i].key, HMAC256_VECS[i].keylen, p, l, 3);
    if (memcmp(md, HMAC256_VECS[i].md, SHA256_DIGEST) != 0) split = 0;
  }
  check(split, "the scattered form matches, and skips NULL pieces");
}

static void test_kdf(void)
{
  size_t i, n = sizeof KDF_VECS / sizeof KDF_VECS[0];
  int all = 1;

  printf("== 802.11 KDF, against a separate reading of 12.7.1.6.2\n");
  for (i = 0; i < n; i++) {
    uint8_t out[64];
    sha256_kdf(out, KDF_VECS[i].outlen, KDF_VECS[i].key, KDF_VECS[i].keylen,
               KDF_VECS[i].label, KDF_VECS[i].ctx, KDF_VECS[i].ctxlen);
    if (memcmp(out, KDF_VECS[i].out, KDF_VECS[i].outlen) != 0) {
      all = 0;
      printf("    vector %zu mismatch (%u bytes out)\n", i, KDF_VECS[i].outlen);
    }
  }
  check(all, "every KDF output matches");
  check(KDF_VECS[0].outlen == 64,
        "one vector crosses a digest boundary (the counter increments)");
}

int main(void)
{
  test_sha();
  test_hmac();
  test_kdf();
  printf("\n%s\n", failures ? "FAILURES" : "all good");
  return failures ? 1 : 0;
}
