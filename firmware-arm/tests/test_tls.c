/*
 * crypto/nab_tls.c offline, against what OpenSSL did.
 *
 * vectors_tls.h holds a recorded session with `openssl s_server` (every
 * byte each side sent, tls_client --dump) and the PSK derivation
 * recomputed in Python. The replay feeds the server's bytes, one TCP
 * segment at a time as the rabbit's stack would, and requires the client's
 * bytes to come out identical: with a fixed random and no (EC)DHE there is
 * nothing else they could depend on. Then the same session, damaged in the
 * ways a network or an attacker could damage it, must end in a fatal alert
 * and nothing else: no plaintext, no state READY.
 *
 * tls_interop.py is the live half of this; `make tls` runs both.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net/nab_tls.h"
#include "vectors_tls.h"

static int fails;
static unsigned long ticks;
void nab_verify_tick(void);
void nab_verify_tick(void) { ticks++; }

static void check(const char *what, int ok)
{
  printf("  %s  %s\n", ok ? "ok  " : "FAIL", what);
  if (!ok) fails++;
}

static const uint8_t PSK[32] = {
  0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31 };
static const char *IDENTITY = "001d92aabbcc";

static void client_random(uint8_t r[32]) { int i; for (i = 0; i < 32; i++) r[i] = (uint8_t)(0xa0 + i); }

/* Feed `n` bytes in segments of `seg`, draining after each; collect
   plaintext into `pt` (cap `ptcap`) and the client's output into `out`. */
static int feed(const uint8_t *p, uint32_t n, uint32_t seg,
                uint8_t *pt, uint32_t *ptlen, uint32_t ptcap,
                uint8_t *out, uint32_t *outlen, uint32_t outcap)
{
  int r = -1;
  while (n) {
    uint32_t k = n < seg ? n : seg, off = 0;
    while (off < k) {
      int took = nab_tls_in(p + off, k - off);
      if (took < 0) return -2;
      off += (uint32_t)took;
      while ((r = nab_tls_next()) >= 0) {
        if (*ptlen + (uint32_t)r <= ptcap) { nab_tls_take(pt + *ptlen); *ptlen += (uint32_t)r; }
        else { uint8_t *tmp = malloc((size_t)r + 1); nab_tls_take(tmp); free(tmp); }
      }
      *outlen += (uint32_t)nab_tls_pending(out + *outlen, outcap - *outlen);
      if (r == -2) return -2;
      if (took == 0) return -2;
    }
    p += k; n -= k;
  }
  return r;
}

/* The recorded session, the server's lines possibly altered by `tamper`
   (line index, a copy of its bytes) before they are fed. Returns the final
   state; `replayed` says how many client lines matched. */
static int alert_out;   /* the last thing the client wrote was a fatal alert */

static int replay(void (*tamper)(int, uint8_t *, uint32_t), uint32_t seg,
                  int *matched, int *mismatched, uint8_t *plain, uint32_t *plainlen)
{
  uint8_t out[512], rnd[32], *copy;
  uint32_t outlen = 0, pos = 0;
  int i, msg_sent = 0, closed = 0;
  client_random(rnd);
  *matched = *mismatched = 0;
  *plainlen = 0;
  alert_out = 0;
  outlen = (uint32_t)nab_tls_start(PSK, (const uint8_t *)IDENTITY, (uint32_t)strlen(IDENTITY), rnd, out, sizeof out);
  for (i = 0; i < SESSION_LINES; i++) {
    if (session[i].dir == '>') {
      /* The application record and the close_notify are the client's
         doing, on the schedule the live run had: after READY, then after
         the echo. */
      if (!msg_sent && nab_tls_state() == NAB_TLS_READY && session[i].p[0] == 23 && outlen - pos == 0) {
        static const char msg[] = "hello rabbit\n";
        outlen += (uint32_t)nab_tls_out((const uint8_t *)msg, sizeof msg - 1, out + outlen, sizeof out - outlen);
        msg_sent = 1;
      }
      if (!closed && msg_sent && *plainlen >= 13 && outlen - pos == 0 && nab_tls_state() == NAB_TLS_READY) {
        outlen += (uint32_t)nab_tls_close(out + outlen, sizeof out - outlen);
        closed = 1;
      }
      if (outlen - pos >= session[i].n && memcmp(out + pos, session[i].p, session[i].n) == 0) (*matched)++;
      else (*mismatched)++;
      pos += session[i].n;
      if (pos > outlen) pos = outlen;
    } else {
      copy = malloc(session[i].n);
      memcpy(copy, session[i].p, session[i].n);
      if (tamper) tamper(i, copy, session[i].n);
      if (feed(copy, session[i].n, seg, plain, plainlen, 64, out, &outlen, sizeof out) == -2) {
        free(copy);
        /* Our alert, if we sent one: 24 bytes protected (header, level,
           description, content type, tag) or 7 in the clear, level fatal. */
        alert_out = (outlen - pos == 24 && out[pos] == 23)
                 || (outlen - pos == 7 && out[pos] == 21 && out[pos + 5] == 2);
        break;
      }
      free(copy);
    }
  }
  return nab_tls_state();
}

static int alert_sent(void) { return alert_out; }

/* --- tamperings ------------------------------------------------------------- */

static void flip_server_finished(int line, uint8_t *p, uint32_t n)
{ if (line == 1 && n > 100) p[n - 20] ^= 1; }              /* inside the EE+Finished record's ciphertext */

