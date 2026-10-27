/**
 * @file test_cmac.c
 * @brief The AES forward cipher, AES-CMAC, and what BIP must and must not
 *        protect.
 *
 * The forward cipher is checked against OpenSSL rather than against this
 * tree's own decrypt side: encrypt-then-decrypt agreeing proves the two
 * halves share a bug, not that either is AES.
 *
 * For BIP there is no library to compare against, and inventing an expected
 * MIC would mean deriving it from the code under test. So the tests here are
 * the properties that catch the mistake that actually happens -- masking the
 * wrong bits of the frame control field, which produces a MIC that verifies
 * against nothing and cannot be debugged from a rabbit with no console.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "net/cmac.h"
#include "vectors_cmac.h"

static int failures;

static void check(int ok, const char *what)
{
  printf("  %-54s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) failures++;
}

static void test_block(void)
{
  uint8_t out[16];

  printf("== the AES forward cipher, against OpenSSL\n");
  aes128_encrypt_block(ecb_key, out, ecb_in);
  check(memcmp(out, ecb_out, 16) == 0, "one block encrypts to OpenSSL's answer");

  /* Deliberately not checked by decrypting it again with this tree's other
     AES: two halves of one mistake agree with each other. OpenSSL is the
     only reference that means anything here. */
}

static void test_cmac(void)
{
  size_t i, n = sizeof CMAC_VECS / sizeof CMAC_VECS[0];
  int all = 1, scattered = 1;

  printf("== AES-128-CMAC against OpenSSL (%zu vectors)\n", n);
  for (i = 0; i < n; i++) {
    uint8_t tag[CMAC_TAG];
    aes_cmac(tag, CMAC_VECS[i].key, CMAC_VECS[i].msg, CMAC_VECS[i].len);
    if (memcmp(tag, CMAC_VECS[i].tag, CMAC_TAG) != 0) {
      all = 0;
      printf("    vector %zu mismatch (%u bytes)\n", i, CMAC_VECS[i].len);
    }
  }
  check(all, "every tag matches, empty message included");

  /* The scatter form is what BIP uses. Split every vector in three, with an
     empty piece in the middle, and the tag must not move. */
  for (i = 0; i < n; i++) {
    uint8_t tag[CMAC_TAG];
    const uint8_t *p[3];
    uint32_t l[3], cut = CMAC_VECS[i].len / 2;
    p[0] = CMAC_VECS[i].msg;        l[0] = cut;
    p[1] = 0;                       l[1] = 0;
    p[2] = CMAC_VECS[i].msg + cut;  l[2] = CMAC_VECS[i].len - cut;
    aes_cmac_v(tag, CMAC_VECS[i].key, p, l, 3);
    if (memcmp(tag, CMAC_VECS[i].tag, CMAC_TAG) != 0) {
      scattered = 0;
      printf("    scattered vector %zu mismatch (cut at %u)\n", i, cut);
    }
  }
  check(scattered, "splitting the message does not change the tag");

  /* A one-byte split, which lands the boundary inside the first block. */
  {
    int one = 1;
    for (i = 0; i < n; i++) {
      uint8_t tag[CMAC_TAG];
      const uint8_t *p[2];
      uint32_t l[2];
      if (CMAC_VECS[i].len == 0) continue;
      p[0] = CMAC_VECS[i].msg;      l[0] = 1;
      p[1] = CMAC_VECS[i].msg + 1;  l[1] = CMAC_VECS[i].len - 1;
      aes_cmac_v(tag, CMAC_VECS[i].key, p, l, 2);
      if (memcmp(tag, CMAC_VECS[i].tag, CMAC_TAG) != 0) one = 0;
    }
    check(one, "a one-byte first piece gives the same tag");
  }
}

static void test_bip(void)
{
  /* A plausible deauthenticate: subtype 12, then addresses. */
  uint8_t hdr[24] = {
    0xc0, 0x00,             /* frame control: deauth, no flags */
    0x3a, 0x01,             /* duration -- must not be covered */
    0x00, 0x1d, 0x92, 0x16, 0x4f, 0x1c,
    0x02, 0x00, 0x5e, 0x10, 0x00, 0x01,
    0x02, 0x00, 0x5e, 0x10, 0x00, 0x01,
    0x00, 0x00
  };
  uint8_t body[16] = { 0x02, 0x00 };   /* reason code + a stub MMIE */
  uint8_t igtk[16];
  uint8_t base[8], moved[8];
  uint8_t other[24];
  int i;

  printf("== BIP, by the properties that matter\n");
  for (i = 0; i < 16; i++) igtk[i] = (uint8_t)(0x40 + i);

  bip_cmac_128(base, igtk, hdr, body, sizeof body);

  /* The duration and the three masked flag bits are mutable in flight, so a
     MIC that covered them would fail on a perfectly good frame. */
  memcpy(other, hdr, 24);
  other[1] |= 0x38;          /* retry, power management, more data */
  other[2] = 0xff;
  other[3] = 0xff;           /* a different duration */
  bip_cmac_128(moved, igtk, other, body, sizeof body);
  check(memcmp(base, moved, 8) == 0,
        "duration and the three mutable flags do not change the MIC");

  /* Everything else must. A changed subtype is an attacker turning one
     management frame into another. */
  memcpy(other, hdr, 24);
  other[0] = 0xa0;           /* disassociate instead of deauthenticate */
  bip_cmac_128(moved, igtk, other, body, sizeof body);
  check(memcmp(base, moved, 8) != 0, "a changed subtype changes the MIC");

  memcpy(other, hdr, 24);
  other[1] |= 0x01;          /* a bit outside the mask */
  bip_cmac_128(moved, igtk, other, body, sizeof body);
  check(memcmp(base, moved, 8) != 0, "an unmasked flag bit changes the MIC");

  memcpy(other, hdr, 24);
  other[10] ^= 1;            /* a different transmitter address */
  bip_cmac_128(moved, igtk, other, body, sizeof body);
  check(memcmp(base, moved, 8) != 0, "a changed address changes the MIC");

  body[0] = 0x07;            /* a different reason code */
  bip_cmac_128(moved, igtk, hdr, body, sizeof body);
  check(memcmp(base, moved, 8) != 0, "a changed reason code changes the MIC");
  body[0] = 0x02;

  igtk[0] ^= 1;
  bip_cmac_128(moved, igtk, hdr, body, sizeof body);
  check(memcmp(base, moved, 8) != 0, "a different key changes the MIC");
}

int main(void)
{
  test_block();
  test_cmac();
  test_bip();
  printf("\n%s\n", failures ? "FAILURES" : "all good");
  return failures ? 1 : 0;
}
