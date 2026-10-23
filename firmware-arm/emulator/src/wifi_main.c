/**
 * @file wifi_main.c
 * @brief The rabbit's Wi-Fi stack, on a host, against a virtual access point.
 *
 * Builds src/net/ieee80211.c, src/net/eapol.c and the crypto beside them —
 * the rabbit's real code, not a reimplementation — and runs them against the
 * virtual AP in vap.c. A scenario file says what the air looks like and what
 * happens to it: how many access points, on what channels, with what
 * security, and when one of them deauthenticates the rabbit or goes dark.
 *
 * What it replaces: unplugging the rabbit. Issue #24 asks whether the
 * firmware rejoins after a link loss, and answering it on the hardware costs
 * a power cycle per attempt and tells you only "it did not come back".
 * Here the same question costs a second and prints which state the machine
 * stopped in, and why.
 *
 *   ./testWIFI scenarios/deauth.txt
 *
 * The exit status is the number of failed assertions, so this drops into CI.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "utils/debug.h"
#include "usb/hcdmem.h"
#include "usb/rt2501usb.h"
#include "net/ieee80211.h"
#include "net/eapol.h"

#include "sim.h"
#include "vap.h"
#include "vapcrypto.h"

/* The firmware's millisecond clock, which sysTimems() reads. sim_advance()
 * is the only thing that moves it. */
uint32_t counter_timer, counter_timer_s;

/* The simulated register window; see the _NAB_SIM block in inc/common.h. */
volatile uint8_t sim_mmio[1u << 20];

/* eapol.c defines this with a uint16_t length; vnet.c declares it with an
 * int32_t one. Matching the definition. */
extern void mypassword_to_pmk(const uint8_t *password, uint8_t *ssid,
                              uint16_t ssidlength, uint8_t *pmk);

/* What rt2501_auth() is handed as the key. For WPA and WPA2 the rabbit is
 * configured with a 32-byte PMK, not a passphrase: eapol_init() copies
 * ieee80211_key straight into pmk without running PBKDF2. rt2501_auth()
 * then memcpy's IEEE80211_MAX_KEYLEN — 64 — bytes out of this buffer, so it
 * is that long whatever is actually in it. */
static uint8_t configured_key[IEEE80211_MAX_KEYLEN];

/* --- what the scan found ---------------------------------------------- */

#define MAX_RESULTS 16
static struct rt2501_scan_result results[MAX_RESULTS];
static int nb_results;

static void scan_callback(struct rt2501_scan_result *r, void *userparam)
{
  (void)userparam;
  for(int i=0;i<nb_results;i++)
    if(memcmp(results[i].bssid, r->bssid, IEEE80211_ADDR_LEN) == 0) {
      results[i] = *r;      /* a later beacon wins, as the firmware sees it */
      return;
    }
  if(nb_results >= MAX_RESULTS) return;
  results[nb_results++] = *r;

  sim_log("scan: \"%s\" %02x:%02x:%02x:%02x:%02x:%02x channel %u rssi %d "
          "rateset 0x%04x encryption 0x%02x",
          r->ssid, r->bssid[0],r->bssid[1],r->bssid[2],r->bssid[3],r->bssid[4],
          r->bssid[5], r->channel, r->rssi, r->rateset, r->encryption);
  if(r->encryption == IEEE80211_CRYPT_UNSUPPORTED)
    sim_log("scan:   ^ the firmware calls this one unsupported");
}

/**
 * @brief Pick a network the way the boot bytecode does: the configured SSID,
 *        strongest signal first.
 */
static struct rt2501_scan_result *best_for(const char *ssid)
{
  struct rt2501_scan_result *best = NULL;
  for(int i=0;i<nb_results;i++) {
    if(strcmp((char *)results[i].ssid, ssid) != 0) continue;
    if(results[i].encryption == IEEE80211_CRYPT_UNSUPPORTED) continue;
    if(best == NULL || results[i].rssi > best->rssi) best = &results[i];
  }
  return best;
}

/* --- the connection attempt ------------------------------------------- */

static int connect_attempts;

/**
 * @brief Scan, choose, authenticate — what the boot bytecode's netScan and
 *        netAuth pair does, in the order it does it.
 *
 * The comparison the real OPnetAuth makes before it will call netAuth at all
 * is reproduced here, and printed, because it is the line that decides
 * whether a rabbit reconnects or sits still:
 *
 *   if ((mac!=NIL)&&(bssid!=NIL)&&(crypt&0xF0)==encrypt)   vinterp.c:921
 *
 * `crypt` comes from the scan, `encrypt` from the stored configuration. When
 * they disagree the bytecode silently does nothing, which from the outside
 * looks exactly like a radio that failed.
 */
