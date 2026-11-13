/* One primitive per build, chosen with -DBENCH=<name>. Each case checks its
 * result against a published vector or a throwaway signature where one
 * exists, so a count is only reported for work that was done correctly.
 * Returns 1 = ok. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>

void sys_write(const void *p, unsigned n);
static void hexout(const uint8_t *p, unsigned n)
{
  static const char hx[] = "0123456789abcdef";
  char line[2 * 64 + 1]; unsigned i, k = 0;
  for (i = 0; i < n; i++) {
    line[k++] = hx[p[i] >> 4]; line[k++] = hx[p[i] & 15];
    if (k == 128 || i + 1 == n) { line[k++] = '\n'; sys_write(line, k); k = 0; }
  }
}

#define CAT2(a, b) a##b
#define CAT(a, b) CAT2(a, b)
#define BENCH_nop 1
#define BENCH_x25519 2
#define BENCH_x25519_base 3
#define BENCH_ed_short 4
#define BENCH_ed_long 5
#define BENCH_sha512_long 6
#define BENCH_sha512_short 7
#define BENCH_p256 8
#define BENCH_p256_base 9
#define BENCH_gcm_kat 10
#define BENCH_gcm_1k 11
#define BENCH_hkdf_kat 12
#define BENCH_hkdf 13
#define BENCH_sha256_4k 14
#define BENCH_aes_block_cmac 15
#define BENCH_aes_block 16
#define BENCH_ghash_1k 17
#define BENCH_ctr_1k 18
#define BENCH_sha256_1k 19
#define BENCH_memcpy_105k 20
#define BENCH_mc_x25519 21
#define BENCH_mc_ed_short 22
#define BENCH_mc_ed_long 23
#define BENCH_mc_sha512_long 24
#define BENCH_c25519_x25519 25
#define BENCH_c25519_ed_short 26
#define BENCH_c25519_ed_long 27
#define BENCH_sae_pt 28
#define BENCH_sae_commit_h2e 29
#define BENCH_sae_commit_hnp 30
#define BENCH_cp_1k 31
#define BENCH_cp_16k 32
#define BENCH_ecdsa_p256 33
#define BENCH_ecdsa_p384 34
#define BENCH_rsa2048_pss 35
#define BENCH_rsa4096_pkcs1 36
#define SEL CAT(BENCH_, BENCH)

#if SEL == BENCH_nop
int bench(void) { return 1; }

#elif SEL == BENCH_x25519 || SEL == BENCH_x25519_base
#include "tweetnacl.h"
/* RFC 7748 6.1 */
static const uint8_t alice_sk[32] = {0x77,0x07,0x6d,0x0a,0x73,0x18,0xa5,0x7d,0x3c,0x16,0xc1,0x72,0x51,0xb2,0x66,0x45,0xdf,0x4c,0x2f,0x87,0xeb,0xc0,0x99,0x2a,0xb1,0x77,0xfb,0xa5,0x1d,0xb9,0x2c,0x2a};
static const uint8_t alice_pk[32] = {0x85,0x20,0xf0,0x09,0x89,0x30,0xa7,0x54,0x74,0x8b,0x7d,0xdc,0xb4,0x3e,0xf7,0x5a,0x0d,0xbf,0x3a,0x0d,0x26,0x38,0x1a,0xf4,0xeb,0xa4,0xa9,0x8e,0xaa,0x9b,0x4e,0x6a};
static const uint8_t bob_pk[32]   = {0xde,0x9e,0xdb,0x7d,0x7b,0x7d,0xc1,0xb4,0xd3,0x5b,0x61,0xc2,0xec,0xe4,0x35,0x37,0x3f,0x83,0x43,0xc8,0x5b,0x78,0x67,0x4d,0xad,0xfc,0x7e,0x14,0x6f,0x88,0x2b,0x4f};
static const uint8_t shared[32]   = {0x4a,0x5d,0x9d,0x5b,0xa4,0xce,0x2d,0xe1,0x72,0x8e,0x3b,0xf4,0x80,0x35,0x0f,0x25,0xe0,0x7e,0x21,0xc9,0x47,0xd1,0x9e,0x33,0x76,0xf0,0x9b,0x3c,0x1e,0x16,0x17,0x42};
int bench(void)
{
  uint8_t q[32];
#if SEL == BENCH_x25519
  crypto_scalarmult(q, alice_sk, bob_pk);
  return memcmp(q, shared, 32) == 0;
#else
  crypto_scalarmult_base(q, alice_sk);
  return memcmp(q, alice_pk, 32) == 0;
#endif
}

