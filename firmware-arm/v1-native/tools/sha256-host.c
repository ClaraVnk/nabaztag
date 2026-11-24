/* The loader's HMAC-SHA-256 compiled on the host, against Python's: the same
 * source as the PIC's (src/loader/sha256.c), SDCC's keywords emptied.
 *   cc -I tools -I src -o /tmp/sha tools/sha256-host.c && /tmp/sha
 * prints HMAC(key 00..1f, "abc") and HMAC over a 1000-byte pattern. */
#include <stdio.h>
#include <stdint.h>
#define __data
#define __code
#define __wparam
#define clrwdt()
#include "../src/loader/sha256.c"
int main(void)
{
  uint8_t key[32], out[32], msg[1000];
  int i;
  for(i = 0; i < 32; i++) key[i] = (uint8_t)i;
  for(i = 0; i < 1000; i++) msg[i] = (uint8_t)(i * 7 + 3);
  hmac_init(key, 32); hmac_update((__data const uint8_t *)"abc", 3); hmac_final(out);
  for(i = 0; i < 32; i++) printf("%02x", out[i]); printf("\n");
  hmac_init(key, 32); hmac_update(msg, 1000); hmac_final(out);
  for(i = 0; i < 32; i++) printf("%02x", out[i]); printf("\n");
  return 0;
}
