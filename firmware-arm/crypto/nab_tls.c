/**
 * @file nab_tls.c
 * @brief TLS 1.3 PSK client, record layer and key schedule. See nab_tls.h.
 *
 * Section numbers are RFC 8446 unless said otherwise. AES-128-GCM is NIST
 * SP 800-38D with the bit-serial GHASH (Algorithm 1, no tables: measured
 * 849 k instructions per 1 KB record, tools/cost/results-2026-11-08.txt),
 * on cmac.c's AES block, which the WPA3 modes already link.
 */
#include <string.h>

#include "net/nab_tls.h"
#include "net/sha256.h"
#include "net/cmac.h"
#ifdef NAB_TLS_CERTS
#include "net/chachapoly.h"
#include "net/nab_pki.h"
int crypto_scalarmult_curve25519_tweet(unsigned char *q, const unsigned char *n, const unsigned char *p);
#endif

/* The watchdog: CLR_WDT on the hardware, see patch_verify_wdt. A 4 KB
   record is ~3.4 M instructions of GCM; the interpreter feeds the watchdog
   once per opcode, so the record loop feeds it too. */
extern void nab_verify_tick(void);

/* External RAM on the rabbit and in the emulator (ELF); a plain static on a
   host whose object format has no such section (the macOS test build). */
#ifdef __ELF__
#define SECTION_EXTBSS __attribute__((section(".extbss")))
#else
#define SECTION_EXTBSS
#endif

/* --- state ---------------------------------------------------------------- */

struct tls {
  uint8_t  state;
  uint8_t  srv_keys;        /* 0 before ServerHello, 1 handshake, 2 application */
  uint8_t  cli_keys;        /* 1 handshake, 2 application */
  uint8_t  app_ready;       /* an application record is decrypted at rx[0] */
  uint32_t app_len;         /* its plaintext length (at rx + 5) */

  uint32_t rxlen;
  uint32_t hslen;           /* bytes in hs[] */
  uint32_t skip;            /* bytes of a NewSessionTicket still to drop */
  uint32_t pend;            /* bytes in pending[] */

  sha256_ctx th;            /* the transcript, 4.4.1 */
  uint8_t early[32];        /* Early Secret until the handshake secret exists */
  uint8_t c_hs[32], s_hs[32];
  uint8_t master[32];
  uint8_t ckey[32], civ[12], skey[32], siv[12];   /* 16 of 32 used with AES */
  uint32_t cseq_hi, cseq_lo, sseq_hi, sseq_lo;   /* 5.3, 64-bit */

  uint8_t pending[64];      /* client Finished record (58) or an alert (24) */
  /* Where records and handshake messages are gathered: the session with
     the warren keeps its 4 KB records and 512 bytes of handshake; a
     session with a certificate needs 16 KB records and the whole chain. */
  uint8_t *rx, *hs;
  uint32_t rxcap, hscap;
  uint8_t suite;            /* 0 TLS_AES_128_GCM_SHA256, 1 TLS_CHACHA20_POLY1305_SHA256 */
#ifdef NAB_TLS_CERTS
  uint8_t certs;            /* 0: PSK with the warren; 1: a certificate is expected */
  uint8_t proved;           /* 1 chain trusted, 2 CertificateVerify checked */
  uint8_t eph[32];          /* our X25519 secret, until the ServerHello */
  char host[64];
  char now[14];
  uint8_t key_alg[10], key_param[10];
  uint8_t key[600];         /* the leaf's key, for CertificateVerify */
  uint32_t alg_len, param_len, key_len;
#endif
};

static uint8_t RX0[NAB_TLS_RX] SECTION_EXTBSS;
static uint8_t HS0[512] SECTION_EXTBSS;
static struct tls T0 SECTION_EXTBSS;
#ifdef NAB_TLS_CERTS
/* 26 KB: more than .extbss has left, so the firmware's tls_certs step gives
   them a region of their own, taken from the bottom of the VM heap. */
#if defined(__ELF__) && defined(NAB_TLS_EXTBSS2)
#define SECTION_EXTBSS2 __attribute__((section(".extbss2")))
#else
#define SECTION_EXTBSS2 SECTION_EXTBSS
#endif
static uint8_t RX1[NAB_TLS_RX_CERT] SECTION_EXTBSS2;
static uint8_t HS1[NAB_TLS_HS_CERT] SECTION_EXTBSS2;
static struct tls T1 SECTION_EXTBSS;
#endif
/* The session the calls below act on: 0 with the warren, 1 with a host
   that has a certificate (nab_tls_select). */
static struct tls *S = &T0;
#define T (*S)

/* --- small helpers -------------------------------------------------------- */

static uint32_t be32(const uint8_t *p)
{ return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static void put32(uint8_t *p, uint32_t v)
{ p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v; }
static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static uint32_t get16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | p[1]; }

static int ct_eq(const uint8_t *a, const uint8_t *b, uint32_t n)
{
  uint8_t d = 0;
  while (n--) d |= *a++ ^ *b++;
  return d == 0;
}

/* --- HKDF (RFC 5869) and the TLS labels (7.1) ------------------------------ */

static void extract(uint8_t prk[32], const uint8_t *salt, uint32_t saltlen,
                    const uint8_t *ikm, uint32_t ikmlen)
{
  static const uint8_t zero[32] = {0};
  if (!salt) { salt = zero; saltlen = 32; }
  hmac_sha256(prk, salt, saltlen, ikm, ikmlen);
}

/* HKDF-Expand-Label for outputs of at most one hash: one HMAC, counter 1.
   HkdfLabel = uint16 length, "tls13 " + label (length-prefixed), context
   (length-prefixed). */
