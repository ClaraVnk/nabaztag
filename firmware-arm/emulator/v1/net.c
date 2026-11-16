/**
 * @file net.c
 * @brief Between the Prism card and gw.c, the tag emulator's router.
 *
 * The card hands its host 802.3 frames whose payload is LLC/SNAP, which is
 * exactly what gw.c eats and produces, so this file only unwraps the Prism
 * TX descriptor (data length at 44, 802.3 header at 46, payload at 60) and
 * builds the RX one.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "prism.h"
#include "../src/gw.h"
#include "vap.h"
#include "sim.h"

int net_trace;

void sim_note_fetch(void) {}

void sim_log(const char *fmt, ...)
{
  char line[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(line, sizeof(line), fmt, ap);
  va_end(ap);
  board_log("%s", line);
}

void vap_to_station(const uint8_t *source_mac, const uint8_t *body, uint32_t length)
{
  prism_receive(prism_mac, source_mac, body, length);
}

void prism_transmitted(const uint8_t *buf, unsigned size)
{
  unsigned len = (unsigned)(buf[44] | (buf[45] << 8));
  if(len > size - 60) len = size - 60;
  if(net_trace) {
    char hex[3 * 64 + 1];
    unsigned n = len < 64 ? len : 64;
    for(unsigned i = 0; i < n; i++) sprintf(hex + 3 * i, "%02x ", buf[60 + i]);
    board_log("card: transmit %u bytes to %02x:%02x:%02x:%02x:%02x:%02x: %s", len,
              buf[46], buf[47], buf[48], buf[49], buf[50], buf[51], hex);
  }
  gw_from_rabbit(buf + 60, len);
}
