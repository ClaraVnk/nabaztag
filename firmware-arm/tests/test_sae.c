/**
 * @file test_sae.c
 * @brief A full SAE exchange, and what it does and does not prove.
 *
 * ## Read this before trusting the "all good" at the bottom
 *
 * Every other suite here compares against somebody else's implementation:
 * OpenSSL for AES, SHA-256, CMAC and P-256. There is no SAE in
 * python-cryptography, no published vector set to hand, and no hostapd on
 * the build machine -- so the peer in these tests is this same code playing
 * the other role.
 *
 * That is weaker, and worth being precise about rather than glossing.
 *
 * **What it does prove.** The two sides reach the same PMK by different
 * routes: each computes K from its own secret scalar and the other's public
 * commit, so the equality is a real property of the group arithmetic and
 * not an identity. It catches a wrong point multiplication, a wrong
 * negation, a scalar reduced mod the wrong modulus, a confirm whose piece
 * order differs between the sides, a PWE derivation that is not symmetric
 * in the two MAC addresses -- all of which are the likely mistakes.
 *
 * **What it cannot prove.** That this is SAE as a real access point
 * implements it. A misreading of the standard that is consistent on both
 * sides passes every test below: the little-endian counter in the KDF, the
 * order of the MAC addresses in the password hash, the exact label strings.
 * Those are settled only by associating with somebody else's access point,
 * and that test is a UniFi with a WPA3 SSID, not this file.
 *
 * So the suite is written to catch what it can and to say clearly what it
 * cannot. The rejections at the end are the part that matters most anyway:
 * they are what stands between the rabbit and a hostile peer, and they do
 * not depend on interop at all.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "net/sae.h"

static int failures;

static void check(int ok, const char *what)
{
  printf("  %-54s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) failures++;
}

/* A deterministic RNG, so a failure can be replayed. xorshift32, seeded per
 * context -- the point is reproducibility, not quality; the firmware passes
 * something seeded from the timer and the MAC. */
struct prng { uint32_t s; };

static void prng_bytes(uint8_t *out, uint32_t len, void *arg)
{
  struct prng *p = (struct prng *)arg;
  uint32_t i;
  for (i = 0; i < len; i++) {
    p->s ^= p->s << 13;
    p->s ^= p->s >> 17;
    p->s ^= p->s << 5;
    out[i] = (uint8_t)(p->s >> 24);
  }
}

static const uint8_t STA[6] = { 0x00, 0x1d, 0x92, 0x16, 0x4f, 0x1c };
static const uint8_t AP[6]  = { 0x02, 0x00, 0x5e, 0x10, 0x00, 0x01 };

/* Runs the whole exchange. Returns 1 if both sides accepted. */
static int exchange(sae_ctx *a, sae_ctx *b, const char *pw_a, const char *pw_b)
{
  struct prng ra = { 0x12345678u }, rb = { 0x9abcdef0u };
  uint8_t ca[SAE_COMMIT_LEN], cb[SAE_COMMIT_LEN];
  uint8_t fa[SAE_CONFIRM_LEN], fb[SAE_CONFIRM_LEN];

  if (!sae_commit(a, STA, AP, pw_a, prng_bytes, &ra, 0)) return 0;
  if (!sae_commit(b, AP, STA, pw_b, prng_bytes, &rb, 0)) return 0;

  sae_commit_bytes(a, ca);
  sae_commit_bytes(b, cb);

  if (!sae_peer_commit(a, cb, sizeof cb, 0)) return 0;
  if (!sae_peer_commit(b, ca, sizeof ca, 0)) return 0;

  sae_confirm_bytes(a, fa);
  sae_confirm_bytes(b, fb);

  if (!sae_peer_confirm(a, fb, sizeof fb)) return 0;
  if (!sae_peer_confirm(b, fa, sizeof fa)) return 0;
  return 1;
}

static void test_exchange(void)
{
  sae_ctx a, b;

  printf("== a full exchange, both sides agreeing\n");
  check(exchange(&a, &b, "le lapin de 2006", "le lapin de 2006"),
        "commit, confirm, accept, on both sides");
  check(a.state == SAE_ACCEPTED && b.state == SAE_ACCEPTED,
        "both reach SAE_ACCEPTED");
  check(memcmp(a.pmk, b.pmk, sizeof a.pmk) == 0, "the same PMK");
  check(memcmp(a.kck, b.kck, sizeof a.kck) == 0, "the same KCK");
  check(memcmp(a.pmkid, b.pmkid, sizeof a.pmkid) == 0, "the same PMKID");

  /* The PWE is derived from max(MAC)||min(MAC), so both sides must get the
     same point even though they pass their own address first. */
  check(memcmp(&a.pwe, &b.pwe, sizeof a.pwe) == 0,
        "the password element is symmetric in the two addresses");

  {
    uint8_t zeros[32];
    memset(zeros, 0, sizeof zeros);
    check(memcmp(a.pmk, zeros, sizeof zeros) != 0, "the PMK is not all zeroes");
  }
  sae_clear(&a);
  sae_clear(&b);
}