static void expand_label(uint8_t *out, uint32_t l, const uint8_t secret[32],
                         const char *label, const uint8_t *ctx, uint32_t ctxlen)
{
  uint8_t info[2 + 1 + 6 + 16 + 1 + 32];
  static const uint8_t one = 1;
  const uint8_t *pieces[2] = { info, &one };
  uint32_t lens[2], n = 0, ll = (uint32_t)strlen(label);
  uint8_t t[32];
  put16(info, l); n = 2;
  info[n++] = (uint8_t)(6 + ll);
  memcpy(info + n, "tls13 ", 6); n += 6;
  memcpy(info + n, label, ll); n += ll;
  info[n++] = (uint8_t)ctxlen;
  if (ctxlen) { memcpy(info + n, ctx, ctxlen); n += ctxlen; }
  lens[0] = n; lens[1] = 1;
  hmac_sha256_v(t, secret, 32, pieces, lens, 2);
  memcpy(out, t, l);
}

/* Transcript-Hash of the messages so far, the running context untouched. */
static void th_now(uint8_t out[32])
{
  sha256_ctx c = T.th;
  sha256_final(&c, out);
}

/* Derive-Secret(secret, label, messages so far). */
static void derive_secret(uint8_t out[32], const uint8_t secret[32], const char *label)
{
  uint8_t h[32];
  th_now(h);
  expand_label(out, 32, secret, label, h, 32);
}

static void traffic_keys(uint8_t key[32], uint8_t iv[12], const uint8_t secret[32])
{
  expand_label(key, T.suite ? 32 : 16, secret, "key", NULL, 0);
  expand_label(iv, 12, secret, "iv", NULL, 0);
}

void nab_tls_psk(uint8_t psk[NAB_TLS_PSK], const uint8_t key[32])
{
  /* RFC 5869 with a salt and an info of their own: the bytecode's HMAC
     uses the adoption key as is, TLS uses this child of it, and a flaw in
     one use does not hand over the other. The warren's kx.py does the same
     two lines. */
  static const uint8_t info[] = "nabtls psk";
  static const uint8_t one = 1;
  const uint8_t *pieces[2] = { info, &one };
  uint32_t lens[2] = { sizeof info - 1, 1 };
  uint8_t prk[32];
  hmac_sha256(prk, (const uint8_t *)"nabaztag tls v1", 15, key, 32);
  hmac_sha256_v(psk, prk, 32, pieces, lens, 2);
  memset(prk, 0, sizeof prk);
}

/* --- AES-128-GCM ------------------------------------------------------------ */

/* y = (y ^ x) * H in GF(2^128), GCM's bit order. */
static void ghash_block(uint32_t y[4], const uint32_t h[4], const uint8_t x[16])
{
  uint32_t z[4] = {0, 0, 0, 0}, v[4];
  int i;
  y[0] ^= be32(x); y[1] ^= be32(x + 4); y[2] ^= be32(x + 8); y[3] ^= be32(x + 12);
  v[0] = h[0]; v[1] = h[1]; v[2] = h[2]; v[3] = h[3];
  for (i = 0; i < 128; i++) {
    uint32_t mask = (uint32_t)0 - ((y[i >> 5] >> (31 - (i & 31))) & 1);
    uint32_t lsb = v[3] & 1;
    z[0] ^= v[0] & mask; z[1] ^= v[1] & mask; z[2] ^= v[2] & mask; z[3] ^= v[3] & mask;
    v[3] = (v[3] >> 1) | (v[2] << 31); v[2] = (v[2] >> 1) | (v[1] << 31);
    v[1] = (v[1] >> 1) | (v[0] << 31);
    v[0] = (v[0] >> 1) ^ (0xe1000000u & ((uint32_t)0 - lsb));
  }
  y[0] = z[0]; y[1] = z[1]; y[2] = z[2]; y[3] = z[3];
}

static void ghash_bytes(uint32_t y[4], const uint32_t h[4], const uint8_t *p, uint32_t n)
{
  uint8_t last[16];
  while (n >= 16) { ghash_block(y, h, p); p += 16; n -= 16; }
  if (n) { memset(last, 0, 16); memcpy(last, p, n); ghash_block(y, h, last); }
}

/* Encrypt (enc=1) or decrypt (enc=0) `buf` in place with a 12-byte IV, and
   write (or check) the 16-byte tag. GHASH runs over the ciphertext in both
   directions, which is why the order of the two halves depends on `enc`.
   Returns 1, or 0 when decrypting and the tag does not match (the buffer
   then holds the plaintext anyway: callers drop the record). */
static int gcm(const uint8_t key[16], const uint8_t iv[12],
               const uint8_t *aad, uint32_t aadlen,
               uint8_t *buf, uint32_t len, uint8_t tag[16], int enc)
{
  uint8_t ctr[16], ks[16], hb[16], lens[16], t[16];
  uint32_t h[4], y[4] = {0, 0, 0, 0}, c, i, n;
  uint8_t *p;

  memset(hb, 0, 16);
  aes128_encrypt_block(key, hb, hb);
  h[0] = be32(hb); h[1] = be32(hb + 4); h[2] = be32(hb + 8); h[3] = be32(hb + 12);
  memcpy(ctr, iv, 12); put32(ctr + 12, 1);
  aes128_encrypt_block(key, hb, ctr);        /* E(K, J0) masks the tag */
  ghash_bytes(y, h, aad, aadlen);
  if (!enc) ghash_bytes(y, h, buf, len);
  c = 2; n = len; p = buf;
  while (n) {
    uint32_t k = n < 16 ? n : 16;
    if ((c & 15) == 0) nab_verify_tick();
    put32(ctr + 12, c++);
    aes128_encrypt_block(key, ks, ctr);
    for (i = 0; i < k; i++) p[i] ^= ks[i];
    p += k; n -= k;
  }
  if (enc) ghash_bytes(y, h, buf, len);
  memset(lens, 0, 16);
  put32(lens + 4, aadlen * 8); put32(lens + 12, len * 8);
  ghash_block(y, h, lens);
  for (i = 0; i < 4; i++) put32(t + 4 * i, y[i]);
  for (i = 0; i < 16; i++) t[i] ^= hb[i];
  if (enc) { memcpy(tag, t, 16); return 1; }
  return ct_eq(tag, t, 16);
}

