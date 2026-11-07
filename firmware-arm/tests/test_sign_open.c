/* crypto_sign_open with one buffer — the call nab_sig.c actually makes.
 *
 * This test exists because that call silently never worked. nab_sig.c
 * composes [sig || payload] in a scratch buffer and passes it as BOTH the
 * input and the output of crypto_sign_open, on the stated grounds that only
 * the signature prefix gets overwritten. It does not:
 *
 *     FOR(i,n) m[i] = sm[i];
 *     FOR(i,32) m[i+32] = pk[i];     <- the public key lands on sm[32..63]
 *     ...
 *     scalarbase(q,sm + 32);         <- and this still needs sm[32..63]
 *
 * so with m == sm the second half of the signature is gone before it is read,
 * and the verification fails for every key, every message and every length.
 * verifySig therefore never returned 1 on any rabbit, no signed .sim could
 * install, and five months of flashes wrote nothing while reporting success.
 * Found 2026-11-07, offline, in the time it took to compile this file.
 *
 * The fix (apply-mods.py, step `sign_open_alias`) keeps the one-buffer API
 * and saves the 32 bytes scalarbase needs. This test is built against the
 * PATCHED tweetnacl that the firmware ships; `make sign_open` stages it.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tweetnacl.h"

static unsigned long long rng = 0x2611071234ULL;

/* Deterministic, so a failure is reproducible. Signing is deterministic in
 * Ed25519; only crypto_sign_keypair asks for randomness. */
void randombytes(unsigned char *p, unsigned long long n)
{
    for (unsigned long long i = 0; i < n; i++) {
        rng = rng * 6364136223846793005ULL + 1442695040888963407ULL;
        p[i] = (unsigned char)(rng >> 33);
    }
}

/* nab_sig.c's call, byte for byte: one buffer, in and out. */
static int verify_one_buffer(const unsigned char *msg, size_t len,
                             const unsigned char *sig, const unsigned char *pk)
{
    unsigned char *buf = malloc(len + 64);
    if (!buf) return -1;
    memcpy(buf, sig, 64);
    memcpy(buf + 64, msg, len);
    unsigned long long mlen = 0;
    int rc = crypto_sign_open(buf, &mlen, buf, len + 64, pk);
    free(buf);
    return rc == 0;
}

static int fails = 0;

static void check(const char *what, int got, int want)
{
    if (got != want) {
        printf("  FAIL %-46s got %d, wanted %d\n", what, got, want);
        fails++;
    } else {
        printf("  ok   %-46s %d\n", what, got);
    }
}

int main(void)
{
    unsigned char pk[32], sk[64];
    crypto_sign_keypair(pk, sk);

    /* Five bytes was enough to expose it; the real image is 107 600. */
    static const size_t sizes[] = { 1, 5, 63, 64, 65, 1024, 107600 };

    for (size_t s = 0; s < sizeof sizes / sizeof sizes[0]; s++) {
        size_t len = sizes[s];
        unsigned char *msg = malloc(len);
        unsigned char *sm  = malloc(len + 64);
        if (!msg || !sm) { printf("out of memory\n"); return 2; }
        randombytes(msg, len);
        unsigned long long smlen = 0;
        crypto_sign(sm, &smlen, msg, len, sk);

        char label[80];
        snprintf(label, sizeof label, "one buffer, %zu-byte message", len);
        check(label, verify_one_buffer(msg, len, sm, pk), 1);

        /* And it must still refuse a forgery, in both halves of the
         * signature -- the half scalarbase reads is the one the bug hid. */
        unsigned char *bad = malloc(64);
        memcpy(bad, sm, 64);
        bad[0] ^= 0x01;
        snprintf(label, sizeof label, "flipped bit in sig[0], %zu bytes", len);
        check(label, verify_one_buffer(msg, len, bad, pk), 0);
        memcpy(bad, sm, 64);
        bad[40] ^= 0x01;
        snprintf(label, sizeof label, "flipped bit in sig[40], %zu bytes", len);
        check(label, verify_one_buffer(msg, len, bad, pk), 0);

        /* A message changed under a good signature. */
        msg[len / 2] ^= 0x01;
        snprintf(label, sizeof label, "tampered message, %zu bytes", len);
        check(label, verify_one_buffer(msg, len, sm, pk), 0);

        free(bad); free(sm); free(msg);
    }

    /* The wrong key must not pass. */
    unsigned char pk2[32], sk2[64];
    crypto_sign_keypair(pk2, sk2);
    unsigned char msg[32], sm[96];
    unsigned long long smlen = 0;
    randombytes(msg, sizeof msg);
    crypto_sign(sm, &smlen, msg, sizeof msg, sk);
    check("signed by another key", verify_one_buffer(msg, sizeof msg, sm, pk2), 0);

    printf("%s\n", fails ? "FAILED" : "all good");
    return fails ? 1 : 0;
}
