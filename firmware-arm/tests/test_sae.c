/**
 * @file test_sae.c
 * @brief SAE against the standard's own vectors, then against itself.
 *
 * ## Known answers first (vectors_h2e.h)
 *
 * Until 2026-11-08 the peer in this file was this same code playing the
 * other role, and the header said at length why that proved less than it
 * looked: a misreading of the standard that is consistent on both sides --
 * the counter's endianness in the KDF, the order of the MAC addresses, a
 * label string -- passes a self-exchange every time. Two published vector
 * sets now settle those questions on a build machine:
 *
 *  - **IEEE 802.11-2020 Annex J.10**, as hostapd carries it. Hunting and
 *    pecking: STA-A, STA-B, a password, rand and mask, and the exact commit
 *    bytes that must come out; then the peer's commit and the KCK, PMK and
 *    PMKID. Hash-to-element: an SSID, a password, an identifier, two MAC
 *    addresses, and the PWE. One wrong byte anywhere in the derivation and
 *    nothing matches.
 *  - **RFC 9380 Appendix J.1.1** (P256_XMD:SHA-256_SSWU_RO_): five pairs of
 *    field elements with the points the Simplified SWU map must send them
 *    to, and their sums. That isolates the map itself.
 *
 * The generator recomputes every vector in Python before printing it, so
 * the expected values are not something a human typed either.
 *
 * ## Then the self-exchange
 *
 * Still worth running. It catches what a vector cannot: that the two
 * sides reach the same PMK by *different* routes (each computes K from
 * its own secret and the other's public commit), that the derivation is
 * symmetric in the two addresses, and above all what the code refuses
 * from a hostile peer -- a reflected commit, a scalar outside (1, n), an
 * element off the curve, a tampered confirm. Those rejections stand
 * between the rabbit and an attacker and depend on no vector at all.
 *
 * What neither settles: the frames around the arithmetic -- status codes,
 * where the token goes, the RSNXE. That is the emulator's job, and after
 * it a real access point's.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "net/sae.h"
#include "vectors_h2e.h"

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

static int same32(const uint8_t *a, const uint8_t *b)
{
  return memcmp(a, b, 32) == 0;
}

static void dump32(const char *label, const uint8_t *v)
{
  int i;
  printf("    %s ", label);
  for (i = 0; i < 32; i++) printf("%02x", v[i]);
  printf("\n");
}

/* --- the published vectors --------------------------------------------- */

/* An RNG that hands out a fixed sequence of 32-byte values: Annex J.10
 * fixes rand and mask, and sae_commit asks for rand first, then mask. */
struct seq_rng { const uint8_t *vals[2]; int next; };

static void seq_bytes(uint8_t *out, uint32_t len, void *arg)
{
  struct seq_rng *s = (struct seq_rng *)arg;
  memcpy(out, s->vals[s->next & 1], len);
  s->next++;
}

static void test_sswu_vectors(void)
{
  size_t i, n = sizeof SSWU_VECS / sizeof SSWU_VECS[0];
  int all_q = 1, all_p = 1;

  printf("== RFC 9380 J.1.1: the Simplified SWU map, P-256, z = -10\n");
  for (i = 0; i < n; i++) {
    const struct sswu_vec *v = &SSWU_VECS[i];
    p256_point q0, q1, p;
    int ok0 = p256_sswu(&q0, v->u0, 0) && same32(q0.x, v->q0x) && same32(q0.y, v->q0y);
    int ok1 = p256_sswu(&q1, v->u1, 0) && same32(q1.x, v->q1x) && same32(q1.y, v->q1y);
    int okp = ok0 && ok1 && p256_add(&p, &q0, &q1, 0) && same32(p.x, v->px) && same32(p.y, v->py);
    if (!ok0 || !ok1) {
      all_q = 0;
      printf("    vector %u:\n", (unsigned)i);
      dump32("u0      ", v->u0); dump32("Q0.x got", q0.x); dump32("Q0.x want", v->q0x);
      dump32("u1      ", v->u1); dump32("Q1.x got", q1.x); dump32("Q1.x want", v->q1x);
    }
    if (!okp) all_p = 0;
  }
  check(all_q, "SSWU(u) is the published point, 10 of 10");
  check(all_p, "Q0 + Q1 is the published P, 5 of 5");
}

static void test_annex_j_hunting_and_pecking(void)
{
  sae_ctx c;
  struct seq_rng rng = { { AJ_RAND, AJ_MASK }, 0 };
  uint8_t commit[SAE_COMMIT_LEN];

  printf("== IEEE 802.11-2020 Annex J.10: hunting and pecking\n");
  check(sae_commit(&c, AJ_ADDR1, AJ_ADDR2, AJ_PW, seq_bytes, &rng, 0),
        "the commit builds from the fixed rand and mask");
  check(!c.h2e, "and is marked hunting-and-pecking");
  if (!(same32(c.pwe.x, AJ_HNP_PWE_X) && same32(c.pwe.y, AJ_HNP_PWE_Y))) {
    dump32("PWE.x got ", c.pwe.x); dump32("PWE.x want", AJ_HNP_PWE_X);
  }
  check(same32(c.pwe.x, AJ_HNP_PWE_X) && same32(c.pwe.y, AJ_HNP_PWE_Y),
        "the PWE is the oracle's");
  sae_commit_bytes(&c, commit);
  check(sizeof commit == sizeof AJ_COMMIT &&
        memcmp(commit, AJ_COMMIT, sizeof commit) == 0,
        "the 98 commit bytes are the standard's");
  check(sae_peer_commit(&c, AJ_PEER_COMMIT, sizeof AJ_PEER_COMMIT, 0),
        "the standard's peer commit is accepted");
  if (!same32(c.kck, AJ_KCK)) { dump32("KCK got ", c.kck); dump32("KCK want", AJ_KCK); }
  check(same32(c.kck, AJ_KCK), "KCK");
  check(same32(c.pmk, AJ_PMK), "PMK");
  check(memcmp(c.pmkid, AJ_PMKID, 16) == 0, "PMKID");
  sae_clear(&c);
}