/* The per-record nonce: the IV xor the 64-bit sequence number, right-aligned (5.3). */
/* The session's AEAD: AES-128-GCM, or ChaCha20-Poly1305 when the server
   chose it (a session with a certificate offers it first: about six
   times cheaper on this processor, tools/cost). */
static int aead(const uint8_t *key, const uint8_t iv[12], const uint8_t *aad, uint32_t aadlen,
                uint8_t *buf, uint32_t len, uint8_t tag[16], int enc)
{
#ifdef NAB_TLS_CERTS
  if (T.suite) return chachapoly(key, iv, aad, aadlen, buf, len, tag, enc);
#endif
  return gcm(key, iv, aad, aadlen, buf, len, tag, enc);
}

static void nonce(uint8_t out[12], const uint8_t iv[12], uint32_t hi, uint32_t lo)
{
  memcpy(out, iv, 12);
  out[4] ^= (uint8_t)(hi >> 24); out[5] ^= (uint8_t)(hi >> 16);
  out[6] ^= (uint8_t)(hi >> 8);  out[7] ^= (uint8_t)hi;
  out[8] ^= (uint8_t)(lo >> 24); out[9] ^= (uint8_t)(lo >> 16);
  out[10] ^= (uint8_t)(lo >> 8); out[11] ^= (uint8_t)lo;
}

static void seq_inc(uint32_t *hi, uint32_t *lo) { if (++*lo == 0) ++*hi; }

/* One protected record (5.2) into `out`: header, then the plaintext
   followed by its content type, encrypted; `len` + NAB_TLS_OVERHEAD bytes. */
static void seal(uint8_t *out, const uint8_t *in, uint32_t len, uint8_t type)
{
  uint8_t nn[12];
  out[0] = 23; out[1] = 3; out[2] = 3; put16(out + 3, len + 1 + 16);
  if (in != out + 5) memmove(out + 5, in, len);
  out[5 + len] = type;
  nonce(nn, T.civ, T.cseq_hi, T.cseq_lo);
  aead(T.ckey, nn, out, 5, out + 5, len + 1, out + 5 + len + 1, 1);
  seq_inc(&T.cseq_hi, &T.cseq_lo);
}

/* --- failing --------------------------------------------------------------- */

/* A fatal alert to the server (6.2), protected when we have keys, and the
   end of this connection. Overwrites anything pending: nothing else matters now. */
static int fail(uint8_t desc)
{
  uint8_t a[2] = { 2, desc };
  if (T.cli_keys) {
    seal(T.pending, a, 2, 21);
    T.pend = 2 + NAB_TLS_OVERHEAD;
  } else {
    T.pending[0] = 21; T.pending[1] = 3; T.pending[2] = 3; put16(T.pending + 3, 2);
    T.pending[5] = 2; T.pending[6] = desc;
    T.pend = 7;
  }
  T.state = NAB_TLS_FAILED;
  T.app_ready = 0;
  T.rxlen = 0;
  return -2;
}

enum {
  A_CLOSE_NOTIFY = 0, A_UNEXPECTED_MESSAGE = 10, A_BAD_RECORD_MAC = 20,
  A_RECORD_OVERFLOW = 22, A_HANDSHAKE_FAILURE = 40, A_ILLEGAL_PARAMETER = 47,
  A_DECODE_ERROR = 50, A_PROTOCOL_VERSION = 70, A_INTERNAL_ERROR = 80
};

/* --- ClientHello ------------------------------------------------------------ */

