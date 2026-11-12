/*
 * nab_tls.c's certificate session (NAB_TLS_CERTS) on a real socket, against
 * a server we did not write.
 *
 *   tls_cert_client HOST PORT SNI NOW ANCHOR.der PATH
 *
 * NOW is "YYYYMMDDHHMMSS" (UTC); ANCHOR.der the one root it trusts. Runs the
 * handshake through nab_tls_select(1), sends "GET PATH HTTP/1.0", reads the
 * answer until the server closes, and prints the suite, the status line and
 * how many bytes came. Exit 0 when the handshake completed and an answer
 * came; 1 otherwise, with the state and the alert.
 */
#define _POSIX_C_SOURCE 200112L
#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "net/nab_tls.h"
#include "net/nab_pki.h"

static unsigned long ticks;
void nab_verify_tick(void);
void nab_verify_tick(void) { ticks++; }

static int send_all(int fd, const uint8_t *p, int n)
{
  while (n > 0) {
    ssize_t w = write(fd, p, (size_t)n);
    if (w <= 0) return -1;
    p += w; n -= (int)w;
  }
  return 0;
}

static uint8_t root_der[8192];

int main(int argc, char **argv)
{
  static uint8_t buf[32768];
  uint8_t random[32], secret[32], req[512], rec[1024];
  struct addrinfo hints, *res;
  struct nab_tls_anchor anchor;
  nab_cert root;
  FILE *f;
  long total = 0;
  char first[120] = "";
  int fd, n, sent = 0, rl;
  if (argc < 7) { fprintf(stderr, "usage: see the header\n"); return 2; }
  f = fopen(argv[5], "rb");
  if (!f) { perror(argv[5]); return 2; }
  n = (int)fread(root_der, 1, sizeof root_der, f);
  fclose(f);
  if (nab_x509_parse(&root, root_der, (uint32_t)n)) { fprintf(stderr, "anchor: not a certificate\n"); return 2; }
  anchor.subject = root.subject.p; anchor.subject_len = root.subject.len;
  anchor.alg = root.key_alg.p; anchor.alg_len = root.key_alg.len;
  anchor.param = root.key_param.p; anchor.param_len = root.key_param.len;
  anchor.key = root.key.p; anchor.key_len = root.key.len;
  nab_tls_anchors(&anchor, 1);

  f = fopen("/dev/urandom", "rb");
  if (!f || fread(random, 1, 32, f) != 32 || fread(secret, 1, 32, f) != 32) return 2;
  fclose(f);
  memset(&hints, 0, sizeof hints);
  hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
  if (getaddrinfo(argv[1], argv[2], &hints, &res)) { perror("getaddrinfo"); return 1; }
  fd = socket(res->ai_family, res->ai_socktype, 0);
  if (connect(fd, res->ai_addr, res->ai_addrlen)) { perror("connect"); return 1; }
  freeaddrinfo(res);

  nab_tls_select(1);
  n = nab_tls_start_cert(argv[3], argv[4], random, secret, buf, sizeof buf);
  if (n <= 0) { printf("client: start refused\n"); return 1; }
  if (send_all(fd, buf, n)) return 1;
  for (;;) {
    ssize_t rd = read(fd, buf, sizeof buf);
    int r = -1, off = 0;
    if (rd <= 0) break;
    while (off < (int)rd) {
      int took = nab_tls_in(buf + off, (uint32_t)(rd - off));
      off += took;
      while ((r = nab_tls_next()) >= 0) {
        static uint8_t pt[20000];
        nab_tls_take(pt);
        if (!total) {
          int k = 0;
          while (k < r && k < (int)sizeof first - 1 && pt[k] != '\r' && pt[k] != '\n') { first[k] = (char)pt[k]; k++; }
          first[k] = 0;
        }
        total += r;
      }
      if (r == -2 || took == 0) break;
    }
    n = nab_tls_pending(rec, sizeof rec);
    if (n && send_all(fd, rec, n)) return 1;
    if (r == -2) break;
    if (!sent && nab_tls_state() == NAB_TLS_READY) {
      printf("client: handshake complete, %lu watchdog ticks\n", ticks);
      rl = snprintf((char *)req, sizeof req, "GET %s HTTP/1.0\r\nHost: %s\r\n\r\n", argv[6], argv[3]);
      n = nab_tls_out(req, (uint32_t)rl, buf, sizeof buf);
      if (n <= 0 || send_all(fd, buf, n)) return 1;
      sent = 1;
    }
  }
  close(fd);
  printf("client: state %d, answer \"%s\", %ld bytes\n", nab_tls_state(), first, total);
  return sent && total > 0 ? 0 : 1;
}

/* tweetnacl wants one; the X25519 secret comes from the caller. */
void randombytes(unsigned char *x, unsigned long long n) { memset(x, 0, (size_t)n); }
