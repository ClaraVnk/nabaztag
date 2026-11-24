#ifndef SHA256_H
#define SHA256_H
#include <stdint.h>
void hmac_init(__data const uint8_t *key, uint8_t klen);
void hmac_update(__data const uint8_t *p, uint16_t n);
void hmac_final(__data uint8_t *out);     /* 32 bytes; forgets the key */
#endif