int nab_tls_start(const uint8_t psk[NAB_TLS_PSK],
                  const uint8_t *identity, uint32_t idlen,
                  const uint8_t random[32], uint8_t *out, uint32_t cap)
{
  uint8_t binder_key[32], fk[32], h[32];
  uint8_t *m, *ext, *psk_ext, *binders;
  uint32_t n, body, trunc;

  S = &T0;
  memset(&T, 0, sizeof T);            /* .extbss is NOLOAD: nothing else clears it */
  T.rx = RX0; T.rxcap = sizeof RX0; T.hs = HS0; T.hscap = sizeof HS0;
  sha256_init(&T.th);
  if (idlen == 0 || idlen > 32) return 0;
  /* 4 header + 2 + 32 + 1 + 4 + 2 + extensions (7 + 6 + 5 + (4 + 2 + 2 + idlen + 4 + 2 + 1 + 32)) */
  n = 5 + 4 + 2 + 32 + 1 + 4 + 2 + 2 + 7 + 6 + 5 + 4 + 2 + 2 + idlen + 4 + 2 + 1 + 32;
  if (cap < n) return 0;

  /* 7.1: Early Secret and the binder key, before the message exists. */
  extract(T.early, NULL, 0, psk, 32);
  derive_secret(binder_key, T.early, "ext binder");          /* TH("") */
  expand_label(fk, 32, binder_key, "finished", NULL, 0);

  out[0] = 22; out[1] = 3; out[2] = 3;            /* record: handshake, 0x0303 */
  m = out + 5;
  m[0] = 1;                                       /* client_hello */
  m[4] = 3; m[5] = 3;                             /* legacy_version */
  memcpy(m + 6, random, 32);
  m[38] = 0;                                      /* legacy_session_id: empty */
  put16(m + 39, 2); m[41] = 0x13; m[42] = 0x01;   /* TLS_AES_128_GCM_SHA256 */
  m[43] = 1; m[44] = 0;                           /* legacy_compression_methods */
  ext = m + 47;
  /* supported_versions (4.2.1): TLS 1.3 only */
  put16(ext, 0x002b); put16(ext + 2, 3); ext[4] = 2; ext[5] = 3; ext[6] = 4; ext += 7;
  /* psk_key_exchange_modes (4.2.9): psk_ke */
  put16(ext, 0x002d); put16(ext + 2, 2); ext[4] = 1; ext[5] = 0; ext += 6;
  /* max_fragment_length (RFC 6066 4): 2^12 */
  put16(ext, 0x0001); put16(ext + 2, 1); ext[4] = 4; ext += 5;
  /* pre_shared_key (4.2.11), last */
  psk_ext = ext;
  put16(ext, 0x0029); put16(ext + 2, 2 + 2 + idlen + 4 + 2 + 1 + 32); ext += 4;
  put16(ext, 2 + idlen + 4); ext += 2;            /* identities */
  put16(ext, idlen); memcpy(ext + 2, identity, idlen); ext += 2 + idlen;
  put32(ext, 0); ext += 4;                        /* obfuscated_ticket_age: external PSK */
  binders = ext;
  put16(ext, 33); ext[2] = 32; ext += 3;          /* binders: one, 32 bytes */
  (void)psk_ext;
  trunc = (uint32_t)(binders - m);                /* 4.2.11.2: up to the identities */
  body = trunc + 2 + 1 + 32;
  m[1] = 0; put16(m + 2, body - 4);               /* handshake length */
  put16(m + 45, body - 47);                       /* extensions length */
  put16(out + 3, body);

  sha256_update(&T.th, m, trunc);
  th_now(h);
  hmac_sha256(ext, fk, 32, h, 32);                /* the binder */
  sha256_update(&T.th, m + trunc, 2 + 1 + 32);

  memset(binder_key, 0, sizeof binder_key);
  memset(fk, 0, sizeof fk);
  T.state = NAB_TLS_HANDSHAKE;
  return (int)(5 + body);
}

/* --- the server's flight ---------------------------------------------------- */

static int on_server_hello(const uint8_t *m, uint32_t n)
{
  static const uint8_t hrr[8] = { 0xcf, 0x21, 0xad, 0x74, 0xe5, 0x9a, 0x61, 0x11 };
  uint32_t p, extlen, saw_version = 0, saw_psk = 0;
  uint8_t derived[32], ikm[32];
  if (T.srv_keys) return fail(A_UNEXPECTED_MESSAGE);
  /* legacy_version, random, session id echo (empty, as ours), suite, compression */
  if (n < 2 + 32 + 1 + 2 + 1 + 2 || m[0] != 3 || m[1] != 3) return fail(A_DECODE_ERROR);
  if (memcmp(m + 2, hrr, 8) == 0) return fail(A_HANDSHAKE_FAILURE);   /* HelloRetryRequest (4.1.4) */
  p = 34;
  if (m[p] != 0) return fail(A_ILLEGAL_PARAMETER);
  p++;
  if (m[p] != 0x13 || m[p + 1] != 0x01) {
#ifdef NAB_TLS_CERTS
    if (!(T.certs && m[p] == 0x13 && m[p + 1] == 0x03))
#endif
    return fail(A_ILLEGAL_PARAMETER);
  }
  T.suite = m[p + 1] == 0x03;                     /* what we offered: ours, or AES */
  p += 2;
  memset(ikm, 0, sizeof ikm);
  if (m[p] != 0) return fail(A_ILLEGAL_PARAMETER);
  p++;
  extlen = get16(m + p); p += 2;
  if (p + extlen != n) return fail(A_DECODE_ERROR);
  while (extlen >= 4) {
    uint32_t type = get16(m + p), len = get16(m + p + 2);
    p += 4; extlen -= 4;
    if (len > extlen) return fail(A_DECODE_ERROR);
    if (type == 0x002b) {                         /* supported_versions: 0x0304 */
      if (len != 2 || m[p] != 3 || m[p + 1] != 4) return fail(A_PROTOCOL_VERSION);
      saw_version = 1;
#ifdef NAB_TLS_CERTS
    } else if (type == 0x0033 && T.certs) {       /* key_share: X25519, 32 bytes (4.2.8) */
      static const uint8_t zero[32] = {0};
      if (len != 36 || get16(m + p) != 0x001d || get16(m + p + 2) != 32)
        return fail(A_ILLEGAL_PARAMETER);
      crypto_scalarmult_curve25519_tweet(ikm, T.eph, m + p + 4);
      memset(T.eph, 0, sizeof T.eph);
      if (ct_eq(ikm, zero, 32)) return fail(A_ILLEGAL_PARAMETER);   /* a low-order point */
      saw_psk = 1;                                /* the key exchange this mode needs */
#endif
    } else if (type == 0x0029
#ifdef NAB_TLS_CERTS
               && !T.certs
#endif
               ) {                                /* pre_shared_key: our one identity */
      if (len != 2 || get16(m + p) != 0) return fail(A_ILLEGAL_PARAMETER);
      saw_psk = 1;
    } else {
      /* key_share would mean the server chose psk_dhe_ke we did not offer;
         anything else we did not ask for. 4.1.3: refuse. */
      return fail(A_ILLEGAL_PARAMETER);
    }
    p += len; extlen -= len;
  }
  if (extlen || !saw_version || !saw_psk) return fail(A_ILLEGAL_PARAMETER);

  /* 7.1: Handshake Secret = HKDF-Extract(Derive-Secret(Early, "derived",
     ""), 0^32) -- no (EC)DHE, so the IKM is a hash length of zeros -- then
     the handshake traffic secrets over CH..SH (the caller has hashed this
     ServerHello already), then the Master Secret the same way. */
  {
    static const uint8_t zero[32] = {0};
    uint8_t empty[32], hs[32];
    sha256(empty, NULL, 0);
    expand_label(derived, 32, T.early, "derived", empty, 32);
    extract(hs, derived, 32, ikm, 32);            /* zeros, or the X25519 secret */
    memset(ikm, 0, sizeof ikm);
    derive_secret(T.c_hs, hs, "c hs traffic");
    derive_secret(T.s_hs, hs, "s hs traffic");
    expand_label(derived, 32, hs, "derived", empty, 32);
    extract(T.master, derived, 32, zero, 32);
    memset(hs, 0, sizeof hs);
  }
  memset(T.early, 0, sizeof T.early);
  traffic_keys(T.ckey, T.civ, T.c_hs);
  traffic_keys(T.skey, T.siv, T.s_hs);
  T.cseq_hi = T.cseq_lo = T.sseq_hi = T.sseq_lo = 0;
  T.srv_keys = 1; T.cli_keys = 1;
  memset(derived, 0, sizeof derived);
  return 0;
}

