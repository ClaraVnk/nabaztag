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