#elif SEL == BENCH_ed_short || SEL == BENCH_ed_long
#include "nab_sig.h"
#if SEL == BENCH_ed_short
extern const unsigned char _binary_msg64_bin_start[], _binary_msg64_bin_end[];
extern const unsigned char _binary_sig64_bin_start[];
#define MSG _binary_msg64_bin_start
#define MSGEND _binary_msg64_bin_end
#define SIG _binary_sig64_bin_start
#else
extern const unsigned char _binary_bootcode_bin_start[], _binary_bootcode_bin_end[];
extern const unsigned char _binary_sigboot_bin_start[];
#define MSG _binary_bootcode_bin_start
#define MSGEND _binary_bootcode_bin_end
#define SIG _binary_sigboot_bin_start
#endif
static unsigned char scratch[110000 + 64];
int bench(void)
{
  size_t len = (size_t)(MSGEND - MSG);
  return nab_verify_sig(MSG, len, SIG, scratch);
}

#elif SEL == BENCH_sha512_long || SEL == BENCH_sha512_short
#include "tweetnacl.h"
extern const unsigned char _binary_bootcode_bin_start[], _binary_bootcode_bin_end[];
int bench(void)
{
  uint8_t h[64];
  size_t len = (size_t)(_binary_bootcode_bin_end - _binary_bootcode_bin_start);
#if SEL == BENCH_sha512_short
  len = 64;
#endif
  crypto_hash(h, _binary_bootcode_bin_start, len);
  hexout(h, 64);
  return 1;
}

#elif SEL == BENCH_memcpy_105k
extern const unsigned char _binary_bootcode_bin_start[], _binary_bootcode_bin_end[];
static unsigned char scratch[110000 + 64];
int bench(void)
{
  size_t len = (size_t)(_binary_bootcode_bin_end - _binary_bootcode_bin_start);
  memcpy(scratch + 64, _binary_bootcode_bin_start, len);
  return scratch[64] == _binary_bootcode_bin_start[0];
}

#elif SEL == BENCH_p256 || SEL == BENCH_p256_base
#include "net/p256.h"
/* 2G on P-256 */
static const uint8_t twoG_x[32] = {0x7c,0xf2,0x7b,0x18,0x8d,0x03,0x4f,0x7e,0x8a,0x52,0x38,0x03,0x04,0xb5,0x1a,0xc3,0xc0,0x89,0x69,0xe2,0x77,0xf2,0x1b,0x35,0xa6,0x0b,0x48,0xfc,0x47,0x66,0x99,0x78};
static const uint8_t twoG_y[32] = {0x07,0x77,0x55,0x10,0xdb,0x8e,0xd0,0x40,0x29,0x3d,0x9a,0xc6,0x9f,0x74,0x30,0xdb,0xba,0x7d,0xad,0xe6,0x3c,0xe9,0x82,0x29,0x9e,0x04,0xb7,0x9d,0x22,0x78,0x73,0xd1};
/* k*G for a full 256-bit scalar (top bit set), k = SHA-256("nabaztag") | 2^255
 * mod n; expected point computed independently in Python (affine, mod p). */
