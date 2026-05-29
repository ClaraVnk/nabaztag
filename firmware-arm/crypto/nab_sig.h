/* nab_sig.h — Ed25519 signature verification for .sim firmware images.
 *
 * Backed by TweetNaCl crypto_sign_open. The public key is embedded at
 * build time (inc/crypto/signing_pubkey.h, generated from
 * firmware-arm/keys/signing_pubkey.h).
 */
#ifndef NAB_SIG_H
#define NAB_SIG_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Returns 1 if signature is valid for payload under the embedded public key,
 * 0 otherwise. Requires payload_len + 64 bytes of scratch allocation.
 */
int nab_verify_sig(const uint8_t *payload, size_t payload_len,
                   const uint8_t *sig, void *scratch);

#ifdef __cplusplus
}
#endif

#endif /* NAB_SIG_H */
