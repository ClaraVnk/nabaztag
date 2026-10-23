/**
 * @file vapcrypto.h
 * @brief The crypto the virtual AP needs and the firmware does not have.
 */
#ifndef _VAPCRYPTO_H_
#define _VAPCRYPTO_H_

#include <stdint.h>

int  vap_aes_key_wrap(const uint8_t kek[16], const uint8_t *in, uint32_t inlen,
                      uint8_t *out);
void vap_compute_ptk(const uint8_t *pmk,
                     const uint8_t *anonce, const uint8_t *aa,
                     const uint8_t *snonce, const uint8_t *sa,
                     uint8_t *ptk, uint32_t length);
int  vap_crypto_selftest(void);

#endif /* _VAPCRYPTO_H_ */
