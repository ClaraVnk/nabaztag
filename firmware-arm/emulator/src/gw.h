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

/** @brief An Ethernet payload from the rabbit: LLC/SNAP, then the packet. */
void gw_from_rabbit(const uint8_t *llc_frame, uint32_t length);

/** @brief Called once per main-loop pass: moves socket traffic to the rabbit. */
void gw_poll(void);

void gw_server_drop(void);
void gw_server_close(int graceful);
void gw_close(void);

#endif /* _GW_H_ */