static const uint8_t kr[32] = {0xa6,0x0d,0xc0,0xe6,0x7d,0xe1,0x91,0xbd,0x1e,0xbc,0x49,0x04,0x4b,0xcb,0xb7,0x5c,0xb6,0x1a,0x8c,0x18,0x8c,0x7e,0xb5,0x9f,0xc8,0xb4,0x2b,0x75,0x96,0x9b,0xe2,0xe6};
static const uint8_t kr_x[32] = {0xe3,0xd9,0x63,0x77,0x4e,0x0f,0x75,0xce,0x51,0x0b,0x87,0x26,0x09,0x7c,0x68,0x90,0x9c,0x49,0xd7,0x7e,0x28,0xc5,0x80,0xaf,0xa3,0xda,0xb7,0x5a,0xe2,0x70,0xe4,0xe8};
static const uint8_t kr_y[32] = {0x8e,0x94,0xb4,0x34,0x43,0xfc,0xb0,0x72,0x10,0xb5,0x6e,0x75,0xf9,0x6d,0x6a,0x09,0x8c,0x33,0xf2,0x7d,0x97,0xcc,0x38,0x71,0x6a,0xc3,0xfb,0x89,0xb5,0x66,0x18,0x83};
int bench(void)
{
  uint8_t k[32]; p256_point out, g;
#if SEL == BENCH_p256
  /* the ECDH shape: a full scalar times a peer point (here G) */
  memcpy(k, kr, 32);
  p256_get_base(&g);
  if (!p256_mul(&out, k, &g, NULL)) return 0;
  return memcmp(out.x, kr_x, 32) == 0 && memcmp(out.y, kr_y, 32) == 0;
#else
  /* k = 2: shows whether a small scalar is cheaper (it must not be, the
   * ladder is constant-time) */
  memset(k, 0, 32); k[31] = 2;
  if (!p256_mul_base(&out, k, NULL)) return 0;
  return memcmp(out.x, twoG_x, 32) == 0 && memcmp(out.y, twoG_y, 32) == 0;
#endif
}

#elif SEL == BENCH_sae_pt || SEL == BENCH_sae_commit_h2e || SEL == BENCH_sae_commit_hnp
/* SAE as the rabbit runs it, on the IEEE 802.11-2020 Annex J.10 vectors
 * (vectors_h2e.h, generated under ../../tests). Three shapes:
 *   sae_pt          the hash-to-element PT, derived once per (SSID, password)
 *   sae_commit_h2e  what every association then costs: PWE from PT, then
 *                   the commit (two scalar multiplications)
 *   sae_commit_hnp  the hunting-and-pecking commit, for comparison: 40
 *                   rounds of KDF and square root, then one multiplication
 * The tick is nab_verify_tick, so count.py can report the longest stretch
 * between two feeds of the watchdog, which is the number that decides
 * whether the rabbit survives the computation. */
#include "net/sae.h"
#include "vectors_h2e.h"
extern void nab_verify_tick(void);
struct seq_rng { const uint8_t *vals[2]; int next; };
static void seq_bytes(uint8_t *out, uint32_t len, void *arg)
{
  struct seq_rng *s = (struct seq_rng *)arg;
  memcpy(out, s->vals[s->next & 1], len);
  s->next++;
}
int bench(void)
{
  struct seq_rng rng = { { AJ_RAND, AJ_MASK }, 0 };
  p256_point pt;
  sae_ctx c;
#if SEL == BENCH_sae_pt
  if (!sae_derive_pt(&pt, (const uint8_t *)AJ_SSID, strlen(AJ_SSID), AJ_PW, AJ_PWID,
                     nab_verify_tick)) return 0;
  return memcmp(pt.x, AJ_H2E_PT_X, 32) == 0 && memcmp(pt.y, AJ_H2E_PT_Y, 32) == 0;
#elif SEL == BENCH_sae_commit_h2e
  memcpy(pt.x, AJ_H2E_PT_X, 32);
  memcpy(pt.y, AJ_H2E_PT_Y, 32);
  if (!sae_commit_pt(&c, AJ_ADDR1B, AJ_ADDR2B, &pt, seq_bytes, &rng, nab_verify_tick)) return 0;
  return memcmp(c.pwe.x, AJ_H2E_PWE_X, 32) == 0 && memcmp(c.pwe.y, AJ_H2E_PWE_Y, 32) == 0;
#else
  {
    uint8_t commit[SAE_COMMIT_LEN];
    if (!sae_commit(&c, AJ_ADDR1, AJ_ADDR2, AJ_PW, seq_bytes, &rng, nab_verify_tick)) return 0;
    sae_commit_bytes(&c, commit);
    return memcmp(commit, AJ_COMMIT, sizeof commit) == 0;
  }
#endif
}

