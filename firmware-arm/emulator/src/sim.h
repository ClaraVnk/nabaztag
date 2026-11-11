/**
 * @file sim.h
 * @brief Virtual clock and scenario for the rabbit emulator.
 *
 * Time here is not wall-clock time. The firmware's only clock is
 * `counter_timer`, a 1 ms counter the system timer interrupt increments, read
 * through sysTimems(); the emulator writes it itself. So IEEE80211_RUN_TIMEOUT
 * (600 ticks of 100 ms, ten minutes on the hardware) passes in a few
 * milliseconds of host time, and a run is reproducible: the same scenario
 * yields the same transcript, because nothing depends on how fast the host is.
 */
#ifndef _SIM_H_
#define _SIM_H_

#include <stdint.h>

#define SIM_MAX_APS      8
#define SIM_MAX_EVENTS   64
#define SIM_SSID_MAXLEN  32

enum {
  SIM_SEC_OPEN,
  SIM_SEC_WEP,
  SIM_SEC_WPA_TKIP,     /* WPA1, vendor IE, TKIP            */
  SIM_SEC_WPA2_TKIP,    /* WPA2, RSN IE, TKIP pairwise      */
  SIM_SEC_WPA2_CCMP,    /* WPA2, RSN IE, CCMP pairwise      */
  SIM_SEC_WPA2_MIXED,   /* RSN IE advertising TKIP AND CCMP */
  SIM_SEC_WPA3_SAE,     /* RSN IE with AKM 8 (SAE) only     */
  SIM_SEC_WPA2_WPA3,    /* transition mode: PSK + SAE       */
};

struct sim_ap {
  uint8_t  bssid[6];
  char     ssid[SIM_SSID_MAXLEN + 1];
  uint8_t  channel;
  int16_t  rssi;
  int      security;
  int      up;              /* 0 = powered off / out of range            */
  int      pmf;             /* 1 = refuse unprotected deauth (802.11w)   */
  int      sae_token;       /* 1 = demand an anti-clogging token first   */
  int      sae_asked;       /* whether this AP has asked yet             */
  int      sae_h2e_only;    /* 1 = H2E commits only (hostapd sae_pwe=1)  */
  int      sae_h2e;         /* 1 = H2E offered, both taken (sae_pwe=2)   */
  int      sae_used_h2e;    /* the last SAE commit was hash-to-element   */
  uint32_t assoc_id;
  int      associated;      /* the rabbit completed assoc with this one  */
  int      authenticated;   /* open-system auth done                     */
  int      mute;            /* 1 = drops data from the station, says nothing */
  int      sae_auth;        /* ...and it was SAE, so the AKM-defined key
                               schedule applies to the handshake         */
  uint8_t  sta_rsn[40];     /* the RSN IE of the association request,    */
  uint8_t  sta_rsn_len;     /* which message 2 must repeat byte for byte */
  uint8_t  sta_akm;         /* its AKM suite type                        */
  uint8_t  sta_rsnxe[8];    /* its RSN Extension element, if any, which  */
  uint8_t  sta_rsnxe_len;   /* message 2 must repeat as well             */
};

