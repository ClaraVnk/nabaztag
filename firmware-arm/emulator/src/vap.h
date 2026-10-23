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
  uint32_t assoc_requests;
  uint32_t assoc_responses;
  uint32_t deauths_sent;
  uint32_t data_from_sta;
  uint32_t data_to_sta;
  uint32_t eapol_from_sta;
  uint32_t eapol_to_sta;
  uint32_t mics_verified;
  uint32_t mics_failed;
  uint32_t handshakes_completed;
  uint32_t rekeys_sent;
  uint32_t rekeys_acked;
  uint32_t ptk_rekeys_sent;
  uint32_t ptk_rekeys_answered;
  uint32_t keys_installed;
  uint32_t channel_switches;
};

extern struct vap_stats vap_stats;

void vap_init(void);
void vap_set_pmk(const uint8_t *pmk);
void vap_poll(void);
void vap_to_station(const uint8_t *source_mac, const uint8_t *body,
                    uint32_t body_length);
void vap_deauth(int ap, int disassoc, uint16_t reason);
void vap_reauth(int ap);
void vap_rekey(int ap);
void vap_ptk_rekey(int ap);
void vap_rekey_ack(int on);
void vap_set_up(int ap, int up);
void vap_drop_eapol(int count);

#endif /* _VAP_H_ */