#elif SEL == BENCH_gcm_kat
#include "gcm.h"
/* GCM spec test case 4 */
static const uint8_t K[16] = {0xfe,0xff,0xe9,0x92,0x86,0x65,0x73,0x1c,0x6d,0x6a,0x8f,0x94,0x67,0x30,0x83,0x08};
static const uint8_t P[60] = {0xd9,0x31,0x32,0x25,0xf8,0x84,0x06,0xe5,0xa5,0x59,0x09,0xc5,0xaf,0xf5,0x26,0x9a,0x86,0xa7,0xa9,0x53,0x15,0x34,0xf7,0xda,0x2e,0x4c,0x30,0x3d,0x8a,0x31,0x8a,0x72,0x1c,0x3c,0x0c,0x95,0x95,0x68,0x09,0x53,0x2f,0xcf,0x0e,0x24,0x49,0xa6,0xb5,0x25,0xb1,0x6a,0xed,0xf5,0xaa,0x0d,0xe6,0x57,0xba,0x63,0x7b,0x39};
static const uint8_t A[20] = {0xfe,0xed,0xfa,0xce,0xde,0xad,0xbe,0xef,0xfe,0xed,0xfa,0xce,0xde,0xad,0xbe,0xef,0xab,0xad,0xda,0xd2};
static const uint8_t IV[12] = {0xca,0xfe,0xba,0xbe,0xfa,0xce,0xdb,0xad,0xde,0xca,0xf8,0x88};
static const uint8_t C[60] = {0x42,0x83,0x1e,0xc2,0x21,0x77,0x74,0x24,0x4b,0x72,0x21,0xb7,0x84,0xd0,0xd4,0x9c,0xe3,0xaa,0x21,0x2f,0x2c,0x02,0xa4,0xe0,0x35,0xc1,0x7e,0x23,0x29,0xac,0xa1,0x2e,0x21,0xd5,0x14,0xb2,0x54,0x66,0x93,0x1c,0x7d,0x8f,0x6a,0x5a,0xac,0x84,0xaa,0x05,0x1b,0xa3,0x0b,0x39,0x6a,0x0a,0xac,0x97,0x3d,0x58,0xe0,0x91};
static const uint8_t T[16] = {0x5b,0xc9,0x4f,0xbc,0x32,0x21,0xa5,0xdb,0x94,0xfa,0xe9,0x5a,0xe7,0x12,0x1a,0x47};
int bench(void)
{
  struct aes128_enc aes; uint8_t buf[60], tag[16];
  aes128_enc_init(&aes, K);
  memcpy(buf, P, 60);
  gcm_encrypt(&aes, IV, A, 20, buf, 60, tag);
  hexout(tag, 16);
  return memcmp(buf, C, 60) == 0 && memcmp(tag, T, 16) == 0;
}

#elif SEL == BENCH_gcm_1k || SEL == BENCH_ctr_1k || SEL == BENCH_ghash_1k
#include "gcm.h"
static uint8_t rec[1024];
static const uint8_t K[16] = {0xfe,0xff,0xe9,0x92,0x86,0x65,0x73,0x1c,0x6d,0x6a,0x8f,0x94,0x67,0x30,0x83,0x08};
static const uint8_t IV[12] = {0xca,0xfe,0xba,0xbe,0xfa,0xce,0xdb,0xad,0xde,0xca,0xf8,0x88};
static const uint8_t hdr[5] = {0x17, 0x03, 0x03, 0x04, 0x10};   /* TLS 1.3 record header = AAD */
int bench(void)
{
  struct aes128_enc aes; uint8_t tag[16]; unsigned i;
  for (i = 0; i < sizeof rec; i++) rec[i] = (uint8_t)i;
  aes128_enc_init(&aes, K);
#if SEL == BENCH_gcm_1k
  gcm_encrypt(&aes, IV, hdr, 5, rec, sizeof rec, tag);
  hexout(tag, 16);
#elif SEL == BENCH_ctr_1k
  /* the CTR half alone: 64 block encryptions + xor */
  { uint8_t ctr[16], ks[16]; uint32_t c = 2; unsigned n;
    memcpy(ctr, IV, 12);
    for (n = 0; n < sizeof rec; n += 16, c++) {
      ctr[12] = (uint8_t)(c >> 24); ctr[13] = (uint8_t)(c >> 16); ctr[14] = (uint8_t)(c >> 8); ctr[15] = (uint8_t)c;
      aes128_enc_block(&aes, ks, ctr);
      for (i = 0; i < 16; i++) rec[n + i] ^= ks[i];
    } }
#else
  /* GHASH alone over 1 KB + lengths, through the public entry with a
   * zero-length cipher run is not possible; measure by difference:
   * gcm_1k - ctr_1k - (2 block encryptions). This case = gcm with 0 bytes
   * of data and 1024 bytes of AAD, which is GHASH of 64 blocks + 1. */
  gcm_encrypt(&aes, IV, rec, sizeof rec, rec, 0, tag);
  hexout(tag, 16);
#endif
  return 1;
}