enum {
  SIM_EV_NONE,
  SIM_EV_DEAUTH,        /* AP deauthenticates the rabbit        */
  SIM_EV_DEAUTH_PROT,   /* ... with a valid 802.11w MMIE        */
  SIM_EV_DISASSOC,      /* AP disassociates the rabbit          */
  SIM_EV_AP_DOWN,       /* AP stops answering entirely          */
  SIM_EV_AP_UP,
  SIM_EV_AP_SECURITY,   /* AP is reconfigured: new security, restart */
  SIM_EV_AP_RESTART,    /* AP restarts: broadcast deauth, forgets all */
  SIM_EV_AP_FORGET,     /* AP forgets the station and says nothing   */
  SIM_EV_SERVER_VANISH, /* the warren's sockets die with no FIN      */
  SIM_EV_EXPECT_REASSOC,/* assert the rabbit has reached RUN >= n times */
  SIM_EV_EXPECT_XMPP,   /* assert >= n connections opened to port 5222 */
  SIM_EV_DROP_EAPOL,    /* swallow the next N EAPOL frames      */
  SIM_EV_REAUTH,        /* AP re-sends an authentication reply  */
  SIM_EV_REKEY,         /* AP rotates the group key             */
  SIM_EV_PTK_REKEY,     /* AP restarts the four-way handshake   */
  SIM_EV_SERVER_DROP,   /* the warren's sockets close           */
  SIM_EV_SERVER_CLOSE,  /* …after announcing </stream:stream>   */
  SIM_EV_ARP,           /* the gateway answers ARP, or stops    */
  SIM_EV_STOP,          /* end the run                          */
  SIM_EV_EXPECT_STATE,  /* assert ieee80211_state at this tick   */
  SIM_EV_REFUSE_STATE,  /* assert it is NOT that state            */
  SIM_EV_UPLOAD,        /* the phone POSTs --upload to the upgrade page */
  SIM_EV_EXPECT_FLASH,  /* assert flashFirmware was reached n times     */
  SIM_EV_EXPECT_NO_RESET, /* assert the firmware never asked for a reset */
  SIM_EV_RFID,          /* a tag is presented to the reader, or taken away */
  SIM_EV_INJECT_PLAIN,  /* an attacker's plaintext data frame     */
  SIM_EV_INJECT_PROBE,  /* a probe request with an oversized SSID (config mode) */
  SIM_EV_EXPECT_PLAIN_DROPPED, /* assert >= n plaintext frames refused */
  SIM_EV_FORGED_MSG1,   /* a message 1 the AP did not send           */
  SIM_EV_RESEND_MSG3,   /* the AP retransmits message 3              */
  SIM_EV_EAPOL_OVERSIZED, /* a group message declaring a 64 KB body  */
  SIM_EV_EXPECT_PTK_INSTALLS,   /* assert exactly n pairwise key installs */
  SIM_EV_EXPECT_GROUP_INSTALLS, /* assert exactly n group key installs    */
  SIM_EV_EXPECT_PTK_SYNCED,     /* assert the station's PTK is the AP's   */
  SIM_EV_EXPECT_EAPOL_BADLEN,   /* assert >= n EAPOL frames refused for length */
  SIM_EV_EXPECT_TKIP_REFUSED,   /* assert >= n networks refused for pairwise TKIP */
  SIM_EV_EXPECT_SAE_REFUSED,    /* assert >= n PSK attempts refused on a SAE network */
  SIM_EV_EXPECT_SAE,    /* assert which SAE derivation the AP saw */
};

struct sim_event {
  uint32_t at_ms;
  int      kind;
  int      ap;          /* index into the AP table, -1 = all     */
  int      arg;         /* reason code, count, expected state... */
  int      fired;
  /* Set by the "fetch <ms>" form: at_ms is a delay, counted from the moment
   * the rabbit starts downloading a resource, and the event is not due until
   * sim_note_fetch() has turned it into a time. Guessing a wall-clock moment
   * for "while the sound is still coming" is how a scenario passes because
   * the thing it meant to interrupt had already finished. */
  int      rel_fetch;
  /* `rfid <16 hex>`: the tag's eight bytes; arg says whether one is there. */
  uint8_t  tag[8];
};

/* The scenario, parsed once from a file. */
struct sim_scenario {
  struct sim_ap    aps[SIM_MAX_APS];
  int              nb_aps;
  struct sim_event events[SIM_MAX_EVENTS];
  int              nb_events;
  char             psk[64];
  char             conf[512];   /* a config page, as mkconf.py writes one */
  uint32_t         stop_ms;
  uint32_t         tick_ms;      /* how much virtual time one loop costs */
  int              realtime;     /* pace the loop to the wall clock */
  int              run_entries;  /* how many times ieee80211_state reached RUN */
  int              config_mode;  /* the head button is held at power-on */
  uint32_t         segment;      /* the phone's TCP segment size, 0 = default */
  int              resets;       /* how often the firmware asked for a reset */
  int              last_reset_reason;
};

extern struct sim_scenario sim;

int  sim_load(const char *path);
void sim_default(void);
void sim_advance(uint32_t ms);
void sim_pace(void);
uint32_t sim_now(void);
void sim_run_events(void);
void sim_note_fetch(void);
void sim_log(const char *fmt, ...);
int  sim_failures(void);
void sim_note_failure(const char *why);
/* The 2026-11-08 audit's events -- forged message 1, retransmitted message
   3, oversized EAPOL, and the key-install assertions -- are the same for
   ./rabbit and ./rabbit-vm, so one handler serves both. 1 when e was one. */
int sim_audit_event(struct sim_event *e);
/* Fill a buffer from an xorshift seeded with `seed`; zeros when seed is 0.
 * Stands in for what external RAM holds at power-up. */
void sim_fill_noise(uint8_t *buf, uint32_t len, uint32_t seed);
const char *sim_security_name(int security);
const char *sim_state_name(int state);

#endif /* _SIM_H_ */