static int on_finished(const uint8_t *m, uint32_t n)
{
  uint8_t fk[32], h[32], want[32], fin[4 + 32];
  if (T.srv_keys != 1) return fail(A_UNEXPECTED_MESSAGE);
#ifdef NAB_TLS_CERTS
  if (T.certs && T.proved != 2) return fail(A_UNEXPECTED_MESSAGE);   /* no Finished without both */
#endif
  if (n != 32) return fail(A_DECODE_ERROR);
  /* 4.4.4: verify_data = HMAC(finished_key(s_hs), TH(CH..EncryptedExtensions)).
     The caller hashes a message after this handler, so the transcript is right here. */
  expand_label(fk, 32, T.s_hs, "finished", NULL, 0);
  th_now(h);
  hmac_sha256(want, fk, 32, h, 32);
  if (!ct_eq(want, m, 32)) return fail(A_DECODE_ERROR);   /* 4.4.4: decrypt_error */
  sha256_update(&T.th, m - 4, 4 + 32);                     /* the server Finished itself */

  /* 7.1: application traffic secrets over CH..server Finished. */
  {
    uint8_t s_ap[32], c_ap[32];
    derive_secret(s_ap, T.master, "s ap traffic");
    derive_secret(c_ap, T.master, "c ap traffic");
    /* 4.4.4: our Finished, under the handshake keys, over CH..server Finished. */
    expand_label(fk, 32, T.c_hs, "finished", NULL, 0);
    th_now(h);
    fin[0] = 20; fin[1] = 0; fin[2] = 0; fin[3] = 32;
    hmac_sha256(fin + 4, fk, 32, h, 32);
    seal(T.pending, fin, sizeof fin, 22);
    T.pend = sizeof fin + NAB_TLS_OVERHEAD;
    sha256_update(&T.th, fin, sizeof fin);                  /* for resumption secrets we never derive */
    /* Keys change: the server to application keys now, we after our Finished went out. */
    traffic_keys(T.skey, T.siv, s_ap);
    traffic_keys(T.ckey, T.civ, c_ap);
    T.sseq_hi = T.sseq_lo = 0;
    T.cseq_hi = T.cseq_lo = 0;
    memset(s_ap, 0, sizeof s_ap); memset(c_ap, 0, sizeof c_ap);
  }
  memset(T.c_hs, 0, sizeof T.c_hs); memset(T.s_hs, 0, sizeof T.s_hs);
  memset(T.master, 0, sizeof T.master);
  memset(fk, 0, sizeof fk);
  T.srv_keys = 2; T.cli_keys = 2;
  T.state = NAB_TLS_READY;
  return 0;
}

#ifdef NAB_TLS_CERTS
/* --- a server with a certificate ----------------------------------------- */

static const struct nab_tls_anchor *ANCHORS;
static uint32_t NANCHORS;
/* Up to four certificates of the chain, parsed in place in hs[]. */
#define CHAIN_MAX 4
static nab_cert CHAIN[CHAIN_MAX] SECTION_EXTBSS;

void nab_tls_anchors(const struct nab_tls_anchor *a, uint32_t n) { ANCHORS = a; NANCHORS = n; }

/* Signed by one of the built-in roots? Found by the issuer's name, proven
   by the root's key: a name alone is what anyone can write. */
static int anchored(const nab_cert *c)
{
  for (uint32_t i = 0; i < NANCHORS; i++) {
    const struct nab_tls_anchor *a = &ANCHORS[i];
    nab_span alg = { a->alg, a->alg_len }, par = { a->param, a->param_len }, key = { a->key, a->key_len };
    if (c->issuer.len != a->subject_len || memcmp(c->issuer.p, a->subject, a->subject_len)) continue;
    if (nab_x509_signed_by(c, &alg, &par, &key, nab_verify_tick)) return 1;
  }
  return 0;
}

/* 4.4.2: the chain, leaf first, each link signed by the next, the last by
   an anchor; the leaf names the host; every certificate in date. */
