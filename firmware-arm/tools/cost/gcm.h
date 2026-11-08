#ifndef NAB_GCM_H
#define NAB_GCM_H
#include <stdint.h>
#include "aes128_enc.h"
/* Encrypts buf in place, 12-byte IV, tag out. */
void gcm_encrypt(const struct aes128_enc *aes, const uint8_t iv[12],
                 const uint8_t *aad, uint32_t aadlen,
                 uint8_t *buf, uint32_t len, uint8_t tag[16]);
#endif
