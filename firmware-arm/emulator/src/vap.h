/**
 * @file vap.h
 * @brief The virtual access point's own entry points.
 */
#ifndef _VAP_H_
#define _VAP_H_

#include <stdint.h>

struct vap_stats {
  uint32_t tx_frames;
  uint32_t tx_unheard;
  uint32_t rx_frames;
  uint32_t rx_overruns;
  uint32_t beacons_sent;
  uint32_t probe_responses;
  uint32_t auth_responses;
  uint32_t assoc_responses;
  uint32_t deauths_sent;
  uint32_t data_from_sta;
  uint32_t eapol_from_sta;
  uint32_t keys_installed;
  uint32_t channel_switches;
  uint32_t handshakes_skipped;
};

extern struct vap_stats vap_stats;

void vap_init(void);
void vap_poll(void);
void vap_deauth(int ap, int disassoc, uint16_t reason);
void vap_set_up(int ap, int up);
void vap_drop_eapol(int count);

#endif /* _VAP_H_ */
