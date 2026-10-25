/**
 * @file console.c
 * @brief The two symbols the firmware's debug plumbing needs on a host.
 *
 * Everything the firmware prints goes through putst_uart() — stubbed in
 * stubs/hal/uart.c — into the buffer declared here. The buffer is the length
 * debug.h declares and not one byte less: src/usb/rt2501usb.c and
 * src/net/ieee80211.c sprintf whole frame dumps into it, and the 50-byte
 * array testvm.c used to define was overrun long before anything crashed.
 */
#include <stdint.h>
#include <stdio.h>

#include "common.h"
#include "utils/debug.h"

/* The virtual clock, which seeds nab_rand below. sim.c writes it, exactly as
   the hardware's timer interrupt does. */
extern uint32_t counter_timer;

char dbg_buffer[DBG_BUFFER_LENGTH];

void consolestr(const void *s)
{
  fputs((const char *)s, stdout);
}

/**
 * @brief The frame dump the firmware's debug paths call.
 *
 * src/net/eapol.c dumps every key frame through this, which is exactly what
 * one wants to read when a handshake goes wrong. Same layout as the one in
 * main.c: offset, sixteen bytes, then the printable characters.
 */
void dump(uint8_t *src, int32_t len)
{
  int32_t i, j;

  for(i = 0; i < len; i += 16) {
    printf("  %04x ", (unsigned)i);
    for(j = 0; j < 16; j++) {
      if(i + j < len) printf("%02x ", src[i + j]);
      else            printf("   ");
    }
    for(j = 0; j < 16; j++) {
      if(i + j >= len) break;
      putchar((src[i + j] >= 32 && src[i + j] < 127) ? src[i + j] : '.');
    }
    putchar('\n');
  }
}

/**
 * @brief The firmware's own non-allocating PRNG, for the host build.
 *
 * The no_libc_malloc step puts nab_rand() in src/utils/sys.c, which is
 * newlib's hook file and is not part of this build -- the host has its own
 * libc. Without this, eapol.c's SNonce and ieee80211.c's IV fields link
 * against nothing.
 *
 * Unreferenced as things stand, because that step is parked: it builds a
 * brick on target, for a reason that has nothing to do with rand() and
 * everything to do with link order. See patch_no_libc_malloc's docstring in
 * apply-mods.py. This stays because the day the step is revived, the
 * emulator will need it again and the reason will be easy to lose.
 *
 * Deliberately the same arithmetic as the firmware's, not a call to the
 * host's rand(): the point of running the real stack here is that the bytes
 * on the wire are the bytes the rabbit would send, and a nonce is one of
 * them. Seeded from the virtual clock, so a scenario replays identically.
 */
uint32_t nab_rand(void)
{
  static uint32_t s;

  if (s == 0)
    s = counter_timer | 1u;
  s ^= s << 13;
  s ^= s >> 17;
  s ^= s << 5;
  return s;
}
