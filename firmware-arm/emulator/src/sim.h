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
  uint32_t assoc_id;
  int      associated;      /* the rabbit completed assoc with this one  */
  int      authenticated;   /* open-system auth done                     */
};

enum {
  SIM_EV_NONE,
  SIM_EV_DEAUTH,        /* AP deauthenticates the rabbit        */
  SIM_EV_DISASSOC,      /* AP disassociates the rabbit          */
  SIM_EV_AP_DOWN,       /* AP stops answering entirely          */
  SIM_EV_AP_UP,
  SIM_EV_DROP_EAPOL,    /* swallow the next N EAPOL frames      */
  SIM_EV_REAUTH,        /* AP re-sends an authentication reply  */
  SIM_EV_REKEY,         /* AP rotates the group key             */
  SIM_EV_PTK_REKEY,     /* AP restarts the four-way handshake   */
  SIM_EV_SERVER_DROP,   /* the warren's sockets close           */
  SIM_EV_SERVER_CLOSE,  /* …after announcing </stream:stream>   */
  SIM_EV_ARP,           /* the gateway answers ARP, or stops    */
  SIM_EV_STOP,          /* end the run                          */
  SIM_EV_EXPECT_STATE,  /* assert ieee80211_state at this tick   */
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
};

/* The scenario, parsed once from a file. */
struct sim_scenario {
  struct sim_ap    aps[SIM_MAX_APS];
  int              nb_aps;
  struct sim_event events[SIM_MAX_EVENTS];
  int              nb_events;
  char             psk[64];
  uint32_t         stop_ms;
  uint32_t         tick_ms;      /* how much virtual time one loop costs */
  int              realtime;     /* pace the loop to the wall clock */
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
const char *sim_security_name(int security);
const char *sim_state_name(int state);

#endif /* _SIM_H_ */