static void test_annex_j_hash_to_element(void)
{
  p256_point pt;
  sae_ctx a, b;
  struct prng ra = { 0x600d5eed }, rb = { 0x0ddba11 };
  uint8_t ca[SAE_COMMIT_LEN], cb[SAE_COMMIT_LEN];
  uint8_t fa[SAE_CONFIRM_LEN], fb[SAE_CONFIRM_LEN];

  printf("== IEEE 802.11-2020 Annex J.10: hash-to-element\n");
  check(sae_derive_pt(&pt, (const uint8_t *)AJ_SSID, (uint32_t)strlen(AJ_SSID),
                      AJ_PW, AJ_PWID, 0),
        "PT derives from SSID, password and identifier");
  if (!same32(pt.x, AJ_H2E_PT_X)) { dump32("PT.x got ", pt.x); dump32("PT.x want", AJ_H2E_PT_X); }
  check(same32(pt.x, AJ_H2E_PT_X) && same32(pt.y, AJ_H2E_PT_Y),
        "PT is the oracle's");
  check(sae_commit_pt(&a, AJ_ADDR1B, AJ_ADDR2B, &pt, prng_bytes, &ra, 0),
        "a commit builds from PT and the two addresses");
  check(a.h2e, "and is marked hash-to-element");
  if (!same32(a.pwe.x, AJ_H2E_PWE_X)) { dump32("PWE.x got ", a.pwe.x); dump32("PWE.x want", AJ_H2E_PWE_X); }
  check(same32(a.pwe.x, AJ_H2E_PWE_X) && same32(a.pwe.y, AJ_H2E_PWE_Y),
        "the PWE is the standard's (Annex J.10, group 19)");
  sae_clear(&a);

  /* Without an identifier, which is what a home network does. */
  check(sae_derive_pt(&pt, (const uint8_t *)AJ_SSID, (uint32_t)strlen(AJ_SSID),
                      AJ_PW, 0, 0) &&
        same32(pt.x, AJ_H2E_NOID_PT_X) && same32(pt.y, AJ_H2E_NOID_PT_Y),
        "without an identifier, PT is the oracle's");
  check(sae_commit_pt(&a, AJ_ADDR1, AJ_ADDR2, &pt, prng_bytes, &ra, 0) &&
        same32(a.pwe.x, AJ_H2E_NOID_PWE_X) && same32(a.pwe.y, AJ_H2E_NOID_PWE_Y),
        "and so is the PWE for STA-A and STA-B");

  /* A whole H2E exchange, the peer passing its addresses the other way
     round and starting from the same PT. */
  check(sae_commit_pt(&b, AJ_ADDR2, AJ_ADDR1, &pt, prng_bytes, &rb, 0),
        "the peer commits from the same PT");
  check(memcmp(&a.pwe, &b.pwe, sizeof a.pwe) == 0,
        "both sides derive the same PWE from PT");
  sae_commit_bytes(&a, ca);
  sae_commit_bytes(&b, cb);
  check(sae_peer_commit(&a, cb, sizeof cb, 0) && sae_peer_commit(&b, ca, sizeof ca, 0),
        "both accept the other's commit");
  sae_confirm_bytes(&a, fa);
  sae_confirm_bytes(&b, fb);
  check(sae_peer_confirm(&a, fb, sizeof fb) && sae_peer_confirm(&b, fa, sizeof fa),
        "both confirms verify");
  check(memcmp(a.pmk, b.pmk, sizeof a.pmk) == 0, "the same PMK");

  /* The anti-clogging token goes in a container element after the
     element with H2E, between the group and the scalar without. */
  {
    static const uint8_t token[5] = { 1, 2, 3, 4, 5 };
    uint8_t out[SAE_COMMIT_TOKEN_LEN];
    uint32_t n = sae_commit_bytes_token(&a, out, token, sizeof token);
    check(n == SAE_COMMIT_LEN + 3 + sizeof token &&
          memcmp(out, ca, SAE_COMMIT_LEN) == 0 &&
          out[SAE_COMMIT_LEN] == 255 && out[SAE_COMMIT_LEN + 1] == 6 &&
          out[SAE_COMMIT_LEN + 2] == 93 &&
          memcmp(out + SAE_COMMIT_LEN + 3, token, sizeof token) == 0,
          "with H2E the token rides in a container after the element");
    a.h2e = 0;
    n = sae_commit_bytes_token(&a, out, token, sizeof token);
    check(n == SAE_COMMIT_LEN + sizeof token &&
          memcmp(out + 2, token, sizeof token) == 0 &&
          memcmp(out + 2 + sizeof token, ca + 2, SAE_COMMIT_LEN - 2) == 0,
          "without it the token sits between group and scalar");
  }
  sae_clear(&a);
  sae_clear(&b);
}

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
  test_sswu_vectors();
  test_annex_j_hunting_and_pecking();
  test_annex_j_hash_to_element();
  test_exchange();
  test_wrong_password();
  test_different_password_different_pmk();
  test_rejections();
  test_clear();
  printf("\n%s\n", failures ? "FAILURES" : "all good");
  printf("\nNote: the arithmetic is checked against IEEE 802.11-2020 Annex J.10 and\n"
         "RFC 9380; the framing around it is the emulator's to check, then an AP's.\n");
  return failures ? 1 : 0;
}
