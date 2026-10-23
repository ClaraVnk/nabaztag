/**
 * @file vapcrypto.c
 * @brief The crypto an access point needs and the rabbit does not have.
 *
 * The firmware is a station, so its AES goes one way only: crypto/aes128.c
 * decrypts, because RFC 3394 key *unwrap* is all a station ever needs — the
 * access point is the one that wraps the group key into message 3. Flash is
 * the scarce resource on an ARM7 with 256 KB, so an encrypt side it never
 * calls was correctly left out.
 *
 * The virtual AP does need it, and this is the AP's own code rather than the
 * rabbit's: FIPS-197 AES-128 encryption, RFC 3394 key wrap, and the PTK
 * derivation. The PTK one is deliberately NOT a reimplementation — it calls
 * the firmware's own prf() and reproduces the concatenation order from
 * compute_ptk() in eapol.c, because a handshake that only works when both
 * ends share a mistake proves nothing. vap_crypto_selftest() checks the AES
 * and the key wrap against the vectors in the standards before any run, so a
 * failed handshake is never this file's fault without saying so.
 */
#include <stdio.h>
#include <string.h>

#include "common.h"
#include "net/hash.h"
#include "net/ieee80211.h"
#include "net/eapol.h"

/* eapol.c exports these but declares them in no header. */
extern void prf(const uint8_t *key, uint16_t key_len,
                const uint8_t *prefix, uint16_t prefix_len,
                const uint8_t *data, uint16_t data_len,
                uint8_t *output, uint16_t len);

#include "vapcrypto.h"

/* --- AES-128, encrypt direction (FIPS-197) ----------------------------- */

static const uint8_t sbox[256] = {
  0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
  0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
  0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
  0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
  0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
  0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
  0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
  0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
  0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
  0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
  0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
  0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
  0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
  0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
  0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
  0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
};

static uint8_t xtime(uint8_t x)
{
  return (uint8_t)((x << 1) ^ ((x & 0x80) ? 0x1b : 0x00));
}

/** @brief Expand a 128-bit key into eleven round keys. */
static void aes_expand(const uint8_t key[16], uint8_t rk[176])
{
  static const uint8_t rcon[10] = {0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36};
  int i;

  memcpy(rk, key, 16);
  for(i = 4; i < 44; i++) {
    uint8_t t[4];
    memcpy(t, rk + (i - 1) * 4, 4);
    if(i % 4 == 0) {
      uint8_t tmp = t[0];
      t[0] = (uint8_t)(sbox[t[1]] ^ rcon[i / 4 - 1]);
      t[1] = sbox[t[2]];
      t[2] = sbox[t[3]];
      t[3] = sbox[tmp];
    }
    for(int j = 0; j < 4; j++)
      rk[i * 4 + j] = (uint8_t)(rk[(i - 4) * 4 + j] ^ t[j]);
  }
}

/** @brief Encrypt one 16-byte block. */
static void aes_encrypt_block(const uint8_t rk[176], uint8_t out[16],
                              const uint8_t in[16])
{
  uint8_t s[16];
  int round, c;

  for(c = 0; c < 16; c++) s[c] = (uint8_t)(in[c] ^ rk[c]);

  for(round = 1; round <= 10; round++) {
    uint8_t t[16];

    /* SubBytes, then ShiftRows: column c of the state takes its bytes from
     * columns c, c+1, c+2, c+3 of the previous one. */
    for(c = 0; c < 4; c++) {
      t[c*4 + 0] = sbox[s[(c*4 + 0) % 16]];
      t[c*4 + 1] = sbox[s[(c*4 + 5) % 16]];
      t[c*4 + 2] = sbox[s[(c*4 + 10) % 16]];
      t[c*4 + 3] = sbox[s[(c*4 + 15) % 16]];
    }

    if(round < 10) {
      /* MixColumns */
      for(c = 0; c < 4; c++) {
        uint8_t *p = &t[c * 4];
        uint8_t a0 = p[0], a1 = p[1], a2 = p[2], a3 = p[3];
        p[0] = (uint8_t)(xtime(a0) ^ (xtime(a1) ^ a1) ^ a2 ^ a3);
        p[1] = (uint8_t)(a0 ^ xtime(a1) ^ (xtime(a2) ^ a2) ^ a3);
        p[2] = (uint8_t)(a0 ^ a1 ^ xtime(a2) ^ (xtime(a3) ^ a3));
        p[3] = (uint8_t)((xtime(a0) ^ a0) ^ a1 ^ a2 ^ xtime(a3));
      }
    }

    for(c = 0; c < 16; c++) s[c] = (uint8_t)(t[c] ^ rk[round * 16 + c]);
  }

  memcpy(out, s, 16);
}

/* --- RFC 3394 key wrap ------------------------------------------------- */

/**
 * @brief Wrap key data with a 128-bit KEK, as an AP wraps the GTK.
 *
 * The inverse of crypto/aes128.c's aes_key_unwrap(), which is what the rabbit
 * runs on the receiving side.
 *
 * @param [in]  kek    the EAPOL key encryption key, ptk.s.kek
 * @param [in]  in     key data, a multiple of 8 bytes, at least 16
 * @param [in]  inlen  its length
 * @param [out] out    inlen + 8 bytes
 * @return 1 on success, 0 if inlen is not something RFC 3394 can wrap
 */
