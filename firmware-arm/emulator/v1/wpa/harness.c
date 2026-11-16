/*
 * Runs cost.hex on the v1 emulator's PIC18 core, checks its results against
 * the test vectors and turns its section marks into time on the real v1
 * (24 MHz crystal, HS: 6 MHz instruction clock). Prints a verdict per
 * section, then what a WPA2 v1 would spend on the handshake, on a frame and
 * on deriving the key from the passphrase.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pic18.h"

#define CYCLES_HZ 6000000.0

static uint64_t start[256], took[256];
static uint8_t got[256];
static unsigned ngot;

static uint8_t rd(struct pic18 *pic, uint16_t a) { return pic->ram[a]; }
static void wr(struct pic18 *pic, uint16_t a, uint8_t v)
{
  if(a == SFR_LATA) {
    if(v & 1) start[v] = pic->cycles;
    else took[v - 1] = pic->cycles - start[v - 1];
  } else if(a == SFR_LATB && ngot < sizeof got)
    got[ngot++] = v;
}

static const struct pic18_board board = { rd, wr };

static int check(const char *name, unsigned at, const char *hex)
{
  unsigned n = strlen(hex) / 2, i, b, ok = at + n <= ngot;
  for(i = 0; ok && i < n; i++) { sscanf(hex + 2*i, "%2x", &b); ok = got[at + i] == b; }
  printf("%-28s %s\n", name, ok ? "PASS" : "FAIL");
  return ok;
}

static double ms(uint64_t c) { return c * 1000.0 / CYCLES_HZ; }

int main(int argc, char **argv)
{
  static struct pic18 pic;
  uint64_t limit = 2000000000ull;
  int ok = 1;
  if(argc < 2) { fprintf(stderr, "usage: %s cost.hex\n", argv[0]); return 2; }
  pic18_reset(&pic);
  if(pic18_load_hex(&pic, argv[1]) <= 0) { fprintf(stderr, "cannot load %s\n", argv[1]); return 2; }
  pic.board = &board;
  pic18_reset(&pic);
  while(!pic.halted && pic.cycles < limit) pic18_step(&pic);
  if(!pic.halted || strcmp(pic.halt_reason, "SLEEP")) {
    printf("did not finish: %s\n", pic.halted ? pic.halt_reason : "cycle limit"); return 1;
  }

  ok &= check("AES-128 (FIPS-197 C.1)", 0, "69c4e0d86a7b0430d8cdb78070b4c55a");
  ok &= check("SHA-1 (\"abc\")", 16, "a9993e364706816aba3e25717850c26c9cd0d89d");
  ok &= check("HMAC-SHA-1 (RFC 2202 #2)", 36, "effcdf6ae5eb2fa2d27416d5f184df9c259a7c79");
  ok &= check("RC4 (\"Key\", \"Plaintext\")", 56, "bbf316e8d940af0ad3");

  printf("\nmeasured, instruction cycles at 6 MHz:\n");
  printf("  AES key expansion      %9llu  %8.2f ms\n", (unsigned long long)took[1], ms(took[1]));
  printf("  AES-128 block          %9llu  %8.2f ms\n", (unsigned long long)took[3], ms(took[3]));
  printf("  SHA-1 block            %9llu  %8.2f ms\n", (unsigned long long)took[5], ms(took[5]));
  printf("  HMAC-SHA-1 (short)     %9llu  %8.2f ms\n", (unsigned long long)took[7], ms(took[7]));
  printf("  RC4, 9 bytes + setup   %9llu  %8.2f ms\n", (unsigned long long)took[9], ms(took[9]));
  printf("  CCMP, one 16-B block   %9llu  %8.2f ms\n", (unsigned long long)took[11], ms(took[11]));

  /* What WPA2-PSK would ask of the v1, from the measured pieces. */
  double sha = took[5], ccmp = took[11], aes = took[3];
  printf("\nprojected (C, no BAP traffic counted):\n");
  printf("  PBKDF2 (4096 x 2 x 2 SHA-1 blocks) %8.1f s   once per passphrase\n",
         16384 * sha / CYCLES_HZ);
  printf("  4-way handshake (~40 SHA-1, ~20 AES) %6.2f s\n",
         (40 * sha + 20 * aes) / CYCLES_HZ);
  printf("  CCMP, 1500-byte frame (94 blocks)  %8.1f ms\n", ms(94 * ccmp));
  printf("  CCMP, poll request (~300 bytes)    %8.1f ms\n", ms(19 * ccmp));
  printf("  CCMP throughput                    %8.1f KB/s\n",
         16.0 / (ccmp / CYCLES_HZ) / 1024);
  return ok ? 0 : 1;
}
