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

/* CLR_WDT lives in common.h and expands to a write to WDTCON, which is in
 * ml674061.h; under _NAB_SIM it expands to nothing. */
#include "common.h"
#ifndef _NAB_SIM
#include "ml674061.h"
#endif

/* Called from the two long loops of crypto_sign_open (apply-mods.py's
 * verify_wdt step puts the calls there). verifySig is one opcode and the
 * interpreter feeds the watchdog once per opcode; the check itself is
 * 247,618,040 instructions on this CPU, measured under qemu-arm 2026-11-08,
 * against a 2.09 s window. Without this the rabbit resets inside the opcode. */
void nab_verify_tick(void)
{
    CLR_WDT;
}

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
