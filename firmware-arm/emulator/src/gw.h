/**
 * @file gw.h
 * @brief The network behind the virtual access point.
 */
#ifndef _GW_H_
#define _GW_H_

#include <stdint.h>

struct gw_stats {
  uint32_t arp_requests;
  uint32_t arp_unanswered;
  uint32_t dhcp_offers;
  uint32_t dns_queries;
  uint32_t pings;
  uint32_t tcp_opened;
  uint32_t xmpp_opened;       /* of which, to port 5222 */
  uint32_t tcp_refused;
  uint32_t tcp_closed;
  uint32_t bytes_to_rabbit;
  uint32_t bytes_from_rabbit;
  uint32_t udp_datagrams;
  uint32_t unknown_ethertype;
  uint32_t unknown_protocol;
};

extern struct gw_stats gw_stats;

/**
 * @brief Configure the gateway.
 *
 * @param warren_host  where to send every TCP connection the rabbit opens,
 *                     keeping the port it asked for. NULL leaves the rabbit
 *                     on an island: ARP, DHCP, DNS and ping work, and nothing
 *                     can be connected to.
 */
void gw_init(const char *warren_host);
void gw_set_subnet(const char *gateway_ip, const char *rabbit_ip);
void gw_set_trace(int on);
void gw_set_arp(int on);
/* The port the warren's XMPP listens on (5222 unless the scenario says
 * otherwise): what tells a session from a resource fetch, and what
 * expect-xmpp-opens counts. */
void gw_set_xmpp_port(uint16_t port);
/* Send the rabbit's connections to port 80 to this port of the warren. */
void gw_set_port80(uint16_t port);

/** @brief An Ethernet payload from the rabbit: LLC/SNAP, then the packet. */
void gw_from_rabbit(const uint8_t *llc_frame, uint32_t length);

/** @brief Called once per main-loop pass: moves socket traffic to the rabbit. */
void gw_poll(void);

void gw_dhcp_rogue(uint32_t rogue);
/* From now on the gateway answers every non-XMPP connection itself, with a
 * 200 and a body of `bytes` bytes; 0 hands them back to the warren. */
void gw_http_big_set(uint32_t bytes);
void gw_server_drop(void);
void gw_server_close(int graceful);
/* The warren's sockets die and the rabbit never sees the FIN: whatever it
 * sends on them from now on is swallowed. The half-open connection a rabbit
 * is left with when its server went away while it could not hear. */
void gw_server_vanish(void);
void gw_close(void);

#endif /* _GW_H_ */