int vap_aes_key_wrap(const uint8_t kek[16], const uint8_t *in, uint32_t inlen,
                     uint8_t *out)
{
  uint8_t rk[176];
  uint8_t a[8];
  uint8_t block[16];
  uint32_t n = inlen / 8;
  uint32_t i, j;

  if(inlen < 16 || (inlen % 8) != 0) return 0;

  aes_expand(kek, rk);
  memset(a, 0xa6, 8);                 /* the standard's initial value */
  memmove(out + 8, in, inlen);

  for(j = 0; j < 6; j++) {
    for(i = 1; i <= n; i++) {
      uint64_t t = (uint64_t)n * j + i;
      memcpy(block, a, 8);
      memcpy(block + 8, out + i * 8, 8);
      aes_encrypt_block(rk, block, block);
      memcpy(a, block, 8);
      for(int k = 0; k < 8; k++)      /* A ^= t, big endian */
        a[7 - k] ^= (uint8_t)(t >> (8 * k));
      memcpy(out + i * 8, block + 8, 8);
    }
  }
  memcpy(out, a, 8);
  return 1;
}

/* --- the PTK ----------------------------------------------------------- */

static const uint8_t ptk_prefix[] = {
  'P','a','i','r','w','i','s','e',' ','k','e','y',' ',
  'e','x','p','a','n','s','i','o','n'
};

/**
 * @brief Derive the PTK the way eapol.c's compute_ptk() does.
 *
 * Same concatenation — lower MAC, higher MAC, lower nonce, higher nonce —
 * and the firmware's own prf(), so if the derivation is wrong both ends are
 * wrong in the same way and the MIC still matches. That is the point: this
 * tests the handshake, not the key schedule, and the key schedule already has
 * its own vectors under ../tests.
 */
void vap_compute_ptk(const uint8_t *pmk,
                     const uint8_t *anonce, const uint8_t *aa,
                     const uint8_t *snonce, const uint8_t *sa,
                     uint8_t *ptk, uint32_t length)
{
  uint8_t concatenation[128];
  uint32_t position = 0;
  int sa_is_higher = memcmp(sa, aa, IEEE80211_ADDR_LEN) > 0;
  int snonce_is_higher = memcmp(snonce, anonce, EAPOL_NONCE_LENGTH) > 0;

  memcpy(&concatenation[position], sa_is_higher ? aa : sa, IEEE80211_ADDR_LEN);
  position += IEEE80211_ADDR_LEN;
  memcpy(&concatenation[position], sa_is_higher ? sa : aa, IEEE80211_ADDR_LEN);
  position += IEEE80211_ADDR_LEN;
  memcpy(&concatenation[position], snonce_is_higher ? anonce : snonce,
         EAPOL_NONCE_LENGTH);
  position += EAPOL_NONCE_LENGTH;
  memcpy(&concatenation[position], snonce_is_higher ? snonce : anonce,
         EAPOL_NONCE_LENGTH);
  position += EAPOL_NONCE_LENGTH;

  prf(pmk, EAPOL_MASTER_KEY_LENGTH, ptk_prefix, sizeof(ptk_prefix),
      concatenation, (uint16_t)position, ptk, (uint16_t)length);
}

/* --- proof that the above is not inventing things ---------------------- */

/**
 * @brief Check the AES and the key wrap against the published vectors.
 *
 * @return 0 when both match, non-zero otherwise.
 */
int vap_crypto_selftest(void)
{
  /* FIPS-197 C.1: AES-128 of 00112233..ff under key 000102..0f */
  static const uint8_t fips_key[16] = {
    0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,
    0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f };
  static const uint8_t fips_in[16] = {
    0x00,0x11,0x22,0x33,0x44,0x55,0x66,0x77,
    0x88,0x99,0xaa,0xbb,0xcc,0xdd,0xee,0xff };
  static const uint8_t fips_out[16] = {
    0x69,0xc4,0xe0,0xd8,0x6a,0x7b,0x04,0x30,
    0xd8,0xcd,0xb7,0x80,0x70,0xb4,0xc5,0x5a };
  /* RFC 3394 section 4.1: wrapping a 128-bit key with a 128-bit KEK */
  static const uint8_t wrap_expected[24] = {
    0x1f,0xa6,0x8b,0x0a,0x81,0x12,0xb4,0x47,
    0xae,0xf3,0x4b,0xd8,0xfb,0x5a,0x7b,0x82,
    0x9d,0x3e,0x86,0x23,0x71,0xd2,0xcf,0xe5 };

  uint8_t rk[176], out[24];
  int failures = 0;

  aes_expand(fips_key, rk);
  aes_encrypt_block(rk, out, fips_in);
  if(memcmp(out, fips_out, 16) != 0) {
    fprintf(stderr, "vapcrypto: AES-128 does not match FIPS-197 C.1\n");
    failures++;
  }

  if(!vap_aes_key_wrap(fips_key, fips_in, 16, out)
     || memcmp(out, wrap_expected, 24) != 0) {
    fprintf(stderr, "vapcrypto: key wrap does not match RFC 3394 4.1\n");
    failures++;
  }

  return failures;
}