#elif SEL == BENCH_cp_1k || SEL == BENCH_cp_16k
/* ChaCha20-Poly1305 (TLS_CHACHA20_POLY1305_SHA256), a record of 1 KB or
 * 16 KB with its 5-byte header as AAD, against gcm_1k above. */
#include "net/chachapoly.h"
#if SEL == BENCH_cp_1k
static uint8_t rec[1024];
#else
static uint8_t rec[16384];
#endif
static const uint8_t K[32] = {1, 2, 3};
static const uint8_t IV[12] = {4, 5, 6};
static const uint8_t hdr[5] = {0x17, 0x03, 0x03, 0x04, 0x10};
int bench(void)
{
  uint8_t tag[16]; unsigned i;
  for (i = 0; i < sizeof rec; i++) rec[i] = (uint8_t)i;
  chachapoly(K, IV, hdr, 5, rec, sizeof rec, tag, 1);
  hexout(tag, 16);
  return chachapoly(K, IV, hdr, 5, rec, sizeof rec, tag, 0) && rec[100] == 100;
}

#elif SEL == BENCH_ecdsa_p256 || SEL == BENCH_ecdsa_p384 || SEL == BENCH_rsa2048_pss || SEL == BENCH_rsa4096_pkcs1
/* One signature check each, from tests/vectors_pki.h: what a TLS handshake
 * with a certificate costs (a Let's Encrypt ECDSA chain: one P-384 check
 * for the leaf, one P-256 for CertificateVerify; an RSA chain: RSA-2048s). */
#include "net/nab_pki.h"
#include "vectors_pki.h"
extern void nab_verify_tick(void);   /* rt.c: counted as the watchdog */
int bench(void)
{
#if SEL == BENCH_ecdsa_p256
  return nab_ecdsa_verify(NAB_P256, EC_CASES[0].q, EC_CASES[0].ql, EC_CASES[0].s, EC_CASES[0].sl, EC_CASES[0].h, EC_CASES[0].hl, nab_verify_tick);
#elif SEL == BENCH_ecdsa_p384
  return nab_ecdsa_verify(NAB_P384, EC_CASES[9].q, EC_CASES[9].ql, EC_CASES[9].s, EC_CASES[9].sl, EC_CASES[9].h, EC_CASES[9].hl, nab_verify_tick);
#elif SEL == BENCH_rsa2048_pss
  return nab_rsa_verify_pss(RSA_CASES[1].n, RSA_CASES[1].nl, RSA_CASES[1].s, RSA_CASES[1].sl, RSA_CASES[1].h, RSA_CASES[1].hl, nab_verify_tick);
#else
  return nab_rsa_verify_pkcs1(RSA_CASES[8].n, RSA_CASES[8].nl, RSA_CASES[8].s, RSA_CASES[8].sl, RSA_CASES[8].h, RSA_CASES[8].hl, nab_verify_tick);
#endif
}

#elif SEL == BENCH_hkdf_kat
#include "hkdf.h"
/* RFC 5869 test case 1, first 32 bytes of OKM */
static const uint8_t ikm[22] = {0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b,0x0b};
static const uint8_t salt[13] = {0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,0x0b,0x0c};
static const uint8_t info[10] = {0xf0,0xf1,0xf2,0xf3,0xf4,0xf5,0xf6,0xf7,0xf8,0xf9};
static const uint8_t prk_ok[32] = {0x07,0x77,0x09,0x36,0x2c,0x2e,0x32,0xdf,0x0d,0xdc,0x3f,0x0d,0xc4,0x7b,0xba,0x63,0x90,0xb6,0xc7,0x3b,0xb5,0x0f,0x9c,0x31,0x22,0xec,0x84,0x4a,0xd7,0xc2,0xb3,0xe5};
static const uint8_t okm_ok[32] = {0x3c,0xb2,0x5f,0x25,0xfa,0xac,0xd5,0x7a,0x90,0x43,0x4f,0x64,0xd0,0x36,0x2f,0x2a,0x2d,0x2d,0x0a,0x90,0xcf,0x1a,0x5a,0x4c,0x5d,0xb0,0x2d,0x56,0xec,0xc4,0xc5,0xbf};
int bench(void)
{
  uint8_t prk[32], okm[32];
  hkdf_extract(prk, salt, 13, ikm, 22);
  hkdf_expand1(okm, 32, prk, info, 10);
  hexout(prk, 32); hexout(okm, 32);
  return memcmp(prk, prk_ok, 32) == 0 && memcmp(okm, okm_ok, 32) == 0;
}

