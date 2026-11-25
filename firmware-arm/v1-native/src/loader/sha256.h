#ifndef SHA256_H
#define SHA256_H
#include <stdint.h>
/* HMAC-SHA-256, what the loader uses; sha256.asm, with SDCC's calling
 * convention (the arguments on its stack). A key of up to 64 bytes. */
void hmac_init(__data const uint8_t *key, uint8_t klen);
void hmac_update(__data const uint8_t *p, uint16_t n);
void hmac_final(__data uint8_t *out);     /* 32 bytes; forgets the key */

#ifdef SHA_TEST
/* The hash underneath, for the test image (shatest.c). */
extern uint32_t sha_total;                /* bytes hashed: the caller counts */
void sha_init(void);
void sha_byte(uint8_t v) __wparam;
void sha_digest(__data uint8_t *out);     /* the padding, the last block, 32 bytes out */
#endif
#endif
