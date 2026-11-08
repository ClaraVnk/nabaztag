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
  uint32_t ptk_installs;       /* pairwise keys handed to the radio (slot 0) */
  uint32_t group_installs;     /* group keys handed to the radio (slots 1-3) */
  uint32_t ptk_mismatch;       /* frames lost to a station key that is not the AP's */
  uint32_t forged_msg1;        /* message 1 frames the AP never sent (forged-msg1) */
  uint32_t msg3_resent;        /* message 3 retransmissions (resend-msg3) */
  uint32_t eapol_oversized;    /* EAPOL frames with a body_length past the frame */
};

extern struct vap_stats vap_stats;
/* 1 = the last SAE commit used hash-to-element, 2 = hunting and pecking. */
extern int vap_last_sae_mode;

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
/* A message 1 the AP did not send, delivered as if it had (the attacker is
   past rx_protected, or the build predates it): a fresh ANonce, a high
   replay counter, and no message 3 ever. The AP keeps its live PTK. */
void vap_forged_msg1(int ap);
/* The AP retransmits message 3, as it does when message 4 was lost. */
void vap_resend_msg3(int ap);
/* A group key message whose body_length says 65535 in a 150-byte frame. */
void vap_eapol_oversized(int ap);
/* 1 when the pairwise key in the station's radio is the one the AP it is
   associated with transmits under; 0 otherwise, or with no association. */
int vap_sta_ptk_synced(void);
/* Deterministic -1..+1 dB jitter on every delivered frame's RSSI; 0 = none. */
void vap_set_rssi_noise(uint32_t seed);

#endif /* _VAP_H_ */