#elif SEL == BENCH_hkdf
#include "hkdf.h"
/* one Extract (32-byte salt, 32-byte IKM) and one Expand-Label with a
 * 32-byte transcript hash: the shape of every step of the key schedule */
static const uint8_t salt[32] = {1,2,3}, ikm[32] = {4,5,6}, th[32] = {7,8,9};
int bench(void)
{
  uint8_t prk[32], out[32];
  hkdf_extract(prk, salt, 32, ikm, 32);
  tls13_expand_label(out, 32, prk, "s hs traffic", th, 32);
  hexout(out, 32);
  return 1;
}

#elif SEL == BENCH_sha256_4k || SEL == BENCH_sha256_1k
#include "net/sha256.h"
static uint8_t buf[4096];
int bench(void)
{
  uint8_t h[32]; unsigned i;
  for (i = 0; i < sizeof buf; i++) buf[i] = (uint8_t)i;
  sha256(h, buf, SEL == BENCH_sha256_4k ? 4096 : 1024);
  hexout(h, 32);
  return 1;
}

#elif SEL == BENCH_aes_block_cmac
#include "net/cmac.h"
/* FIPS-197 C.1 */
static const uint8_t K[16] = {0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f};
static const uint8_t P[16] = {0x00,0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,0x99,0xaa,0xbb,0xcc,0xdd,0xee,0xff};
static const uint8_t C[16] = {0x69,0xc4,0xe0,0xd8,0x6a,0x7b,0x04,0x30,0xd8,0xcd,0xb7,0x80,0x70,0xb4,0xc5,0x5a};
int bench(void) { uint8_t o[16]; aes128_encrypt_block(K, o, P); return memcmp(o, C, 16) == 0; }

#elif SEL == BENCH_aes_block
#include "aes128_enc.h"
static const uint8_t K[16] = {0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f};
static const uint8_t P[16] = {0x00,0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,0x99,0xaa,0xbb,0xcc,0xdd,0xee,0xff};
static const uint8_t C[16] = {0x69,0xc4,0xe0,0xd8,0x6a,0x7b,0x04,0x30,0xd8,0xcd,0xb7,0x80,0x70,0xb4,0xc5,0x5a};
int bench(void) { struct aes128_enc a; uint8_t o[16]; aes128_enc_init(&a, K); aes128_enc_block(&a, o, P); return memcmp(o, C, 16) == 0; }

#elif SEL == BENCH_mc_x25519
/* Monocypher, 32-bit limbs: the comparison point for TweetNaCl's i64 limbs */
#include "monocypher.h"
static const uint8_t alice_sk[32] = {0x77,0x07,0x6d,0x0a,0x73,0x18,0xa5,0x7d,0x3c,0x16,0xc1,0x72,0x51,0xb2,0x66,0x45,0xdf,0x4c,0x2f,0x87,0xeb,0xc0,0x99,0x2a,0xb1,0x77,0xfb,0xa5,0x1d,0xb9,0x2c,0x2a};
static const uint8_t bob_pk[32]   = {0xde,0x9e,0xdb,0x7d,0x7b,0x7d,0xc1,0xb4,0xd3,0x5b,0x61,0xc2,0xec,0xe4,0x35,0x37,0x3f,0x83,0x43,0xc8,0x5b,0x78,0x67,0x4d,0xad,0xfc,0x7e,0x14,0x6f,0x88,0x2b,0x4f};
static const uint8_t shared[32]   = {0x4a,0x5d,0x9d,0x5b,0xa4,0xce,0x2d,0xe1,0x72,0x8e,0x3b,0xf4,0x80,0x35,0x0f,0x25,0xe0,0x7e,0x21,0xc9,0x47,0xd1,0x9e,0x33,0x76,0xf0,0x9b,0x3c,0x1e,0x16,0x17,0x42};
int bench(void)
{
  uint8_t q[32];
  crypto_x25519(q, alice_sk, bob_pk);
  return memcmp(q, shared, 32) == 0;
}