static void test_wrong_password(void)
{
  sae_ctx a, b;

  printf("== a password that does not match\n");
  check(!exchange(&a, &b, "le lapin de 2006", "le lapin de 2007"),
        "the exchange fails");
  /* It must fail at the confirm, not earlier: the commit carries no
     information about whether the password was right, which is the entire
     point of SAE. Both sides derive keys and only then disagree. */
  check(a.state == SAE_CONFIRMED || b.state == SAE_CONFIRMED,
        "and it fails at the confirm, not at the commit");
  sae_clear(&a);
  sae_clear(&b);
}

static void test_different_password_different_pmk(void)
{
  sae_ctx a1, b1, a2, b2;

  printf("== two passwords, two keys\n");
  if (exchange(&a1, &b1, "aaaa", "aaaa") &&
      exchange(&a2, &b2, "bbbb", "bbbb")) {
    check(memcmp(a1.pmk, a2.pmk, sizeof a1.pmk) != 0,
          "a different password gives a different PMK");
    check(memcmp(&a1.pwe, &a2.pwe, sizeof a1.pwe) != 0,
          "and a different password element");
  } else {
    check(0, "both exchanges complete");
  }
  sae_clear(&a1); sae_clear(&b1); sae_clear(&a2); sae_clear(&b2);
}

static void test_rejections(void)
{
  sae_ctx a, b;
  struct prng ra = { 0xfeedface }, rb = { 0x0badc0de };
  uint8_t ca[SAE_COMMIT_LEN], cb[SAE_COMMIT_LEN], bad[SAE_COMMIT_LEN];
  uint8_t fb[SAE_CONFIRM_LEN];

  printf("== what it refuses from a hostile peer\n");

  check(sae_commit(&a, STA, AP, "pw", prng_bytes, &ra, 0), "our commit builds");
  check(sae_commit(&b, AP, STA, "pw", prng_bytes, &rb, 0), "the peer's too");
  sae_commit_bytes(&a, ca);
  sae_commit_bytes(&b, cb);

  /* Our own commit reflected back at us. Accepting it lets an attacker who
     can relay frames complete an exchange with us against ourselves. */
  check(!sae_peer_commit(&a, ca, sizeof ca, 0),
        "our own commit reflected back is refused");

  /* A group that is not 19. */
  memcpy(bad, cb, sizeof bad);
  bad[0] = 20;
  check(!sae_peer_commit(&a, bad, sizeof bad, 0), "another group is refused");

  /* A scalar of zero, and a scalar of one: both outside (1, n). */
  memcpy(bad, cb, sizeof bad);
  memset(bad + 2, 0, 32);
  check(!sae_peer_commit(&a, bad, sizeof bad, 0), "a zero scalar is refused");
  bad[2 + 31] = 1;
  check(!sae_peer_commit(&a, bad, sizeof bad, 0), "a scalar of one is refused");

  /* An element that is not on the curve: invalid-curve attacks start here. */
  memcpy(bad, cb, sizeof bad);
  bad[2 + 32 + 31] ^= 1;
  check(!sae_peer_commit(&a, bad, sizeof bad, 0),
        "an element off the curve is refused");

  /* A truncated commit. */
  check(!sae_peer_commit(&a, cb, sizeof cb - 1, 0),
        "a short commit is refused");

  /* Now a legitimate peer commit, then a forged confirm. */
  check(sae_peer_commit(&a, cb, sizeof cb, 0), "the real commit is accepted");
  check(sae_peer_commit(&b, ca, sizeof ca, 0), "and on the other side");
  sae_confirm_bytes(&b, fb);
  fb[2 + 7] ^= 0x01;
  check(!sae_peer_confirm(&a, fb, sizeof fb), "a tampered confirm is refused");
  check(a.state != SAE_ACCEPTED, "and the state does not advance");
  fb[2 + 7] ^= 0x01;
  check(sae_peer_confirm(&a, fb, sizeof fb), "the real confirm is accepted");

  /* Order: a confirm before a commit must not be accepted. */
  {
    sae_ctx fresh;
    struct prng rc = { 0x5eed };
    uint8_t f[SAE_CONFIRM_LEN];
    memset(f, 0, sizeof f);
    check(sae_commit(&fresh, STA, AP, "pw", prng_bytes, &rc, 0), "a fresh context");
    check(!sae_peer_confirm(&fresh, f, sizeof f),
          "a confirm before the peer's commit is refused");
    sae_clear(&fresh);
  }

  sae_clear(&a);
  sae_clear(&b);
}

static void test_clear(void)
{
  sae_ctx a, b;
  uint8_t zeros[sizeof(sae_ctx)];

  printf("== wiping\n");
  if (!exchange(&a, &b, "pw", "pw")) { check(0, "exchange"); return; }
  sae_clear(&a);
  memset(zeros, 0, sizeof zeros);
  check(memcmp(&a, zeros, sizeof a) == 0, "sae_clear leaves nothing behind");
  sae_clear(&b);
}

int main(void)
{
  test_exchange();
  test_wrong_password();
  test_different_password_different_pmk();
  test_rejections();
  test_clear();
  printf("\n%s\n", failures ? "FAILURES" : "all good");
  printf("\nNote: the peer here is this same code. See the header comment for\n"
         "what that proves and what only a real access point can settle.\n");
  return failures ? 1 : 0;
}
