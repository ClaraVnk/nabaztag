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

/* A real router drops a frame whose IPv4 header or UDP/TCP checksum does
 * not hold, and says nothing; this gateway took anything. Lazare's native
 * firmware sent DHCP discovers the access point passed on and nothing ever
 * answered (UniFi: 42 packets up, 0 down, 2026-11-25). The frame here is
 * SNAP (AA AA 03, 3 bytes of OUI, the type) then the IP packet. */
static uint32_t sum16(const uint8_t *p, unsigned n, uint32_t s)
{
  for(unsigned i = 0; i + 1 < n; i += 2) s += (uint32_t)(p[i] << 8 | p[i + 1]);
  if(n & 1) s += (uint32_t)(p[n - 1] << 8);
  return s;
}

static uint16_t fold(uint32_t s)
{
  while(s >> 16) s = (s & 0xFFFF) + (s >> 16);
  return (uint16_t)s;
}

static int sums_hold(const uint8_t *f, unsigned len)
{
  if(len < 8 + 20 || f[6] != 0x08 || f[7] != 0x00) return 1;     /* not IPv4: not ours to judge */
  const uint8_t *ip = f + 8;
  unsigned ihl = (ip[0] & 15) * 4, total = (unsigned)(ip[2] << 8 | ip[3]);
  if(ihl < 20 || total > len - 8) { board_log("gw: dropped, an IPv4 length that does not fit"); return 0; }
  if(fold(sum16(ip, ihl, 0)) != 0xFFFF) { board_log("gw: dropped, bad IPv4 header checksum"); return 0; }
  uint8_t proto = ip[9];
  unsigned tl = total - ihl;
  const uint8_t *tp = ip + ihl;
  if(proto != 17 && proto != 6) return 1;
  if(proto == 17 && tl >= 8 && !(tp[6] | tp[7])) return 1;          /* UDP without a checksum */
  uint32_t s = sum16(ip + 12, 8, 0) + proto + tl;
  if(fold(sum16(tp, tl, s)) != 0xFFFF) {
    board_log("gw: dropped, bad %s checksum", proto == 17 ? "UDP" : "TCP");
    return 0;
  }
  return 1;
}

void prism_transmitted(const uint8_t *buf, unsigned size)
{
  unsigned len = (unsigned)(buf[44] | (buf[45] << 8));
  if(len > size - 60) len = size - 60;
  if(net_trace) {
    char hex[3 * 400 + 1];                /* a DHCP discover whole (2026-11-25) */
    unsigned n = len < 400 ? len : 400;
    for(unsigned i = 0; i < n; i++) sprintf(hex + 3 * i, "%02x ", buf[60 + i]);
    board_log("card: transmit %u bytes to %02x:%02x:%02x:%02x:%02x:%02x: %s", len,
              buf[46], buf[47], buf[48], buf[49], buf[50], buf[51], hex);
    /* The descriptor too (2026-11-25): our firmware's frames left Lazare
     * and nothing ever came back; Violet's and ours side by side. */
    char d[3 * 60 + 1];
    for(unsigned i = 0; i < 60; i++) sprintf(d + 3 * i, "%02x ", buf[i]);
    board_log("card: tx descriptor: %s", d);
  }
  if(prism_link_is_down()) {
    if(net_trace) board_log("card: %u bytes transmitted into the void, the link is down", len);
    return;
  }
  if(!sums_hold(buf + 60, len)) return;
  gw_from_rabbit(buf + 60, len);
}