static int on_certificate(const uint8_t *m, uint32_t n)
{
  uint32_t p, end, k = 0;
  if (T.srv_keys != 1 || T.proved) return fail(A_UNEXPECTED_MESSAGE);
  if (n < 4 || m[0] != 0) return fail(A_DECODE_ERROR);          /* no request context */
  end = 4 + (((uint32_t)m[1] << 16) | ((uint32_t)m[2] << 8) | m[3]);
  if (end != n) return fail(A_DECODE_ERROR);
  for (p = 4; p < end && k < CHAIN_MAX; k++) {
    uint32_t cl, el;
    if (end - p < 3) return fail(A_DECODE_ERROR);
    cl = ((uint32_t)m[p] << 16) | ((uint32_t)m[p + 1] << 8) | m[p + 2];
    p += 3;
    if (end - p < cl + 2) return fail(A_DECODE_ERROR);
    if (nab_x509_parse(&CHAIN[k], m + p, cl)) return fail(42);  /* bad_certificate */
    p += cl;
    el = get16(m + p);
    p += 2 + el;
    if (p > end) return fail(A_DECODE_ERROR);
  }
  if (!k) return fail(A_DECODE_ERROR);
  if (!nab_x509_names_host(&CHAIN[0], T.host)) return fail(42);
  for (uint32_t i = 0; i < k; i++) {
    if (!nab_x509_valid_at(&CHAIN[i], T.now)) return fail(45);  /* certificate_expired */
    if (anchored(&CHAIN[i])) break;
    if (i + 1 == k || !CHAIN[i + 1].ca || !nab_x509_issued_by(&CHAIN[i], &CHAIN[i + 1])
        || !nab_x509_signed_by(&CHAIN[i], &CHAIN[i + 1].key_alg, &CHAIN[i + 1].key_param,
                               &CHAIN[i + 1].key, nab_verify_tick))
      return fail(48);                                           /* unknown_ca */
  }
  /* The leaf's key, kept: hs[] is overwritten by the next message. */
  if (CHAIN[0].key_alg.len > sizeof T.key_alg || CHAIN[0].key_param.len > sizeof T.key_param
      || CHAIN[0].key.len > sizeof T.key) return fail(42);
  memcpy(T.key_alg, CHAIN[0].key_alg.p, CHAIN[0].key_alg.len); T.alg_len = CHAIN[0].key_alg.len;
  memcpy(T.key_param, CHAIN[0].key_param.p, CHAIN[0].key_param.len); T.param_len = CHAIN[0].key_param.len;
  memcpy(T.key, CHAIN[0].key.p, CHAIN[0].key.len); T.key_len = CHAIN[0].key.len;
  T.proved = 1;
  return 0;
}

/* 4.4.3: the server's signature over 64 spaces, the context string, a zero
   and the transcript up to the Certificate. */
static int on_certificate_verify(const uint8_t *m, uint32_t n)
{
  static const char ctx[] = "TLS 1.3, server CertificateVerify";   /* with its zero */
  static const uint8_t P256[8] = {0x2a,0x86,0x48,0xce,0x3d,0x03,0x01,0x07};
  static const uint8_t P384[5] = {0x2b,0x81,0x04,0x00,0x22};
  uint8_t content[64 + sizeof ctx + 32], h[48];
  uint32_t scheme, sl, hl;
  int how;
  nab_span alg = { T.key_alg, T.alg_len }, par = { T.key_param, T.param_len }, key = { T.key, T.key_len };
  if (T.proved != 1) return fail(A_UNEXPECTED_MESSAGE);
  if (n < 4) return fail(A_DECODE_ERROR);
  scheme = get16(m); sl = get16(m + 2);
  if (4 + sl != n) return fail(A_DECODE_ERROR);
  switch (scheme) {
  case 0x0403: hl = 32; how = NAB_SIG_ECDSA;
    if (par.len != sizeof P256 || memcmp(par.p, P256, sizeof P256)) return fail(A_ILLEGAL_PARAMETER);
    break;
  case 0x0503: hl = 48; how = NAB_SIG_ECDSA;
    if (par.len != sizeof P384 || memcmp(par.p, P384, sizeof P384)) return fail(A_ILLEGAL_PARAMETER);
    break;
  case 0x0804: hl = 32; how = NAB_SIG_PSS; break;
  case 0x0805: hl = 48; how = NAB_SIG_PSS; break;
  default: return fail(A_ILLEGAL_PARAMETER);       /* not one we offered */
  }
  memset(content, 0x20, 64);
  memcpy(content + 64, ctx, sizeof ctx);
  th_now(content + 64 + sizeof ctx);
  if (hl == 32) sha256(h, content, sizeof content);
  else nab_sha384(h, content, sizeof content);
  if (!nab_pki_verify(&alg, &par, &key, how, m + 4, sl, h, hl, nab_verify_tick))
    return fail(51);                               /* decrypt_error */
  T.proved = 2;
  return 0;
}

