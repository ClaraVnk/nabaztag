/**
 * @file cmac.h
 * @brief AES-128-CMAC (RFC 4493) and 802.11w's BIP-CMAC-128.
 *
 * WPA3 mandates protected management frames, and the RT2573 does CCMP for
 * data in hardware but nothing for management frames. So the deauthenticate
 * that kicks a rabbit off its network has to be authenticated in software,
 * or the rabbit believes any attacker who sends one -- which is the whole
 * reason 802.11w exists.
 *
 * ~600 bytes of ARM7TDMI flash on top of the AES forward cipher.
 */
#ifndef NAB_CMAC_H
#define NAB_CMAC_H

#include <stdint.h>

#define CMAC_TAG 16

/**
 * @brief AES-128 encryption of one block, with its own key schedule.
 *
 * Deliberately not added to `crypto/aes128.c`, which has the decrypt side
 * and the forward S-box already and would have been the obvious home.
 * Touching that file changes the firmware image: `naboot-lean-wpa2`
 * (`31060c76`) is hardware-validated and `releases/README.md` promises a
 * rebuild is byte-identical, and that promise failed the moment the file
 * was edited -- the size stayed at 107 288 bytes and the hash moved, which
 * is exactly the kind of change that is easy to ship without noticing.
 *
 * So this duplicates a key schedule and an S-box, about 900 bytes, and buys
 * a released image that still reproduces from `main`. The day SAE is
 * actually linked there will be a new image to validate anyway, and the two
 * can be merged then -- with a note in the release, not quietly.
 */
void aes128_encrypt_block(const uint8_t key[16],
                          uint8_t out[16], const uint8_t in[16]);

/** AES-128-CMAC over one buffer. */
void aes_cmac(uint8_t tag[CMAC_TAG], const uint8_t key[16],
              const uint8_t *data, uint32_t len);

/**
 * @brief AES-128-CMAC over several pieces, without joining them.
 *
 * BIP computes the MIC over the frame header, a modified copy of a few of
 * its fields, and the body -- which do not sit next to each other in
 * memory, and a 16 KB RAM has no spare frame buffer to build them in.
 */
void aes_cmac_v(uint8_t tag[CMAC_TAG], const uint8_t key[16],
                const uint8_t *const *pieces, const uint32_t *lens, int n);

/**
 * @brief 802.11w BIP-CMAC-128: the MMIE's 8-byte MIC over a management frame.
 *
 * @param igtk   the 16-byte integrity group key
 * @param hdr    the 24-byte 802.11 MAC header, as received
 * @param body   everything after the header, MMIE included, with the MIC
 *               field zeroed
 * @param blen   length of body
 * @param mic    the 8 bytes that belong in the MMIE
 *
 * The AAD is the header with the duration zeroed and three bits of the frame
 * control masked, per 802.11-2020 12.5.4.3. Getting that masking wrong gives
 * a MIC that verifies against nothing and is impossible to debug from the
 * rabbit, so it is done here rather than left to the caller.
 */
void bip_cmac_128(uint8_t mic[8], const uint8_t igtk[16],
                  const uint8_t hdr[24],
                  const uint8_t *body, uint32_t blen);

/**
 * @brief Find the IGTK KDE in unwrapped EAPOL key data.
 *
 * The AP sends it in message 3 alongside the GTK when management frame
 * protection is in use: KDE type 9, whose data is KeyID (2, little-endian),
 * IPN (6) and then the 16-byte key.
 *
 * @return 1 if one was found and copied out, 0 otherwise.
 */
int32_t rsn_find_igtk(const uint8_t *data, uint32_t len,
                      uint8_t *key_id, uint8_t ipn[6], uint8_t igtk[16]);

#endif /* NAB_CMAC_H */
