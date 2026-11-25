/*
 * shatest.c -- the SHA-256 of sha256.asm and the HMAC of sha256.c run on the
 * PIC itself (the emulator: ../emulator/tools/v1-sha-proof.sh), the digests
 * on the UART for the script to compare with Python's hashlib and hmac:
 * FIPS 180-4's "abc", the 448-bit message and the million 'a', the empty
 * message, RFC 4231's cases 1 to 5 (6 and 7 have 131-byte keys, above the
 * 64 our hmac_init takes: the rabbit's keys are 32 bytes), and the 1000-byte
 * pattern of tools/sha256-host.c. Built with `make shatest`, linked like
 * the loader (at 0, the configuration words of config.c: the watchdog is
 * on, as on Lazare).
 *
 * Lines: `sha <name> <64 hex>`, `hmac <name> <64 hex>`, `sha done`.
 */
#include "board.h"
#include "sha256.h"

static uint8_t out[32], key[64], msg[1000];

static void print(const __code char *kind, const __code char *name)
{
  uint8_t i;
  log_s(kind); uart_putc(' '); log_s(name); uart_putc(' ');
  for(i = 0; i < 32; i++) log_hex8(out[i]);
  log_nl();
}

static void fill(__data uint8_t *d, uint8_t v, uint16_t n) { while(n--) *d++ = v; }
static void copy(__data uint8_t *d, const __code char *s, uint8_t n) { while(n--) *d++ = (uint8_t)*s++; }

static void sha(const __code char *name, __data const uint8_t *p, uint16_t n)
{
  sha_init(); sha_total += n;
  while(n--) sha_byte(*p++);
  sha_digest(out);
  print("sha", name);
}

static void hmac(const __code char *name, uint8_t klen, uint16_t n)
{
  hmac_init(key, klen); hmac_update(msg, n); hmac_final(out);
  print("hmac", name);
}

void main(void)
{
  uint16_t k, m;
  uart_init();
  log_line("shatest");

  sha("empty", msg, 0);
  copy(msg, "abc", 3); sha("abc", msg, 3);
  copy(msg, "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56); sha("448", msg, 56);
  for(k = 0; k < 1000; k++) msg[k] = (uint8_t)(k * 7 + 3);
  sha("pattern", msg, 1000);

  fill(key, 0x0b, 20); copy(msg, "Hi There", 8); hmac("rfc1", 20, 8);
  copy(key, "Jefe", 4); copy(msg, "what do ya want for nothing?", 28); hmac("rfc2", 4, 28);
  fill(key, 0xaa, 20); fill(msg, 0xdd, 50); hmac("rfc3", 20, 50);
  for(k = 0; k < 25; k++) key[k] = (uint8_t)(k + 1);
  fill(msg, 0xcd, 50); hmac("rfc4", 25, 50);
  fill(key, 0x0c, 20); copy(msg, "Test With Truncation", 20); hmac("rfc5", 20, 20);
  for(k = 0; k < 32; k++) key[k] = (uint8_t)k;
  for(k = 0; k < 1000; k++) msg[k] = (uint8_t)(k * 7 + 3);
  hmac("pattern", 32, 1000);

  log_line("sha million start");         /* 15 625 blocks: the time per block */
  sha_init(); sha_total = 1000000UL;
  for(k = 0; k < 1000; k++) for(m = 0; m < 1000; m++) sha_byte('a');
  sha_digest(out);
  print("sha", "million");
  log_line("sha done");
  for(;;) clrwdt();
}
