/**
 * @file sim.c
 * @brief Virtual clock and scenario for the rabbit emulator.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sim.h"

struct sim_scenario sim;

static uint32_t sim_clock_ms;
static int sim_fail_count;

/* The firmware's own millisecond counter, which sysTimems() returns. On the
 * hardware a timer interrupt increments it; here the main loop does. */
extern uint32_t counter_timer, counter_timer_s;

uint32_t sim_now(void)
{
  return sim_clock_ms;
}

void sim_advance(uint32_t ms)
{
  sim_clock_ms += ms;
  counter_timer = sim_clock_ms;
  counter_timer_s = sim_clock_ms / 1000;
}

void sim_log(const char *fmt, ...)
{
  va_list ap;
  fprintf(stderr, "[%7u ms] ", sim_clock_ms);
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);
  fputc('\n', stderr);
}

int sim_failures(void)
{
  return sim_fail_count;
}

void sim_note_failure(const char *why)
{
  sim_fail_count++;
  sim_log("FAIL %s", why);
}

const char *sim_security_name(int security)
{
  switch(security) {
    case SIM_SEC_OPEN:       return "open";
    case SIM_SEC_WEP:        return "wep";
    case SIM_SEC_WPA_TKIP:   return "wpa-tkip";
    case SIM_SEC_WPA2_TKIP:  return "wpa2-tkip";
    case SIM_SEC_WPA2_CCMP:  return "wpa2-ccmp";
    case SIM_SEC_WPA2_MIXED: return "wpa2-mixed";
    case SIM_SEC_WPA3_SAE:   return "wpa3-sae";
    case SIM_SEC_WPA2_WPA3:  return "wpa2+wpa3";
    default:                 return "?";
  }
}

const char *sim_state_name(int state)
{
  /* Mirrors the enum in ieee80211.h; kept here so sim.c needs no firmware
   * header and can be read on its own. */
  switch(state) {
    case 0: return "IDLE";
    case 1: return "SCAN";
    case 2: return "AUTH";
    case 3: return "ASSOC";
    case 4: return "EAPOL";
    case 5: return "RUN";
    default: return "?";
  }
}

/* --- parsing ---------------------------------------------------------- */

static int parse_security(const char *s)
{
  if(!strcmp(s, "open"))       return SIM_SEC_OPEN;
  if(!strcmp(s, "wep"))        return SIM_SEC_WEP;
  if(!strcmp(s, "wpa-tkip"))   return SIM_SEC_WPA_TKIP;
  if(!strcmp(s, "wpa2-tkip"))  return SIM_SEC_WPA2_TKIP;
  if(!strcmp(s, "wpa2-ccmp"))  return SIM_SEC_WPA2_CCMP;
  if(!strcmp(s, "wpa2-mixed")) return SIM_SEC_WPA2_MIXED;
  if(!strcmp(s, "wpa3-sae"))   return SIM_SEC_WPA3_SAE;
  if(!strcmp(s, "wpa2+wpa3"))  return SIM_SEC_WPA2_WPA3;
  return -1;
}

static int parse_mac(const char *s, uint8_t *out)
{
  unsigned v[6];
  if(sscanf(s, "%x:%x:%x:%x:%x:%x", &v[0],&v[1],&v[2],&v[3],&v[4],&v[5]) != 6)
    return 0;
  for(int i=0;i<6;i++) out[i] = (uint8_t)v[i];
  return 1;
}

static int parse_state(const char *s)
{
  for(int i=0;i<=5;i++)
    if(!strcasecmp(s, sim_state_name(i))) return i;
  return -1;
}

static int ap_by_bssid(const char *s)
{
  uint8_t mac[6];
  if(!parse_mac(s, mac)) return -1;
  for(int i=0;i<sim.nb_aps;i++)
    if(memcmp(sim.aps[i].bssid, mac, 6) == 0) return i;
  return -1;
}

void sim_default(void)
{
  memset(&sim, 0, sizeof(sim));
  sim.tick_ms = 50;          /* one main-loop pass of virtual time */
  sim.stop_ms = 120000;
  strcpy(sim.psk, "nabaztag");
  sim.nb_aps = 1;
  memcpy(sim.aps[0].bssid, "\x02\x00\x5e\x10\x00\x01", 6);
  strcpy(sim.aps[0].ssid, "Terrier");
  sim.aps[0].channel  = 6;
  sim.aps[0].rssi     = -55;
  sim.aps[0].security = SIM_SEC_OPEN;
  sim.aps[0].up       = 1;
}

/**
 * @brief Read a scenario file.
 *
 * Directives, one per line, '#' starts a comment:
 *   psk <passphrase>
 *   tick <ms>                      virtual time per main-loop pass
 *   stop <ms>
 *   ap <bssid> <ssid> <channel> <security> [<rssi>] [pmf]
 *   at <ms> deauth [<bssid>] [reason <n>]
 *   at <ms> disassoc [<bssid>] [reason <n>]
 *   at <ms> down <bssid>
 *   at <ms> up <bssid>
 *   at <ms> arp on|off            the gateway answers ARP, or stops
 *   at <ms> server-drop           the warren's TCP connections close
 *   at <ms> rekey [<bssid>]       the AP rotates the group key
 *   at <ms> ptk-rekey [<bssid>]   the AP restarts the four-way handshake
 *   at <ms> reauth [<bssid>]      the AP repeats its authentication reply
 *   at <ms> drop-eapol <count>
 *   at <ms> expect <state>         assert ieee80211_state, else exit non-zero
 */
