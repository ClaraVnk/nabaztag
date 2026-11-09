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
  uint32_t nulls_from_sta;     /* null-data frames: the rabbit probing its AP */
  uint32_t muted_from_sta;     /* frames a muted AP swallowed */
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
  uint32_t plain_injected;     /* attacker frames sent in clear (inject-plaintext) */
  uint32_t rx_cipher_err;      /* protected frames the radio could not decrypt */
};

extern struct vap_stats vap_stats;

void vap_init(void);
void vap_set_pmk(const uint8_t *pmk);
void vap_poll(void);
void vap_to_station(const uint8_t *source_mac, const uint8_t *body,
                    uint32_t body_length);
void vap_deauth(int ap, int disassoc, uint16_t reason);
/* The AP restarts (one broadcast deauth, signed under 802.11w, then it
 * knows nobody) or silently forgets the station. See the definitions. */
void vap_restart(int ap);
void vap_forget(int ap);
/* ...and from then on drops its data frames without a word, until it
 * authenticates again. The AP that never says reason 7. */
void vap_mute(int ap);
void vap_deauth_protected(int ap, int disassoc, uint16_t reason);
void vap_reauth(int ap);
void vap_rekey(int ap);
void vap_ptk_rekey(int ap);
void vap_rekey_ack(int on);
void vap_set_up(int ap, int up);
void vap_drop_eapol(int count);
/* A frame from the phone that joined a rabbit in config mode: see phone.c. */
void vap_inject(const uint8_t *frame, uint32_t length);
/* An attacker's plaintext data frame from the AP's address; `eapol` makes
 * it an EAPOL-Key message 1. */
void vap_inject_plain(int ap, int eapol);
/* Deterministic -1..+1 dB jitter on every delivered frame's RSSI; 0 = none. */
void vap_set_rssi_noise(uint32_t seed);

#endif /* _VAP_H_ */