static void hello_retry(int line, uint8_t *p, uint32_t n)
{
  static const uint8_t hrr[8] = { 0xcf, 0x21, 0xad, 0x74, 0xe5, 0x9a, 0x61, 0x11 };
  (void)n;
  if (line == 1) memcpy(p + 5 + 4 + 2, hrr, 8);              /* ServerHello.random */
}

static void other_suite(int line, uint8_t *p, uint32_t n)
{ (void)n; if (line == 1) p[5 + 4 + 2 + 32 + 1 + 1] = 0x02; }  /* TLS_AES_256_GCM_SHA384 */

static void key_share(int line, uint8_t *p, uint32_t n)
{
  /* The first extension in OpenSSL's ServerHello is supported_versions
     (0x002b); rename it key_share (0x0033): a mode we did not offer. */
  uint32_t i;
  if (line != 1) return;
  for (i = 5 + 4 + 2 + 32 + 1 + 2 + 1 + 2; i + 1 < n; i++)
    if (p[i] == 0x00 && p[i + 1] == 0x2b) { p[i + 1] = 0x33; return; }
}

static void flip_echo(int line, uint8_t *p, uint32_t n)
{ if (line == 5 && n > 10) p[8] ^= 0x40; }                 /* the echoed application record */

static void huge_record(int line, uint8_t *p, uint32_t n)
{ (void)n; if (line == 5) { p[3] = 0x40; p[4] = 0x11; } }  /* length 16401: RFC 8446 5.2 maximum + 1 */

int main(void)
{
  uint8_t psk[32], plain[64], out[256], rnd[32];
  uint32_t plainlen;
  int matched, mismatched, st, i;

  printf("== the PSK from the adoption key (RFC 5869, hashlib's answer)\n");
  nab_tls_psk(psk, psk0_key); check("key 0", memcmp(psk, psk0_psk, 32) == 0);
  nab_tls_psk(psk, psk1_key); check("key 1", memcmp(psk, psk1_psk, 32) == 0);
  nab_tls_psk(psk, psk2_key); check("key 2", memcmp(psk, psk2_psk, 32) == 0);
  check("different keys, different PSKs", memcmp(psk0_psk, psk1_psk, 32) != 0);

  printf("== the recorded OpenSSL session, replayed\n");
  st = replay(NULL, 1460, &matched, &mismatched, plain, &plainlen);
  check("every client line reproduced: ClientHello, Finished, application record, close_notify",
        matched == 4 && mismatched == 0);
  check("the echo decrypted", plainlen == 13 && memcmp(plain, "tibbar olleh\n", 13) == 0);
  check("ended in CLOSED after the server's close_notify", st == NAB_TLS_CLOSED);
  st = replay(NULL, 1, &matched, &mismatched, plain, &plainlen);
  check("the same, one byte per segment", matched == 4 && mismatched == 0 && st == NAB_TLS_CLOSED);
  st = replay(NULL, 7, &matched, &mismatched, plain, &plainlen);
  check("the same, seven bytes per segment", matched == 4 && mismatched == 0 && st == NAB_TLS_CLOSED);
  printf("        %lu watchdog ticks over the three replays\n", ticks);

  printf("== damage: each ends in FAILED with a fatal alert and no plaintext\n");
  st = replay(flip_server_finished, 1460, &matched, &mismatched, plain, &plainlen);
  check("a bit flipped in the server's Finished record", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());
  st = replay(hello_retry, 1460, &matched, &mismatched, plain, &plainlen);
  check("a HelloRetryRequest", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());
  st = replay(other_suite, 1460, &matched, &mismatched, plain, &plainlen);
  check("another cipher suite chosen", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());
  st = replay(key_share, 1460, &matched, &mismatched, plain, &plainlen);
  check("a key_share we did not offer", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());
  st = replay(flip_echo, 1460, &matched, &mismatched, plain, &plainlen);
  check("a bit flipped in an application record", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());
  st = replay(huge_record, 1460, &matched, &mismatched, plain, &plainlen);
  check("a record longer than the buffer", st == NAB_TLS_FAILED && plainlen == 0 && alert_sent());

  printf("== refusals\n");
  client_random(rnd);
  check("an identity longer than 32 bytes",
        nab_tls_start(PSK, (const uint8_t *)"0123456789abcdef0123456789abcdef0", 33, rnd, out, sizeof out) == 0);
  check("a buffer too small for the ClientHello",
        nab_tls_start(PSK, (const uint8_t *)IDENTITY, 12, rnd, out, 100) == 0);
  i = nab_tls_start(PSK, (const uint8_t *)IDENTITY, 12, rnd, out, sizeof out);
  check("ClientHello is 129 bytes, as OpenSSL received it", i == 129 && memcmp(out, s0, 129) == 0);
  check("no application data before the handshake", nab_tls_out((const uint8_t *)"x", 1, out, sizeof out) == 0);
  check("no close_notify before the handshake", nab_tls_close(out, sizeof out) == 0);
  check("state is HANDSHAKE", nab_tls_state() == NAB_TLS_HANDSHAKE);
  check("out_size: one record per 4096 bytes",
        nab_tls_out_size(0) == 22 && nab_tls_out_size(4096) == 4118 && nab_tls_out_size(4097) == 4097 + 44);

  printf("%s\n", fails ? "FAILED" : "all passed");
  return fails ? 1 : 0;
}
