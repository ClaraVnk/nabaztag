/* nab_sig.c — Ed25519 verification wrapper.
 *
 * The caller supplies a scratch buffer of size (payload_len + 64). We
 * compose [sig || payload] there and call TweetNaCl crypto_sign_open,
 * which validates Ed25519 (no decryption, no key material exposure).
 *
 * crypto_sign_open also writes the message back to its output buffer; we
 * point it at the same scratch (in-place write of msg over sig||msg is
 * safe — only sig prefix gets overwritten by the message bytes).
 */

#include <string.h>
#include "tweetnacl.h"
#include "nab_sig.h"
#include "signing_pubkey.h"

/* TweetNaCl needs a randombytes() symbol even when only verifying; the
 * verify path never calls it, so a stub is enough to satisfy the linker.
 */
void randombytes(unsigned char *p, unsigned long long n)
{
    (void)p;
    (void)n;
}

int nab_verify_sig(const uint8_t *payload, size_t payload_len,
                   const uint8_t *sig, void *scratch)
{
    if (!payload || !sig || !scratch) return 0;

    unsigned char *buf = (unsigned char *)scratch;
    memcpy(buf, sig, 64);
    memcpy(buf + 64, payload, payload_len);

    unsigned long long mlen = 0;
    int rc = crypto_sign_open(buf, &mlen, buf, payload_len + 64,
                              nab_signing_pubkey);
    return (rc == 0) ? 1 : 0;
}