int nab_tls_start_cert(const char *host, const char now[14], const uint8_t random[32],
                       const uint8_t secret[32], uint8_t *out, uint32_t cap)
{
  static const uint8_t zero[32] = {0};
  static const uint8_t fixed[] = {
    0x00,0x2b,0x00,0x03,0x02,0x03,0x04,                    /* supported_versions: 1.3 */
    0x00,0x0a,0x00,0x04,0x00,0x02,0x00,0x1d,               /* supported_groups: x25519 */
    /* signature_algorithms: what nab_pki checks -- ECDSA P-256 and P-384,
       RSA-PSS; PKCS#1 for the certificates only (4.2.3) */
    0x00,0x0d,0x00,0x0e,0x00,0x0c,0x04,0x03,0x05,0x03,0x08,0x04,0x08,0x05,0x04,0x01,0x05,0x01,
  };
  uint32_t hl = (uint32_t)strlen(host), n;
  uint8_t *m, *e;
  S = &T1;
  memset(&T, 0, sizeof T);
  T.rx = RX1; T.rxcap = sizeof RX1; T.hs = HS1; T.hscap = sizeof HS1;
  T.certs = 1;
  sha256_init(&T.th);
  if (hl == 0 || hl >= sizeof T.host) return 0;
  n = 5 + 4 + 2 + 32 + 1 + 2 + 4 + 2 + 2 + (9 + hl) + sizeof fixed + 42;
  if (cap < n) return 0;
  memcpy(T.host, host, hl + 1);
  memcpy(T.now, now, 14);
  memcpy(T.eph, secret, 32);
  extract(T.early, NULL, 0, zero, 32);              /* 7.1: no PSK, 0^32 */

  out[0] = 22; out[1] = 3; out[2] = 1;              /* record: handshake, 0x0301 (5.1) */
  m = out + 5;
  m[0] = 1;
  m[4] = 3; m[5] = 3;
  memcpy(m + 6, random, 32);
  m[38] = 0;
  put16(m + 39, 4);
  m[41] = 0x13; m[42] = 0x03;                       /* TLS_CHACHA20_POLY1305_SHA256 first */
  m[43] = 0x13; m[44] = 0x01;                       /* then TLS_AES_128_GCM_SHA256 */
  m[45] = 1; m[46] = 0;
  e = m + 49;
  put16(e, 0x0000); put16(e + 2, hl + 5); put16(e + 4, hl + 3); e[6] = 0; put16(e + 7, hl);
  memcpy(e + 9, host, hl); e += 9 + hl;             /* server_name (RFC 6066 3) */
  memcpy(e, fixed, sizeof fixed); e += sizeof fixed;
  put16(e, 0x0033); put16(e + 2, 38); put16(e + 4, 36); put16(e + 6, 0x001d); put16(e + 8, 32);
  {
    /* The base point by hand: strip_tweetnacl keeps crypto_scalarmult (the
       adoption key exchange uses it) and drops crypto_scalarmult_base. */
    static const uint8_t nine[32] = {9};
    crypto_scalarmult_curve25519_tweet(e + 10, T.eph, nine);
  }
  e += 42;                                          /* key_share */
  put16(m + 47, (uint32_t)(e - (m + 49)));
  m[1] = 0; put16(m + 2, (uint32_t)(e - m) - 4);
  put16(out + 3, (uint32_t)(e - m));
  sha256_update(&T.th, m, (uint32_t)(e - m));
  T.state = NAB_TLS_HANDSHAKE;
  return (int)(5 + (e - m));
}
#endif

/* One handshake message: type, body. The transcript is updated here for
   every message but Finished, which checks it first. */
static int on_handshake(uint8_t type, const uint8_t *hdr, uint32_t n)
{
  const uint8_t *m = hdr + 4;
  switch (type) {
  case 2:                                         /* server_hello */
    sha256_update(&T.th, hdr, 4 + n);
    return on_server_hello(m, n);
  case 8:                                         /* encrypted_extensions (4.3.1) */
    if (T.srv_keys != 1) return fail(A_UNEXPECTED_MESSAGE);
    /* Only max_fragment_length may be echoed; whatever is there, we
       asked for nothing that needs reading. */
    sha256_update(&T.th, hdr, 4 + n);
    return 0;
  case 20:                                        /* finished */
    return on_finished(m, n);
#ifdef NAB_TLS_CERTS
  case 11:                                        /* Certificate */
    if (!T.certs) return fail(A_UNEXPECTED_MESSAGE);
    if (on_certificate(m, n) < 0) return -1;
    sha256_update(&T.th, hdr, 4 + n);
    return 0;
  case 15:                                        /* CertificateVerify: over CH..Certificate */
    if (!T.certs) return fail(A_UNEXPECTED_MESSAGE);
    if (on_certificate_verify(m, n) < 0) return -1;
    sha256_update(&T.th, hdr, 4 + n);
    return 0;
  case 13:                                        /* CertificateRequest: we have none to give */
    return fail(A_UNEXPECTED_MESSAGE);
#else
  case 11: case 13: case 15:                      /* Certificate, CertificateRequest, CertificateVerify */
    return fail(A_UNEXPECTED_MESSAGE);            /* 4.2.11: not with a PSK */
#endif
  case 24:                                        /* key_update: not supported, 4.6.3 */
    return fail(A_UNEXPECTED_MESSAGE);
  default:
    return fail(A_UNEXPECTED_MESSAGE);
  }
}

/* Decrypted handshake bytes, in any framing (5.1: messages may span
   records and share them). NewSessionTicket (4.6.1) is dropped unread:
   it would not fit hs[] and we never resume. */
static int hs_feed(const uint8_t *p, uint32_t n)
{
  while (n) {
    if (T.skip) {
      uint32_t k = n < T.skip ? n : T.skip;
      p += k; n -= k; T.skip -= k;
      continue;
    }
    if (T.hslen < 4) {
      uint32_t k = 4 - T.hslen;
      if (k > n) k = n;
      memcpy(T.hs + T.hslen, p, k);
      T.hslen += k; p += k; n -= k;
      if (T.hslen < 4) return 0;
      if (T.hs[0] == 4 && T.srv_keys == 2) {      /* new_session_ticket after the handshake */
        T.skip = ((uint32_t)T.hs[1] << 16) | ((uint32_t)T.hs[2] << 8) | T.hs[3];
        T.hslen = 0;
        continue;
      }
    }
    {
      uint32_t len = ((uint32_t)T.hs[1] << 16) | ((uint32_t)T.hs[2] << 8) | T.hs[3];
      uint32_t k;
      if (4 + len > T.hscap) return fail(A_DECODE_ERROR);
      k = 4 + len - T.hslen;
      if (k > n) k = n;
      memcpy(T.hs + T.hslen, p, k);
      T.hslen += k; p += k; n -= k;
      if (T.hslen < 4 + len) return 0;
      T.hslen = 0;
      if (on_handshake(T.hs[0], T.hs, len) < 0) return -2;
    }
  }
  return 0;
}

