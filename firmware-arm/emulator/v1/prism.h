/**
 * @file prism.h
 * @brief The Prism 2 card's side towards the network (net.c).
 */
#ifndef _PRISM_H_
#define _PRISM_H_

#include <stdint.h>

extern uint8_t prism_mac[6];
extern char prism_ssid[33];

int  prism_link_up(void);
void prism_link_down(uint64_t at_ms, uint64_t seconds);
int  prism_link_is_down(void);
void prism_receive(const uint8_t *dst, const uint8_t *src, const uint8_t *llc, unsigned len);
void prism_rx_queue(uint16_t fid);
void prism_rx_pump(void);
void prism_scan_results(void);
/* Called with a TX buffer as the firmware left it. */
void prism_transmitted(const uint8_t *buf, unsigned size);

#endif /* _PRISM_H_ */
