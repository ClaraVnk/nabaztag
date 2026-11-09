/**
 * @file vaph2e.h
 * @brief SAE hash-to-element as the virtual AP derives it, independently of
 *        the rabbit's crypto. See vaph2e.c.
 */
#ifndef _VAPH2E_H_
#define _VAPH2E_H_

#include <stdint.h>

/** PT (802.11-2020 12.4.4.2.3) as x || y, 64 bytes big-endian. `identifier`
 *  may be NULL. Returns 1, or 0 if the two SSWU points cancel. */
int vap_h2e_pt(uint8_t pt_out[64], const char *ssid, const char *password,
               const char *identifier);

/** PWE (12.4.5.2) from a PT and the two addresses, x || y. */
int vap_h2e_pwe(uint8_t pwe_out[64], const uint8_t pt_in[64],
                const uint8_t *mac_a, const uint8_t *mac_b);

/** RFC 9380 J.1.1 and Annex J.10 against this file's own arithmetic.
 *  0 when every vector comes out. */
int vap_h2e_selftest(void);

#endif /* _VAPH2E_H_ */