/* --- records in ----------------------------------------------------------- */

int nab_tls_in(const uint8_t *data, uint32_t len)
{
  if (T.state != NAB_TLS_HANDSHAKE && T.state != NAB_TLS_READY) return -1;
  if (len > T.rxcap - T.rxlen) len = T.rxcap - T.rxlen;
  memcpy(T.rx + T.rxlen, data, len);
  T.rxlen += len;
  return (int)len;
}

static void drop(uint32_t n)
{
  memmove(T.rx, T.rx + n, T.rxlen - n);
  T.rxlen -= n;
}

int nab_tls_next(void)
{
  if (T.app_ready) return (int)T.app_len;
  if (T.state != NAB_TLS_HANDSHAKE && T.state != NAB_TLS_READY) return -2;
  for (;;) {
    uint32_t len, type;
    uint8_t *body;
    if (T.rxlen < 5) return -1;
    type = T.rx[0];
    len = get16(T.rx + 3);
    if (T.rx[1] != 3 || len > T.rxcap - 5) return fail(A_RECORD_OVERFLOW);
    if (T.rxlen < 5 + len) return -1;
    body = T.rx + 5;

    if (type == 20) {                             /* change_cipher_spec: ignored (D.4) */
      if (len != 1 || body[0] != 1) return fail(A_UNEXPECTED_MESSAGE);
      drop(6);
      continue;
    }
    if (T.srv_keys == 0) {
      /* Plaintext: only the ServerHello, or an alert refusing us (5.1). */
      if (type == 21) {
        drop(5 + len);
        if (len == 2 && body[1] == A_CLOSE_NOTIFY) { T.state = NAB_TLS_CLOSED; return -2; }
        return fail(A_UNEXPECTED_MESSAGE);
      }
      if (type != 22) return fail(A_UNEXPECTED_MESSAGE);
      if (hs_feed(body, len) < 0) return -2;
      drop(5 + len);
      continue;
    }
    if (type != 23) return fail(A_UNEXPECTED_MESSAGE);
    if (len < 1 + 16) return fail(A_DECODE_ERROR);
    {
      uint8_t nn[12], aad[5];
      uint32_t plen = len - 16, ct;
      memcpy(aad, T.rx, 5);
      nonce(nn, T.siv, T.sseq_hi, T.sseq_lo);
      if (!aead(T.skey, nn, aad, 5, body, plen, body + plen, 0)) return fail(A_BAD_RECORD_MAC);
      seq_inc(&T.sseq_hi, &T.sseq_lo);
      /* 5.4: the content type is the last non-zero byte. */
      while (plen && body[plen - 1] == 0) plen--;
      if (plen == 0) return fail(A_UNEXPECTED_MESSAGE);
      ct = body[--plen];
      if (ct == 22) {
        if (hs_feed(body, plen) < 0) return -2;
        drop(5 + len);
        continue;
      }
      if (ct == 21) {
        drop(5 + len);
        if (plen == 2 && body[1] == A_CLOSE_NOTIFY) { T.state = NAB_TLS_CLOSED; T.rxlen = 0; return -2; }
        T.state = NAB_TLS_FAILED; T.rxlen = 0;    /* theirs: nothing to answer */
        return -2;
      }
      if (ct != 23 || T.state != NAB_TLS_READY) return fail(A_UNEXPECTED_MESSAGE);
      T.app_ready = 1;
      T.app_len = plen;
      return (int)plen;
    }
  }
}

void nab_tls_take(uint8_t *dst)
{
  if (!T.app_ready) return;
  if (T.app_len) memcpy(dst, T.rx + 5, T.app_len);
  drop(5 + get16(T.rx + 3));
  T.app_ready = 0;
  T.app_len = 0;
}

int nab_tls_pending(uint8_t *out, uint32_t cap)
{
  uint32_t n = T.pend;
  if (n == 0 || n > cap) return 0;
  memcpy(out, T.pending, n);
  T.pend = 0;
  return (int)n;
}

/* --- records out ---------------------------------------------------------- */

uint32_t nab_tls_out_size(uint32_t len)
{
  uint32_t records = len ? (len + NAB_TLS_FRAG - 1) / NAB_TLS_FRAG : 1;
  return len + records * NAB_TLS_OVERHEAD;
}

int nab_tls_out(const uint8_t *in, uint32_t len, uint8_t *out, uint32_t cap)
{
  uint32_t done = 0, w = 0;
  if (T.state != NAB_TLS_READY || cap < nab_tls_out_size(len)) return 0;
  do {
    uint32_t k = len - done;
    if (k > NAB_TLS_FRAG) k = NAB_TLS_FRAG;
    seal(out + w, in + done, k, 23);
    w += k + NAB_TLS_OVERHEAD;
    done += k;
  } while (done < len);
  return (int)w;
}

int nab_tls_close(uint8_t *out, uint32_t cap)
{
  uint8_t a[2] = { 1, A_CLOSE_NOTIFY };
  if (T.state != NAB_TLS_READY || cap < 2 + NAB_TLS_OVERHEAD) return 0;
  seal(out, a, 2, 21);
  T.state = NAB_TLS_CLOSED;
  return 2 + NAB_TLS_OVERHEAD;
}

int nab_tls_state(void) { return T.state; }

void nab_tls_select(int session)
{
#ifdef NAB_TLS_CERTS
  S = session ? &T1 : &T0;
#else
  (void)session;
#endif
}