static int try_connect(const char *ssid, const char *psk, int configured_encrypt)
{
  (void)psk;
  struct rt2501_scan_result *r;

  connect_attempts++;
  nb_results = 0;
  sim_log("---- attempt %d: scanning for \"%s\"", connect_attempts, ssid);
  rt2501_scan((const uint8_t *)ssid, scan_callback, NULL);

  r = best_for(ssid);
  if(r == NULL) {
    sim_log("---- attempt %d: \"%s\" was not found", connect_attempts, ssid);
    return 0;
  }

  sim_log("OPnetAuth check: crypt=0x%02x (crypt&0xF0)=0x%02x configured "
          "encrypt=0x%02x -> %s",
          r->encryption, r->encryption & 0xF0, configured_encrypt,
          (r->encryption & 0xF0) == configured_encrypt
            ? "netAuth IS called"
            : "netAuth is SKIPPED, the bytecode does nothing");

  if((r->encryption & 0xF0) != configured_encrypt) {
    sim_note_failure("the bytecode would skip netAuth: the scan's cipher bits "
                     "do not equal the configured encryption");
    return 0;
  }

  rt2501_auth((const uint8_t *)ssid, r->mac, r->bssid, r->channel, r->rateset,
              IEEE80211_AUTH_OPEN, r->encryption, configured_key);
  return 1;
}

/* --- events ----------------------------------------------------------- */

void sim_run_events(void)
{
  for(int i=0;i<sim.nb_events;i++) {
    struct sim_event *e = &sim.events[i];
    if(e->fired || sim_now() < e->at_ms) continue;
    e->fired = 1;
    switch(e->kind) {
      case SIM_EV_DEAUTH:      vap_deauth(e->ap, 0, (uint16_t)e->arg); break;
      case SIM_EV_DISASSOC:    vap_deauth(e->ap, 1, (uint16_t)e->arg); break;
      case SIM_EV_AP_DOWN:     vap_set_up(e->ap, 0); break;
      case SIM_EV_AP_UP:       vap_set_up(e->ap, 1); break;
      case SIM_EV_DROP_EAPOL:  vap_drop_eapol(e->arg); break;
      case SIM_EV_EXPECT_STATE:
        if(ieee80211_state == e->arg)
          sim_log("ok   state is %s, as expected", sim_state_name(e->arg));
        else {
          char why[160];
          snprintf(why, sizeof(why), "expected state %s, found %s",
                   sim_state_name(e->arg), sim_state_name(ieee80211_state));
          sim_note_failure(why);
        }
        break;
      default: break;
    }
  }
}

/* --- main ------------------------------------------------------------- */

static int configured_encrypt_for(int security)
{
  /* What the owner stored in the rabbit's configuration, in the form
   * OPnetAuth compares against: the high nibble of ieee80211_encryption. */
  switch(security) {
    case SIM_SEC_OPEN:      return IEEE80211_CRYPT_NONE;
    case SIM_SEC_WEP:       return IEEE80211_CRYPT_WEP;
    case SIM_SEC_WPA_TKIP:  return IEEE80211_CRYPT_WPA;
    default:                return IEEE80211_CRYPT_WPA2;
  }
}

