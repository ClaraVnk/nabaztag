/* ChaCha20-Poly1305: RFC 8439's own vectors, then OpenSSL's output for
 * every awkward length, and every tampered byte refused. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net/chachapoly.h"
#include "vectors_chachapoly.h"

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("  FAIL  " __VA_ARGS__); printf("\n"); } } while (0)

static void hex(uint8_t *out, const char *h) { for (; *h; h += 2) *out++ = (uint8_t)strtol((char[3]){h[0], h[1], 0}, 0, 16); }

static void rfc8439(void)
{
  /* 2.4.2 */
  static const char sun[] = "Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.";
  uint8_t key[32], nonce[12], buf[200], want[200], tag[16], wtag[16], aad[12];
  for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
  hex(nonce, "000000000000004a00000000");
  memcpy(buf, sun, sizeof sun - 1);
  chacha20_xor(key, nonce, 1, buf, sizeof sun - 1);
  hex(want, "6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0bf91b65c5524733ab8f593dabcd62b3571639d624e65152ab8f530c359f0861d807ca0dbf500d6a6156a38e088a22b65e52bc514d16ccf806818ce91ab77937365af90bbf74a35be6b40b8eedf2785e42874d");
  CHECK(!memcmp(buf, want, sizeof sun - 1), "RFC 8439 2.4.2 ChaCha20");

  /* 2.5.2 */
  hex(key, "85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b");
  poly1305(tag, key, (const uint8_t *)"Cryptographic Forum Research Group", 34);
  hex(wtag, "a8061dc1305136c6c22b8baf0c0127a9");
  CHECK(!memcmp(tag, wtag, 16), "RFC 8439 2.5.2 Poly1305");

  /* 2.8.2 */
  hex(key, "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f");
  hex(nonce, "070000004041424344454647");
  hex(aad, "50515253c0c1c2c3c4c5c6c7");
  memcpy(buf, sun, sizeof sun - 1);
  chachapoly(key, nonce, aad, 12, buf, sizeof sun - 1, tag, 1);
  hex(want, "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d63dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b3692ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3ff4def08e4b7a9de576d26586cec64b6116");
  hex(wtag, "1ae10b594f09e26a7e902ecbd0600691");
  CHECK(!memcmp(buf, want, sizeof sun - 1) && !memcmp(tag, wtag, 16), "RFC 8439 2.8.2 AEAD");
  CHECK(chachapoly(key, nonce, aad, 12, buf, sizeof sun - 1, tag, 0) && !memcmp(buf, sun, sizeof sun - 1),
        "RFC 8439 2.8.2 opens");
}

static uint8_t b[20000];

static void openssl(void)
{
  for (unsigned i = 0; i < sizeof CP / sizeof *CP; i++) {
    uint32_t n = CP[i].len, a = CP[i].alen;
    uint8_t tag[16];
    memcpy(b, CP[i].p, n);
    chachapoly(CP[i].k, CP[i].n, CP[i].a, a, b, n, tag, 1);
    CHECK(!memcmp(b, CP[i].c, n) && !memcmp(tag, CP[i].c + n, 16), "seal %u bytes, aad %u", (unsigned)n, (unsigned)a);
    memcpy(b, CP[i].c, n); memcpy(tag, CP[i].c + n, 16);
    CHECK(chachapoly(CP[i].k, CP[i].n, CP[i].a, a, b, n, tag, 0) && !memcmp(b, CP[i].p, n),
          "open %u bytes, aad %u", (unsigned)n, (unsigned)a);
    memcpy(b, CP[i].c, n); memcpy(tag, CP[i].c + n, 16);
    if (n) { b[n / 2] ^= 0x10; }
    else tag[3] ^= 1;
    CHECK(!chachapoly(CP[i].k, CP[i].n, CP[i].a, a, b, n, tag, 0), "tampered %u bytes accepted", (unsigned)n);
    if (n) CHECK(b[n / 2] == (CP[i].c[n / 2] ^ 0x10), "a refused record is left untouched");
  }
}

int main(void)
{
  rfc8439();
  openssl();
  printf("  %s  %d checks, %d failed\n", fails ? "FAIL" : "ok  ", checks, fails);
  return fails != 0;
}