#elif SEL == BENCH_mc_ed_short || SEL == BENCH_mc_ed_long || SEL == BENCH_mc_sha512_long
/* Monocypher's crypto_eddsa_* is EdDSA over BLAKE2b; standard Ed25519
 * (SHA-512) is the optional monocypher-ed25519.c. */
#include "monocypher.h"
#include "monocypher-ed25519.h"
#include "signing_pubkey.h"
#if SEL == BENCH_mc_ed_short
extern const unsigned char _binary_msg64_bin_start[], _binary_msg64_bin_end[];
extern const unsigned char _binary_sig64_bin_start[];
#define MSG _binary_msg64_bin_start
#define MSGEND _binary_msg64_bin_end
#define SIG _binary_sig64_bin_start
#else
extern const unsigned char _binary_bootcode_bin_start[], _binary_bootcode_bin_end[];
extern const unsigned char _binary_sigboot_bin_start[];
#define MSG _binary_bootcode_bin_start
#define MSGEND _binary_bootcode_bin_end
#define SIG _binary_sigboot_bin_start
#endif
int bench(void)
{
  size_t len = (size_t)(MSGEND - MSG);
#if SEL == BENCH_mc_sha512_long
  uint8_t h[64];
  crypto_sha512(h, MSG, len);
  hexout(h, 64);
  return 1;
#else
  return crypto_ed25519_check(SIG, nab_signing_pubkey, MSG, len) == 0;
#endif
}

#elif SEL == BENCH_c25519_x25519 || SEL == BENCH_c25519_ed_short || SEL == BENCH_c25519_ed_long
/* c25519 (Daniel Beer), written for small MCUs: no tables, 8-bit limbs */
#include "c25519.h"
#include "edsign.h"
#include "signing_pubkey.h"
#if SEL == BENCH_c25519_x25519
static const uint8_t alice_sk[32] = {0x77,0x07,0x6d,0x0a,0x73,0x18,0xa5,0x7d,0x3c,0x16,0xc1,0x72,0x51,0xb2,0x66,0x45,0xdf,0x4c,0x2f,0x87,0xeb,0xc0,0x99,0x2a,0xb1,0x77,0xfb,0xa5,0x1d,0xb9,0x2c,0x2a};
static const uint8_t bob_pk[32]   = {0xde,0x9e,0xdb,0x7d,0x7b,0x7d,0xc1,0xb4,0xd3,0x5b,0x61,0xc2,0xec,0xe4,0x35,0x37,0x3f,0x83,0x43,0xc8,0x5b,0x78,0x67,0x4d,0xad,0xfc,0x7e,0x14,0x6f,0x88,0x2b,0x4f};
static const uint8_t shared[32]   = {0x4a,0x5d,0x9d,0x5b,0xa4,0xce,0x2d,0xe1,0x72,0x8e,0x3b,0xf4,0x80,0x35,0x0f,0x25,0xe0,0x7e,0x21,0xc9,0x47,0xd1,0x9e,0x33,0x76,0xf0,0x9b,0x3c,0x1e,0x16,0x17,0x42};
int bench(void)
{
  uint8_t q[32], e[32];
  memcpy(e, alice_sk, 32); c25519_prepare(e);
  c25519_smult(q, bob_pk, e);
  return memcmp(q, shared, 32) == 0;
}
#else
#if SEL == BENCH_c25519_ed_short
extern const unsigned char _binary_msg64_bin_start[], _binary_msg64_bin_end[];
extern const unsigned char _binary_sig64_bin_start[];
#define MSG _binary_msg64_bin_start
#define MSGEND _binary_msg64_bin_end
#define SIG _binary_sig64_bin_start
#else
extern const unsigned char _binary_bootcode_bin_start[], _binary_bootcode_bin_end[];
extern const unsigned char _binary_sigboot_bin_start[];
#define MSG _binary_bootcode_bin_start
#define MSGEND _binary_bootcode_bin_end
#define SIG _binary_sigboot_bin_start
#endif
int bench(void)
{
  size_t len = (size_t)(MSGEND - MSG);
  return edsign_verify(SIG, nab_signing_pubkey, MSG, len) != 0;
}
#endif

#else
#error "unknown BENCH"
#endif