int sim_load(const char *path)
{
  FILE *f;
  char line[512];
  int lineno = 0;

  sim_default();
  sim.nb_aps = 0;

  f = fopen(path, "r");
  if(f == NULL) {
    fprintf(stderr, "%s: cannot open\n", path);
    return -1;
  }

  while(fgets(line, sizeof(line), f) != NULL) {
    char *hash, *tok[12];
    int n = 0;
    lineno++;
    hash = strchr(line, '#');
    if(hash) *hash = 0;
    for(char *p = strtok(line, " \t\r\n"); p && n < 12; p = strtok(NULL, " \t\r\n"))
      tok[n++] = p;
    if(n == 0) continue;

    if(!strcmp(tok[0], "psk") && n >= 2) {
      snprintf(sim.psk, sizeof(sim.psk), "%s", tok[1]);
    } else if(!strcmp(tok[0], "tick") && n >= 2) {
      sim.tick_ms = (uint32_t)strtoul(tok[1], NULL, 10);
    } else if(!strcmp(tok[0], "stop") && n >= 2) {
      sim.stop_ms = (uint32_t)strtoul(tok[1], NULL, 10);
    } else if(!strcmp(tok[0], "ap") && n >= 5) {
      struct sim_ap *a;
      int sec = parse_security(tok[4]);
      if(sim.nb_aps >= SIM_MAX_APS) {
        fprintf(stderr, "%s:%d: too many APs (max %d)\n", path, lineno, SIM_MAX_APS);
        fclose(f); return -1;
      }
      if(sec < 0) {
        fprintf(stderr, "%s:%d: unknown security \"%s\"\n", path, lineno, tok[4]);
        fclose(f); return -1;
      }
      a = &sim.aps[sim.nb_aps];
      memset(a, 0, sizeof(*a));
      if(!parse_mac(tok[1], a->bssid)) {
        fprintf(stderr, "%s:%d: \"%s\" is not a BSSID\n", path, lineno, tok[1]);
        fclose(f); return -1;
      }
      snprintf(a->ssid, sizeof(a->ssid), "%s", tok[2]);
      a->channel  = (uint8_t)strtoul(tok[3], NULL, 10);
      a->security = sec;
      a->rssi     = (n >= 6) ? (int16_t)strtol(tok[5], NULL, 10) : -55;
      a->up       = 1;
      for(int i = 5; i < n; i++)
        if(!strcmp(tok[i], "pmf")) a->pmf = 1;
      sim.nb_aps++;
    } else if(!strcmp(tok[0], "at") && n >= 3) {
      struct sim_event *e;
      if(sim.nb_events >= SIM_MAX_EVENTS) {
        fprintf(stderr, "%s:%d: too many events (max %d)\n", path, lineno, SIM_MAX_EVENTS);
        fclose(f); return -1;
      }
      e = &sim.events[sim.nb_events];
      memset(e, 0, sizeof(*e));
      e->at_ms = (uint32_t)strtoul(tok[1], NULL, 10);
      e->ap = -1;
      e->arg = 0;
      if(!strcmp(tok[2], "deauth") || !strcmp(tok[2], "disassoc")) {
        e->kind = !strcmp(tok[2], "deauth") ? SIM_EV_DEAUTH : SIM_EV_DISASSOC;
        e->arg = 7;  /* IEEE80211_REASON_NOT_ASSOCED, what an AP sends on a reboot */
        for(int i = 3; i < n; i++) {
          if(!strcmp(tok[i], "reason") && i + 1 < n) e->arg = (int)strtol(tok[++i], NULL, 10);
          else if(strchr(tok[i], ':')) e->ap = ap_by_bssid(tok[i]);
        }
      } else if(!strcmp(tok[2], "down") || !strcmp(tok[2], "up")) {
        e->kind = !strcmp(tok[2], "down") ? SIM_EV_AP_DOWN : SIM_EV_AP_UP;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
        if(e->ap < 0 && n >= 4) {
          fprintf(stderr, "%s:%d: no AP with BSSID %s\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "arp") && n >= 4) {
        e->kind = SIM_EV_ARP;
        e->arg = !strcmp(tok[3], "on");
      } else if(!strcmp(tok[2], "server-drop")) {
        e->kind = SIM_EV_SERVER_DROP;
      } else if(!strcmp(tok[2], "ptk-rekey")) {
        e->kind = SIM_EV_PTK_REKEY;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "rekey")) {
        e->kind = SIM_EV_REKEY;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "reauth")) {
        e->kind = SIM_EV_REAUTH;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "drop-eapol")) {
        e->kind = SIM_EV_DROP_EAPOL;
        e->arg = (n >= 4) ? (int)strtol(tok[3], NULL, 10) : 1;
      } else if(!strcmp(tok[2], "expect") && n >= 4) {
        e->kind = SIM_EV_EXPECT_STATE;
        e->arg = parse_state(tok[3]);
        if(e->arg < 0) {
          fprintf(stderr, "%s:%d: unknown state \"%s\"\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else {
        fprintf(stderr, "%s:%d: unknown event \"%s\"\n", path, lineno, tok[2]);
        fclose(f); return -1;
      }
      sim.nb_events++;
    } else {
      fprintf(stderr, "%s:%d: unknown directive \"%s\"\n", path, lineno, tok[0]);
      fclose(f); return -1;
    }
  }
  fclose(f);

  if(sim.nb_aps == 0) {
    fprintf(stderr, "%s: the scenario declares no AP\n", path);
    return -1;
  }
  return 0;
}
