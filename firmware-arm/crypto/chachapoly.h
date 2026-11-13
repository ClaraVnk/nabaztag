/**
 * @file chachapoly.h
 * @brief ChaCha20-Poly1305 (RFC 8439), the AEAD of TLS_CHACHA20_POLY1305_SHA256.
 *
 * 2026-11-12. AES-128-GCM costs this ARM7 849 k instructions per KB, most
 * of it the bit-serial GHASH (tools/cost/results-2026-11-08.txt): a third
 * of the processor for a radio at 128 kbit/s. ChaCha20 is additions,
 * rotations and XORs on 32-bit words, and Poly1305 a 130-bit multiply in
 * 26-bit limbs: what this processor does well, with no table to keep.
 */
#ifndef CHACHAPOLY_H
#define CHACHAPOLY_H

#include <stdint.h>

/* Encrypt in place and write the tag (encrypt = 1); or check the tag and
   only then decrypt in place (encrypt = 0). Returns 1, or 0 for a bad tag
   (data untouched). */
int chachapoly(const uint8_t key[32], const uint8_t nonce[12],
               const uint8_t *aad, uint32_t aadlen,
               uint8_t *data, uint32_t len, uint8_t tag[16], int encrypt);

/* The two halves, for the tests against RFC 8439's own vectors. */
void chacha20_xor(const uint8_t key[32], const uint8_t nonce[12], uint32_t counter,
                  uint8_t *data, uint32_t len);
void poly1305(uint8_t tag[16], const uint8_t key[32], const uint8_t *m, uint32_t len);

#endif