int main(int argc, char **argv)
{
  const char *scenario = NULL;
  const char *target_ssid = NULL;
  int reconnect = 1;
  int last_state = -1;
  uint32_t next_timer_ms = 0;
  uint32_t next_retry_ms = 0;
  int was_connected = 0;
  int reconnections = 0;

  for(int i=1;i<argc;i++) {
    if(!strcmp(argv[i], "--no-reconnect")) reconnect = 0;
    else if(!strcmp(argv[i], "--ssid") && i+1 < argc) target_ssid = argv[++i];
    else if(argv[i][0] == '-') {
      fprintf(stderr,
        "usage: %s [scenario] [--ssid <name>] [--no-reconnect]\n"
        "\n"
        "  scenario        a scenario file; without one, one open AP on channel 6\n"
        "  --ssid          which network to join; default is the first AP's\n"
        "  --no-reconnect  do not retry after a link loss, to see where it stops\n",
        argv[0]);
      return 2;
    }
    else scenario = argv[i];
  }

  if(scenario != NULL) {
    if(sim_load(scenario) != 0) return 2;
  } else {
    sim_default();
  }
  if(target_ssid == NULL) target_ssid = sim.aps[0].ssid;

  sim_log("scenario %s, target SSID \"%s\", tick %u ms, stop at %u ms",
          scenario ? scenario : "(default)", target_ssid, sim.tick_ms,
          sim.stop_ms);

  if(vap_crypto_selftest() != 0) {
    fprintf(stderr, "the emulator's own crypto is wrong; not running\n");
    return 2;
  }

  /* Derive the PMK once, with the firmware's own PBKDF2, and give it to both
   * ends: the rabbit through rt2501_auth(), the AP through vap_set_pmk(). */
  if(sim.aps[0].security >= SIM_SEC_WPA_TKIP) {
    uint8_t pmk[EAPOL_MASTER_KEY_LENGTH];
    mypassword_to_pmk((const uint8_t *)sim.psk, (uint8_t *)target_ssid,
                      (uint16_t)strlen(target_ssid), pmk);
    memcpy(configured_key, pmk, sizeof(pmk));
    vap_set_pmk(pmk);
    sim_log("PMK for \"%s\" / \"%s\": %02x%02x%02x%02x...", target_ssid,
            sim.psk, pmk[0], pmk[1], pmk[2], pmk[3]);
  } else {
    snprintf((char *)configured_key, sizeof(configured_key), "%s", sim.psk);
  }

  vap_init();
  ieee80211_init();
  rt2501_driver_install();
  rt2501_setmode(IEEE80211_M_MANAGED, NULL, 0);

  int configured = configured_encrypt_for(sim.aps[0].security);

  /* Events dated 0 have to be in effect before the first attempt. The scan
   * is a blocking call that advances the virtual clock by nearly five
   * seconds and runs the whole handshake inside it, so an event only looked
   * at by the main loop would arrive after everything it was meant to
   * disturb. The first version of the lost-message-2 scenario swallowed
   * message 4 instead and looked like a pass. */
  sim_run_events();
  try_connect(target_ssid, sim.psk, configured);

  while(sim_now() < sim.stop_ms) {
    struct rt2501buffer *r;

    vap_poll();

    /* Drain the receive queue, as main.c does. EAPOL frames are consumed
     * inside rt2501_receive() by eapol_input(); anything else would go up to
     * netCb() and into the TCP/IP stack, which this harness does not run. */
    while((r = rt2501_receive()) != NULL) {
      sim_log("data: %u bytes from %02x:%02x:%02x:%02x:%02x:%02x", r->length,
              r->source_mac[0],r->source_mac[1],r->source_mac[2],
              r->source_mac[3],r->source_mac[4],r->source_mac[5]);
      hcd_free(r);
    }

    if(ieee80211_state != last_state) {
      sim_log("state %s -> %s   (radio %d)",
              last_state < 0 ? "-" : sim_state_name(last_state),
              sim_state_name(ieee80211_state), rt2501_state());
      if(ieee80211_state == IEEE80211_S_RUN) {
        if(was_connected) reconnections++;
        was_connected = 1;
      }
      last_state = ieee80211_state;
    }

    /* The firmware's 100 ms timer, which is what expires every timeout in
     * ieee80211.c. main.c calls it every fourth pass of a 50 ms loop. */
    if(sim_now() >= next_timer_ms) {
      next_timer_ms = sim_now() + 100;
      rt2501_timer();
    }

    /* The policy under test: when the driver gives up and returns to IDLE,
     * does anything try again? On the rabbit that is the boot bytecode's
     * job and not the driver's, which is the whole of issue #24 — so it is
     * modelled here, where it can be switched off with --no-reconnect to see
     * exactly where a rabbit that does not retry comes to rest.
     *
     * IDLE is reached two ways and both matter: a deauthentication while
     * connected, and a handshake that died halfway and timed out sixty
     * seconds later. */
    if(reconnect && ieee80211_state == IEEE80211_S_IDLE
       && sim_now() >= next_retry_ms) {
      next_retry_ms = sim_now() + 1000;
      sim_log("idle%s, retrying", was_connected ? " after a link loss" : "");
      try_connect(target_ssid, sim.psk, configured);
    }

    sim_run_events();
    sim_advance(sim.tick_ms);
  }

  printf("\n==== run summary ====\n");
  printf("virtual time        %u ms\n", sim_now());
  printf("final state         %s (radio %d)\n",
         sim_state_name(ieee80211_state), rt2501_state());
  printf("connect attempts    %d\n", connect_attempts);
  printf("reached RUN         %s\n", was_connected ? "yes" : "no");
  printf("reconnections       %d\n", reconnections);
  printf("frames tx / rx      %u / %u\n", vap_stats.tx_frames, vap_stats.rx_frames);
  printf("  transmitted into the void %u\n", vap_stats.tx_unheard);
  printf("beacons             %u\n", vap_stats.beacons_sent);
  printf("probe responses     %u\n", vap_stats.probe_responses);
  printf("auth / assoc resp   %u / %u\n", vap_stats.auth_responses,
         vap_stats.assoc_responses);
  printf("deauths sent        %u\n", vap_stats.deauths_sent);
  printf("EAPOL rabbit / AP   %u / %u\n", vap_stats.eapol_from_sta,
         vap_stats.eapol_to_sta);
  if(vap_stats.mics_verified || vap_stats.mics_failed)
    printf("  MIC ok / bad      %u / %u\n", vap_stats.mics_verified,
           vap_stats.mics_failed);
  if(vap_stats.handshakes_completed)
    printf("handshakes done     %u\n", vap_stats.handshakes_completed);
  printf("keys installed      %u\n", vap_stats.keys_installed);
  printf("channel switches    %u\n", vap_stats.channel_switches);
  if(vap_stats.rx_overruns)
    printf("rx overruns         %u\n", vap_stats.rx_overruns);
  printf("failures            %d\n", sim_failures());

  return sim_failures() == 0 ? 0 : 1;
}
