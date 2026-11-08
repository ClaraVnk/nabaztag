/*
 * crypto/nab_tls.c on a real socket, against a TLS stack we did not write.
 *
 *   tls_client HOST PORT PSK_HEX IDENTITY MESSAGE [--random HEX32] [--dump FILE]
 *
 * Connects, runs the handshake through the same six calls the bytecode's
 * opcodes make, sends MESSAGE as application data, waits for the server's
 * answer (printed), sends close_notify and exits 0. Any failure prints the
 * state and exits 1. --dump records every byte sent and received as
 * `>` / `<` hex lines, which vectors_tls.py turns into an offline replay
 * test: with a fixed client random and no (EC)DHE, the client's whole key
 * schedule is a function of the bytes the server sent, so a recorded
 * OpenSSL session is a deterministic oracle.
 *
 * tls_interop.py drives this against `openssl s_server` and Python's ssl.
 */
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "net/nab_tls.h"

static unsigned long ticks;
void nab_verify_tick(void);
void nab_verify_tick(void) { ticks++; }

static FILE *dump;

static void dumpline(char dir, const uint8_t *p, int n)
{
  int i;
  if (!dump) return;
  fputc(dir, dump);
  for (i = 0; i < n; i++) fprintf(dump, "%02x", p[i]);
  fputc('\n', dump);
}

static int unhex(const char *s, uint8_t *out, int max)
{
  int n = 0;
  while (s[0] && s[1] && n < max) {
    unsigned v;
    if (sscanf(s, "%2x", &v) != 1) return -1;
    out[n++] = (uint8_t)v;
    s += 2;
  }
  return s[0] ? -1 : n;
}

static int send_all(int fd, const uint8_t *p, int n)
{
  dumpline('>', p, n);
  while (n > 0) {
    ssize_t w = write(fd, p, (size_t)n);
    if (w <= 0) return -1;
    p += w; n -= (int)w;
  }
  return 0;
}

/* Everything nab_tls has queued for the server. */
static int flush_pending(int fd)
{
  uint8_t out[128];
  int n = nab_tls_pending(out, sizeof out);
  return n ? send_all(fd, out, n) : 0;
}

int main(int argc, char **argv)
{
  uint8_t psk[32], random[32], buf[8192], *msg_out;
  const char *host, *identity, *message;
  int port, fd, i, n, got = 0, phase = 0;
  struct addrinfo hints, *res;
  char service[8];

  if (argc < 6) { fprintf(stderr, "usage: see the header\n"); return 2; }
  host = argv[1]; port = atoi(argv[2]); identity = argv[4]; message = argv[5];
  if (unhex(argv[3], psk, 32) != 32) { fprintf(stderr, "psk: 64 hex digits\n"); return 2; }
  for (i = 0; i < 32; i++) random[i] = (uint8_t)(0xa0 + i);
  for (i = 6; i < argc; i++) {
    if (!strcmp(argv[i], "--random") && i + 1 < argc) {
      if (unhex(argv[++i], random, 32) != 32) { fprintf(stderr, "random: 64 hex digits\n"); return 2; }
    } else if (!strcmp(argv[i], "--dump") && i + 1 < argc) {
      dump = fopen(argv[++i], "w");
    }
  }

  memset(&hints, 0, sizeof hints);
  hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
  snprintf(service, sizeof service, "%d", port);
  if (getaddrinfo(host, service, &hints, &res) != 0) { perror("getaddrinfo"); return 1; }
  fd = socket(res->ai_family, res->ai_socktype, 0);
  if (connect(fd, res->ai_addr, res->ai_addrlen) != 0) { perror("connect"); return 1; }
  freeaddrinfo(res);

  n = nab_tls_start(psk, (const uint8_t *)identity, (uint32_t)strlen(identity), random, buf, sizeof buf);
  if (n <= 0) { fprintf(stderr, "nab_tls_start refused\n"); return 1; }
  printf("client: ClientHello %d bytes, identity '%s'\n", n, identity);
  if (send_all(fd, buf, n) < 0) { perror("write"); return 1; }

  msg_out = malloc(nab_tls_out_size((uint32_t)strlen(message)));
  for (;;) {
    int r;
    ssize_t rd = read(fd, buf, sizeof buf);
    if (rd <= 0) {
      printf("client: socket closed by the server, state %d\n", nab_tls_state());
      break;
    }
    dumpline('<', buf, (int)rd);
    /* read() here returns up to 8 KB at once; the rabbit's TCP hands over
       one segment at a time. Feed what fits, drain, feed the rest. */
    {
      int off = 0;
      r = -1;
      while (off < (int)rd) {
        int took = nab_tls_in(buf + off, (uint32_t)rd - (uint32_t)off);
        if (took < 0) { printf("client: input refused\n"); break; }
        off += took;
        while ((r = nab_tls_next()) >= 0) {
          uint8_t *pt = malloc((size_t)r + 1);
          nab_tls_take(pt);
          pt[r] = 0;
          printf("client: application data %d bytes: %.*s%s\n", r, r > 60 ? 60 : r, pt,
                 r > 60 ? "..." : "");
          got += r;
          free(pt);
        }
        if (r == -2 || took == 0) break;
      }
    }
    if (flush_pending(fd) < 0) { perror("write"); return 1; }
    if (r == -2) {
      printf("client: connection over, state %d\n", nab_tls_state());
      break;
    }
    if (phase == 0 && nab_tls_state() == NAB_TLS_READY) {
      phase = 1;
      printf("client: handshake complete, %lu watchdog ticks so far\n", ticks);
      n = nab_tls_out((const uint8_t *)message, (uint32_t)strlen(message), msg_out,
                      nab_tls_out_size((uint32_t)strlen(message)));
      if (n <= 0) { printf("client: nab_tls_out refused\n"); return 1; }
      printf("client: sent %d bytes of application data in %d record(s)\n", n,
             (int)((strlen(message) + NAB_TLS_FRAG - 1) / NAB_TLS_FRAG));
      if (send_all(fd, msg_out, n) < 0) { perror("write"); return 1; }
    }
    if (phase == 1 && got >= (int)strlen(message)) {
      phase = 2;
      n = nab_tls_close(buf, sizeof buf);
      printf("client: close_notify %d bytes\n", n);
      if (send_all(fd, buf, n) < 0) { perror("write"); return 1; }
      /* Give the server its close_notify back, then stop. */
      shutdown(fd, SHUT_WR);
    }
  }
  close(fd);
  if (dump) fclose(dump);
  printf("client: %lu watchdog ticks, final state %d, %d bytes received\n",
         ticks, nab_tls_state(), got);
  if (phase < 2) return 1;
  return (nab_tls_state() == NAB_TLS_CLOSED) ? 0 : 1;
}
