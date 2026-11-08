#ifndef NAB_AES128_ENC_H
#define NAB_AES128_ENC_H
#include <stdint.h>
struct aes128_enc { uint8_t rk[176]; };
void aes128_enc_init(struct aes128_enc *ctx, const uint8_t key[16]);
void aes128_enc_block(const struct aes128_enc *ctx, uint8_t out[16], const uint8_t in[16]);
#endif
