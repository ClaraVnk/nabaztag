/**
 * @file test_md5.c
 * @brief MD5 and HMAC-MD5 from nabgcc's hash.c, against RFC 1321 and RFC 2202.
 *
 * apply-mods.py's md5_compact step replaces the 64 unrolled MD5 steps in
 * src/net/hash.c (2 176 bytes of ARM code) with a loop over two tables. The
 * firmware uses hmac_md5 for the MIC of WPA (TKIP) key frames, so a wrong
 * digest would be a rabbit that never finishes a TKIP handshake. This runs
 * the RFC's test vectors through the file -- the pristine file first, as the
 * control, then the patched one (the Makefile's `md5` target does both).
 *
 * MD5Init/Update/Final are static in hash.c, so the file is included rather
 * than linked: HASH_C names it.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include HASH_C

static int failures;

static void hex(const uint8_t *d, unsigned n, char *out)
{
  static const char *digits = "0123456789abcdef";
  unsigned i;
  for (i = 0; i < n; i++) { out[2 * i] = digits[d[i] >> 4]; out[2 * i + 1] = digits[d[i] & 15]; }
  out[2 * n] = 0;
}

static void check(const char *what, const uint8_t *got, const char *want)
{
  char s[33];
  hex(got, 16, s);
  printf("  %-40s %s\n", what, strcmp(s, want) == 0 ? "ok" : "FAIL");
  if (strcmp(s, want) != 0) { printf("    got  %s\n    want %s\n", s, want); failures++; }
}

static void md5_of(const char *msg, uint8_t out[16])
{
  struct MD5_CTX ctx;
  MD5Init(&ctx);
  MD5Update(&ctx, (const uint8_t *)msg, (uint32_t)strlen(msg));
  MD5Final(out, &ctx);
}

int main(void)
{
  /* RFC 1321, appendix A.5 */
  static const struct { const char *msg, *digest; } rfc1321[] = {
    { "", "d41d8cd98f00b204e9800998ecf8427e" },
    { "a", "0cc175b9c0f1b6a831c399e269772661" },
    { "abc", "900150983cd24fb0d6963f7d28e17f72" },
    { "message digest", "f96b697d7cb7938d525a2f31aaf161d0" },
    { "abcdefghijklmnopqrstuvwxyz", "c3fcd3d76192e4007dfb496cca67e13b" },
    { "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
      "d174ab98d277d9f5a5611c2c9f419d9f" },
    { "12345678901234567890123456789012345678901234567890123456789012345678901234567890",
      "57edf4a22be3c955ac49da2e2107b67a" },
  };
  uint8_t d[16];
  unsigned i;

  printf("== MD5, RFC 1321 A.5 (%s)\n", HASH_C);
  for (i = 0; i < sizeof rfc1321 / sizeof rfc1321[0]; i++) {
    char what[48];
    md5_of(rfc1321[i].msg, d);
    snprintf(what, sizeof what, "MD5(\"%.20s%s\")", rfc1321[i].msg, strlen(rfc1321[i].msg) > 20 ? "..." : "");
    check(what, d, rfc1321[i].digest);
  }

  /* A message long enough to go through several Update calls and blocks,
     fed byte by byte and in one go: both must agree with the one-shot. */
  {
    struct MD5_CTX ctx;
    uint8_t one[16], many[16];
    static uint8_t msg[1000];
    for (i = 0; i < sizeof msg; i++) msg[i] = (uint8_t)(i * 7 + 3);
    MD5Init(&ctx); MD5Update(&ctx, msg, sizeof msg); MD5Final(one, &ctx);
    MD5Init(&ctx); for (i = 0; i < sizeof msg; i++) MD5Update(&ctx, msg + i, 1); MD5Final(many, &ctx);
    printf("  %-40s %s\n", "1000 bytes, one call == 1000 calls", memcmp(one, many, 16) == 0 ? "ok" : "FAIL");
    if (memcmp(one, many, 16) != 0) failures++;
    check("MD5(1000 bytes, i*7+3)", one, "10046f077f2082ac19676b8079f1cb1a");  /* hashlib */
  }

  /* RFC 2202, section 2 */
  printf("== HMAC-MD5, RFC 2202\n");
  {
    uint8_t key[80], data[80];
    memset(key, 0x0b, 16);
    hmac_md5(key, 16, (const uint8_t *)"Hi There", 8, d);
    check("test case 1", d, "9294727a3638bb1c13f48ef8158bfc9d");
    hmac_md5((const uint8_t *)"Jefe", 4, (const uint8_t *)"what do ya want for nothing?", 28, d);
    check("test case 2", d, "750c783e6ab0b503eaa86e310a5db738");
    memset(key, 0xaa, 16); memset(data, 0xdd, 50);
    hmac_md5(key, 16, data, 50, d);
    check("test case 3", d, "56be34521d144c88dbb8c733f0e8b3f6");
    for (i = 0; i < 25; i++) key[i] = (uint8_t)(i + 1);
    memset(data, 0xcd, 50);
    hmac_md5(key, 25, data, 50, d);
    check("test case 4", d, "697eaf0aca3a3aea3a75164746ffaa79");
    memset(key, 0x0c, 16);
    hmac_md5(key, 16, (const uint8_t *)"Test With Truncation", 20, d);
    check("test case 5", d, "56461ef2342edc00f9bab995690efd4c");
    memset(key, 0xaa, 80);
    hmac_md5(key, 80, (const uint8_t *)"Test Using Larger Than Block-Size Key - Hash Key First", 54, d);
    check("test case 6", d, "6b1ab7fe4bd7bf8f0b62e6ce61b9d0cd");
    hmac_md5(key, 80, (const uint8_t *)"Test Using Larger Than Block-Size Key and Larger Than One Block-Size Data", 73, d);
    check("test case 7", d, "6f630fad67cda0ee1fb1f562db3aa53e");
  }

  printf("%d failure(s)\n", failures);
  return failures ? 1 : 0;
}
