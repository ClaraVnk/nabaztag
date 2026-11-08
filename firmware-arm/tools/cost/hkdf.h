#ifndef NAB_HKDF_H
#define NAB_HKDF_H
#include <stdint.h>
void hkdf_extract(uint8_t prk[32], const uint8_t *salt, uint32_t saltlen,
                  const uint8_t *ikm, uint32_t ikmlen);
void hkdf_expand1(uint8_t *okm, uint32_t l, const uint8_t prk[32],
                  const uint8_t *info, uint32_t infolen);
void tls13_expand_label(uint8_t *out, uint32_t l, const uint8_t secret[32],
                        const char *label, const uint8_t *ctx, uint32_t ctxlen);
#endif
