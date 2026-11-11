/**
 * @file sim.c
 * @brief Virtual clock and scenario for the rabbit emulator.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "sim.h"
#include "vap.h"

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

/**
 * @brief Hold the loop back so virtual time tracks the wall clock.
 *
 * Off by default, and that is the right default: a run that finishes in
 * seconds is why ten minutes of IEEE80211_RUN_TIMEOUT costs nothing to test,
 * and why two runs of a scenario agree.
 *
 * It is needed as soon as something outside the process has its own clock.
 * The add-on schedules work with threading.Timer in real seconds -- the
 * connect jingle at +3.5 s, the mic clear at +1.5 s -- while the rabbit lives
 * in virtual time running several times faster. So the jingle lands at an
 * unpredictable point in the rabbit's life, or after the run has ended, and a
 * test that needs the server to act at a particular moment cannot be written.
 * With this on, the two clocks are the same clock.
 */
void sim_pace(void)
{
  static struct timespec started;
  static int running;
  struct timespec now;
  long long elapsed_us, target_us, wait_us;

  if(!sim.realtime) return;
  if(!running) {
    clock_gettime(CLOCK_MONOTONIC, &started);
    running = 1;
    return;
  }
  clock_gettime(CLOCK_MONOTONIC, &now);
  elapsed_us = (long long)(now.tv_sec - started.tv_sec) * 1000000
             + (now.tv_nsec - started.tv_nsec) / 1000;
  target_us = (long long)sim_now() * 1000;
  wait_us = target_us - elapsed_us;
  if(wait_us > 0) {
    struct timespec rq;
    /* Never sleep more than one tick: a scenario that jumps the clock should
     * not make the run hang. */
    if(wait_us > (long long)sim.tick_ms * 1000)
      wait_us = (long long)sim.tick_ms * 1000;
    rq.tv_sec = (time_t)(wait_us / 1000000);
    rq.tv_nsec = (long)((wait_us % 1000000) * 1000);
    nanosleep(&rq, NULL);
  }
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

void sim_fill_noise(uint8_t *buf, uint32_t len, uint32_t seed)
{
  uint32_t s = seed, i;
  memset(buf, 0, len);
  if(s == 0) return;
  for(i = 0; i < len; i++) {
    s ^= s << 13; s ^= s >> 17; s ^= s << 5;
    buf[i] = (uint8_t)(s >> 11);
  }
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
 * @brief The rabbit has started downloading a resource.
 *
 * Arms every "fetch <ms>" event against the clock, once. The gateway calls
 * this when the rabbit opens a connection to something other than the XMPP
 * port while a session is up, which is what RequestResource looks like from
 * the outside.
 */
void sim_note_fetch(void)
{
  static int armed;

  if(armed) return;
  armed = 1;
  for(int i = 0; i < sim.nb_events; i++) {
    struct sim_event *e = &sim.events[i];
    if(!e->rel_fetch) continue;
    e->at_ms += sim_now();
    e->rel_fetch = 0;
    sim_log("scenario: an event is now due at %u ms, %u ms into the download",
            e->at_ms, e->at_ms - sim_now());
  }
}

/**
 * @brief Read a scenario file.
 *
 * Directives, one per line, '#' starts a comment:
 *   psk <passphrase>              what the AP knows; the PMK is derived from it
 *   conf <file>                   a config page (mkconf.py). WPA3 needs one:
 *                                 SAE reads the passphrase out of it, as the
 *                                 bootloader's wifiAuth does, and a scenario
 *                                 without one is a rabbit that can only do PSK
 *   tick <ms>                      virtual time per main-loop pass
 *   realtime                       pace the loop to the wall clock, for when
 *                                  something outside has its own timers
 *   stop <ms>
 *   ap <bssid> <ssid> <channel> <security> [<rssi>] [pmf] [sae-token]
 *                                 [h2e-only] [h2e]
 *                                 h2e-only: hostapd's sae_pwe=1, the AP
 *                                 advertises SAE hash-to-element and takes
 *                                 nothing else; h2e: sae_pwe=2, advertises
 *                                 it and takes both; neither: sae_pwe=0
 *   at <ms> deauth [<bssid>] [reason <n>]
 *   at <ms> disassoc [<bssid>] [reason <n>]
 *   at <ms> down <bssid>
 *   at <ms> up <bssid>
 *   at <ms> arp on|off            the gateway answers ARP, or stops
 *   at <ms> server-drop           the warren's TCP connections close
 *   at <ms> rfid <16 hex>|none    a tag is on the reader (eight bytes), or
 *                                 none is any more
 *   at <ms> button down|up        the head button: down then up within half
 *                                 a second is the click that stops a sound
 *   at <ms> server-close          …after announcing </stream:stream>, which
 *                                 is what a server shutting down does
 *   at <ms> rekey [<bssid>]       the AP rotates the group key
 *   at <ms> ptk-rekey [<bssid>]   the AP restarts the four-way handshake
 *   at <ms> forged-msg1 [<bssid>] a message 1 the AP did not send, no message 3
 *   at <ms> resend-msg3 [<bssid>] the AP retransmits message 3
 *   at <ms> eapol-oversized [<bssid>]  a group message declaring a 64 KB body
 *   at <ms> expect-ptk-installs <n>    exactly n pairwise key installs so far
 *   at <ms> expect-group-installs <n>  exactly n group key installs so far
 *   at <ms> expect-ptk-synced          the station's pairwise key is the AP's
 *   at <ms> expect-eapol-badlen <n>    >= n EAPOL frames refused for their length
 *   at <ms> expect-tkip-refused <n>    >= n networks refused for pairwise TKIP
 *   at <ms> expect-sae-refused <n>     >= n PSK attempts refused on a SAE network
 *   at <ms> reauth [<bssid>]      the AP repeats its authentication reply
 *   at <ms> drop-eapol <count>
 *   at <ms> expect <state>         assert ieee80211_state, else exit non-zero
 *   at <ms> expect-sae h2e|hnp     assert which password element derivation
 *                                 the AP saw in the last SAE commit:
 *                                 hash-to-element or hunting and pecking.
 *                                 A transition-mode AP that offers H2E and
 *                                 reaches RUN is not proof the station chose
 *                                 it; this is
 *   at <ms> expect-not <state>     the opposite, for when what matters is
 *                                 that something did NOT happen. `expect RUN`
 *                                 pins one state out of six; `expect-not RUN`
 *                                 says the association never completed, which
 *                                 is the property a negative test is really
 *                                 about and cannot be satisfied by accident
 *
 *   decoder <kbit/s>               the audio decoder takes a sound at this
 *                                  bit rate, as a VS1003 does, instead of
 *                                  swallowing all it is offered: what a
 *                                  stream needs for its buffering to be seen
 *   config-mode                    the head button is held at power-on: the
 *                                  rabbit becomes an access point and serves
 *                                  its configuration pages (phone.c joins it)
 *   segment <bytes>                the TCP segment size the phone uploads
 *                                  with; 800 is the rabbit's own window
 *   at <ms> upload                 the phone POSTs --upload <file> to the
 *                                  upgrade page, as the browser form does
 *   at <ms> expect-flash [<n>]     assert flashFirmware was reached n times
 *   at <ms> expect-no-reset        assert the firmware never asked to reset
 *                                  (a VM out of memory does, reason 1)
 *   at <ms> http-big <bytes>       from now on the gateway answers every
 *                                  HTTP request itself, 200 and a body of
 *                                  that many bytes; 0 turns it off
 *   at <ms> http-bighead <bytes>   the same, but as one header line that
 *                                  never ends: no blank line, ever
 *   at <ms> data [group]           the AP sends one protected data frame,
 *                                  next packet number of the pairwise key,
 *                                  or of the group key to the broadcast
 *                                  address
 *   at <ms> replay [group] [retry] the last such frame again with the
 *                                  number it had: a replay, or with `retry`
 *                                  (the Retry bit) the AP's retransmission
 *   at <ms> expect-replay <u> <g> <r>  exactly u unicast and g group frames
 *                                  counted as replays, r as retransmissions
 *                                  (ccmp_replay_count's counters)
 *   at <ms> expect-data-up <n>     exactly n data frames handed up by the
 *                                  driver so far (./rabbit: EAPOL is not
 *                                  counted, and nothing else sends data)
 *
 *   fetch <ms> <event>             the same events, but <ms> after the rabbit
 *                                  starts downloading a sound or a
 *                                  choreography, so "cut it mid-download" is
 *                                  a thing a scenario can say without
 *                                  knowing when the download begins
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
    } else if(!strcmp(tok[0], "conf") && n >= 2) {
      snprintf(sim.conf, sizeof(sim.conf), "%s", tok[1]);
    } else if(!strcmp(tok[0], "realtime")) {
      sim.realtime = 1;
    } else if(!strcmp(tok[0], "config-mode")) {
      sim.config_mode = 1;
    } else if(!strcmp(tok[0], "decoder") && n >= 2) {
      sim.decoder_kbps = (uint32_t)strtoul(tok[1], NULL, 10);
    } else if(!strcmp(tok[0], "segment") && n >= 2) {
      sim.segment = (uint32_t)strtoul(tok[1], NULL, 10);
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
      /* Braces, now that there is more than one flag: the loop had none,
         and the second test landed outside it. */
      for(int i = 5; i < n; i++) {
        if(!strcmp(tok[i], "pmf")) a->pmf = 1;
        if(!strcmp(tok[i], "h2e-only")) a->sae_h2e_only = 1;
        if(!strcmp(tok[i], "h2e")) a->sae_h2e = 1;
        /* Make the AP answer the first SAE commit with status 76 and a
           token, the way a loaded access point does. Without this the
           rabbit never exercises that path, and the path is the most
           likely reason a real association fails. */
        if(!strcmp(tok[i], "sae-token")) a->sae_token = 1;
      }
      sim.nb_aps++;
    } else if((!strcmp(tok[0], "at") || !strcmp(tok[0], "fetch")) && n >= 3) {
      struct sim_event *e;
      if(sim.nb_events >= SIM_MAX_EVENTS) {
        fprintf(stderr, "%s:%d: too many events (max %d)\n", path, lineno, SIM_MAX_EVENTS);
        fclose(f); return -1;
      }
      e = &sim.events[sim.nb_events];
      memset(e, 0, sizeof(*e));
      e->at_ms = (uint32_t)strtoul(tok[1], NULL, 10);
      e->rel_fetch = !strcmp(tok[0], "fetch");
      e->ap = -1;
      e->arg = 0;
      if(!strcmp(tok[2], "deauth") || !strcmp(tok[2], "disassoc")) {
        e->kind = !strcmp(tok[2], "deauth") ? SIM_EV_DEAUTH : SIM_EV_DISASSOC;
        e->arg = 7;  /* IEEE80211_REASON_NOT_ASSOCED, what an AP sends on a reboot */
        for(int i = 3; i < n; i++) {
          if(!strcmp(tok[i], "reason") && i + 1 < n) e->arg = (int)strtol(tok[++i], NULL, 10);
          /* `protected` makes the AP sign the frame with the IGTK, which is
             the control for the forged case: a rabbit that drops every
             management frame would pass the forged test and fail this one. */
          else if(!strcmp(tok[i], "protected")) e->kind = SIM_EV_DEAUTH_PROT;
          else if(strchr(tok[i], ':')) e->ap = ap_by_bssid(tok[i]);
        }
      } else if(!strcmp(tok[2], "down") || !strcmp(tok[2], "up")) {
        e->kind = !strcmp(tok[2], "down") ? SIM_EV_AP_DOWN : SIM_EV_AP_UP;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
        if(e->ap < 0 && n >= 4) {
          fprintf(stderr, "%s:%d: no AP with BSSID %s\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "restart")) {
        /* hostapd going down and coming back: one broadcast deauthenticate
           (signed under 802.11w), then an AP that knows nobody. */
        e->kind = SIM_EV_AP_RESTART;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
        if(e->ap < 0 && n >= 4) {
          fprintf(stderr, "%s:%d: no AP with BSSID %s\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "forget")) {
        /* The AP drops the station's entry and says nothing at all; with
           `mute` it also swallows the station's frames instead of
           answering them with reason 7. */
        e->kind = SIM_EV_AP_FORGET;
        for(int i = 3; i < n; i++) {
          if(!strcmp(tok[i], "mute")) e->arg = 1;
          else e->ap = ap_by_bssid(tok[i]);
        }
        if(e->ap < 0 && n >= 4 && !(n == 4 && e->arg)) {
          fprintf(stderr, "%s:%d: no AP with BSSID %s\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "server-vanish")) {
        e->kind = SIM_EV_SERVER_VANISH;
      } else if(!strcmp(tok[2], "expect-reassoc") && n >= 4) {
        e->kind = SIM_EV_EXPECT_REASSOC;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-xmpp-opens") && n >= 4) {
        e->kind = SIM_EV_EXPECT_XMPP;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "security") && n >= 5) {
        /* An admin changing the SSID's security, as a UniFi controller
           does: the AP reprovisions (see `restart`) with the new security. */
        e->kind = SIM_EV_AP_SECURITY;
        e->ap = ap_by_bssid(tok[3]);
        e->arg = parse_security(tok[4]);
        if(e->ap < 0 || e->arg < 0) {
          fprintf(stderr, "%s:%d: security needs a known BSSID and a security\n", path, lineno);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "arp") && n >= 4) {
        e->kind = SIM_EV_ARP;
        e->arg = !strcmp(tok[3], "on");
      } else if(!strcmp(tok[2], "server-close")) {
        e->kind = SIM_EV_SERVER_CLOSE;
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
      } else if(!strcmp(tok[2], "inject-plaintext")) {
        e->kind = SIM_EV_INJECT_PLAIN;
        for(int i = 3; i < n; i++) {
          if(!strcmp(tok[i], "eapol")) e->arg = 1;
          else e->ap = ap_by_bssid(tok[i]);
        }
      } else if(!strcmp(tok[2], "dhcp-rogue")) {
        e->kind = SIM_EV_DHCP_ROGUE;
      } else if(!strcmp(tok[2], "inject-probe-ssid") && n >= 4) {
        e->kind = SIM_EV_INJECT_PROBE;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-plain-dropped") && n >= 4) {
        e->kind = SIM_EV_EXPECT_PLAIN_DROPPED;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "forged-msg1")) {
        e->kind = SIM_EV_FORGED_MSG1;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "resend-msg3")) {
        e->kind = SIM_EV_RESEND_MSG3;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "eapol-oversized")) {
        e->kind = SIM_EV_EAPOL_OVERSIZED;
        if(n >= 4) e->ap = ap_by_bssid(tok[3]);
      } else if(!strcmp(tok[2], "expect-ptk-installs") && n >= 4) {
        e->kind = SIM_EV_EXPECT_PTK_INSTALLS;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-group-installs") && n >= 4) {
        e->kind = SIM_EV_EXPECT_GROUP_INSTALLS;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-ptk-synced")) {
        e->kind = SIM_EV_EXPECT_PTK_SYNCED;
      } else if(!strcmp(tok[2], "expect-eapol-badlen") && n >= 4) {
        e->kind = SIM_EV_EXPECT_EAPOL_BADLEN;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-tkip-refused") && n >= 4) {
        e->kind = SIM_EV_EXPECT_TKIP_REFUSED;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-sae-refused") && n >= 4) {
        e->kind = SIM_EV_EXPECT_SAE_REFUSED;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect") && n >= 4) {
        e->kind = SIM_EV_EXPECT_STATE;
        e->arg = parse_state(tok[3]);
        if(e->arg < 0) {
          fprintf(stderr, "%s:%d: unknown state \"%s\"\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "upload")) {
        e->kind = SIM_EV_UPLOAD;
      } else if(!strcmp(tok[2], "expect-flash")) {
        e->kind = SIM_EV_EXPECT_FLASH;
        e->arg = (n >= 4) ? (int)strtol(tok[3], NULL, 10) : 1;
      } else if(!strcmp(tok[2], "rfid") && n >= 4) {
        /* The reader sees one tag until `rfid none`: the runtime reports a
           tag that stays once a second, which is what a Ztamp left on the
           rabbit does, so a scenario takes it away when it has been seen. */
        e->kind = SIM_EV_RFID;
        if(strcmp(tok[3], "none")) {
          if(strlen(tok[3]) != 16 || strspn(tok[3], "0123456789abcdefABCDEF") != 16) {
            fprintf(stderr, "%s:%d: a tag is 16 hex digits, or none\n", path, lineno);
            fclose(f); return -1;
          }
          for(int i = 0; i < 8; i++) {
            char h[3] = { tok[3][2*i], tok[3][2*i+1], 0 };
            e->tag[i] = (uint8_t)strtoul(h, NULL, 16);
          }
          e->arg = 1;
        }
      } else if(!strcmp(tok[2], "button") && n >= 4) {
        e->kind = SIM_EV_BUTTON;
        e->arg = !strcmp(tok[3], "down");
      } else if(!strcmp(tok[2], "expect-no-reset")) {
        e->kind = SIM_EV_EXPECT_NO_RESET;
      } else if(!strcmp(tok[2], "http-big") && n >= 4) {
        e->kind = SIM_EV_HTTP_BIG;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "http-bighead") && n >= 4) {
        e->kind = SIM_EV_HTTP_BIGHEAD;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "data") || !strcmp(tok[2], "replay")) {
        /* `data [group]`: one protected data frame from the AP, with the
           next packet number. `replay [group] [retry]`: the last one
           again, with the number it had. */
        e->kind = !strcmp(tok[2], "data") ? SIM_EV_AP_DATA : SIM_EV_AP_REPLAY;
        for(int i = 3; i < n; i++) {
          if(!strcmp(tok[i], "group")) e->arg = 1;
          else if(!strcmp(tok[i], "retry") && e->kind == SIM_EV_AP_REPLAY) e->arg2 = 1;
          else e->ap = ap_by_bssid(tok[i]);
        }
      } else if(!strcmp(tok[2], "expect-replay") && n >= 6) {
        e->kind = SIM_EV_EXPECT_REPLAY;
        e->arg  = (int)strtol(tok[3], NULL, 10);
        e->arg2 = (int)strtol(tok[4], NULL, 10);
        e->arg3 = (int)strtol(tok[5], NULL, 10);
      } else if(!strcmp(tok[2], "expect-data-up") && n >= 4) {
        e->kind = SIM_EV_EXPECT_DATA_UP;
        e->arg = (int)strtol(tok[3], NULL, 10);
      } else if(!strcmp(tok[2], "expect-sae") && n >= 4) {
        e->kind = SIM_EV_EXPECT_SAE;
        if(!strcmp(tok[3], "h2e")) e->arg = 1;
        else if(!strcmp(tok[3], "hnp")) e->arg = 2;
        else {
          fprintf(stderr, "%s:%d: expect-sae takes h2e or hnp, not \"%s\"\n", path, lineno, tok[3]);
          fclose(f); return -1;
        }
      } else if(!strcmp(tok[2], "expect-not") && n >= 4) {
        e->kind = SIM_EV_REFUSE_STATE;
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

/* --- the audit's events, shared by both binaries ----------------------- */

/* Counters the hardening steps add to the firmware. Weak: a build without
   the step has no symbol, and the assertion then fails for that reason --
   which is the control the scenario wants, not a link error. */
extern uint8_t eapol_bad_length __attribute__((weak));
extern uint8_t ieee80211_tkip_refused __attribute__((weak));
extern uint8_t ieee80211_sae_refused __attribute__((weak));
/* ccmp_replay_count's: frames examined, what would have been refused. */
extern uint16_t ieee80211_ccmp_seen __attribute__((weak));
extern uint16_t ieee80211_ccmp_replay_uc __attribute__((weak));
extern uint16_t ieee80211_ccmp_replay_gr __attribute__((weak));
extern uint16_t ieee80211_ccmp_retry __attribute__((weak));
extern uint32_t ieee80211_ccmp_last_iv __attribute__((weak));
extern uint32_t ieee80211_ccmp_last_eiv __attribute__((weak));

void sim_replay_summary(void)
{
  if(&ieee80211_ccmp_seen == NULL) return;
  printf("ccmp replay check   %u examined, replays %u unicast / %u group, "
         "%u retransmitted; last Eiv/Iv %08x/%08x\n",
         ieee80211_ccmp_seen, ieee80211_ccmp_replay_uc,
         ieee80211_ccmp_replay_gr, ieee80211_ccmp_retry,
         ieee80211_ccmp_last_eiv, ieee80211_ccmp_last_iv);
}

static void sim_expect_count(const char *what, const uint8_t *counter,
                             const char *step, int want)
{
  char why[200];
  if(counter == NULL) {
    snprintf(why, sizeof(why), "expected %s: this build has no %s step, "
             "nothing counts them", what, step);
    sim_note_failure(why);
  } else if(*counter >= want) {
    sim_log("ok   %u %s, at least %d expected", *counter, what, want);
  } else {
    snprintf(why, sizeof(why), "expected at least %d %s, the rabbit counted %u",
             want, what, *counter);
    sim_note_failure(why);
  }
}

static void sim_expect_exact(const char *what, uint32_t have, int want)
{
  char why[160];
  if((int)have == want) {
    sim_log("ok   %u %s, as expected", have, what);
  } else {
    snprintf(why, sizeof(why), "expected exactly %d %s, there were %u",
             want, what, have);
    sim_note_failure(why);
  }
}

int sim_audit_event(struct sim_event *e)
{
  switch(e->kind) {
    case SIM_EV_FORGED_MSG1:     vap_forged_msg1(e->ap); return 1;
    case SIM_EV_RESEND_MSG3:     vap_resend_msg3(e->ap); return 1;
    case SIM_EV_EAPOL_OVERSIZED: vap_eapol_oversized(e->ap); return 1;
    case SIM_EV_EXPECT_PTK_INSTALLS:
      sim_expect_exact("pairwise key install(s)", vap_stats.ptk_installs, e->arg);
      return 1;
    case SIM_EV_EXPECT_GROUP_INSTALLS:
      sim_expect_exact("group key install(s)", vap_stats.group_installs, e->arg);
      return 1;
    case SIM_EV_EXPECT_PTK_SYNCED:
      if(vap_sta_ptk_synced())
        sim_log("ok   the station's pairwise key is the one its AP transmits under");
      else
        sim_note_failure("the station's pairwise key is not the one its AP "
                         "transmits under -- the link is deaf in both directions");
      return 1;
    case SIM_EV_EXPECT_EAPOL_BADLEN:
      sim_expect_count("EAPOL frame(s) refused for a body length past the frame",
                       &eapol_bad_length, "eapol_mic_len", e->arg);
      return 1;
    case SIM_EV_EXPECT_TKIP_REFUSED:
      sim_expect_count("network(s) refused for pairwise TKIP",
                       &ieee80211_tkip_refused, "no_tkip_pairwise", e->arg);
      return 1;
    case SIM_EV_EXPECT_SAE_REFUSED:
      sim_expect_count("PSK attempt(s) refused on a network that did SAE",
                       &ieee80211_sae_refused, "sae_required", e->arg);
      return 1;
    case SIM_EV_AP_DATA:   vap_send_data(e->ap, e->arg); return 1;
    case SIM_EV_AP_REPLAY: vap_replay(e->ap, e->arg, e->arg2); return 1;
    case SIM_EV_EXPECT_REPLAY:
      if(&ieee80211_ccmp_seen == NULL) {
        sim_note_failure("expected CCMP replay counters: this build has no "
                         "ccmp_replay_count step, nothing looks at a packet number");
      } else if(ieee80211_ccmp_replay_uc == e->arg
                && ieee80211_ccmp_replay_gr == e->arg2
                && ieee80211_ccmp_retry == e->arg3) {
        sim_log("ok   replays counted: %u unicast, %u group, %u retransmitted "
                "(%u frames examined, last Eiv/Iv %08x/%08x)",
                ieee80211_ccmp_replay_uc, ieee80211_ccmp_replay_gr,
                ieee80211_ccmp_retry, ieee80211_ccmp_seen,
                ieee80211_ccmp_last_eiv, ieee80211_ccmp_last_iv);
      } else {
        char why[220];
        snprintf(why, sizeof(why), "expected replays %d unicast / %d group / "
                 "%d retransmitted, the rabbit counted %u / %u / %u (%u examined)",
                 e->arg, e->arg2, e->arg3, ieee80211_ccmp_replay_uc,
                 ieee80211_ccmp_replay_gr, ieee80211_ccmp_retry,
                 ieee80211_ccmp_seen);
        sim_note_failure(why);
      }
      return 1;
    case SIM_EV_EXPECT_DATA_UP:
      sim_expect_exact("data frame(s) handed up by the driver", sim.data_up, e->arg);
      return 1;
    default:
      return 0;
  }
}
