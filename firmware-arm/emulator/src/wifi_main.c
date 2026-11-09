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
#include "net/nab_rng.h"
#include "utils/mem.h"

#include "sim.h"
#include "hw.h"

/* boot.0.0.0.13.mtl:211 — `const CONF_WIFIKEY0=92;;  //64`. The config form
 * writes the passphrase there for every security mode (`v=='n'`, line 1814). */
#define CONF_WIFIKEY0 92
#include "vap.h"
#include "vapcrypto.h"

/* The firmware's millisecond clock, which sysTimems() reads. sim_advance()
 * is the only thing that moves it. */
uint32_t counter_timer, counter_timer_s;

/* The simulated register window; see the _NAB_SIM block in inc/common.h. */
volatile uint8_t sim_mmio[1u << 20];

/* eapol.c defines this with a uint16_t length; vnet.c declares it with an
 * int32_t one. Matching the definition. */
/* rx_protected's counter (ieee80211.c); weak zero in stubs/hw.c for a build
 * without that step, which is how the control run of
 * scenarios/wpa2-plaintext-inject.txt fails. */
extern uint8_t ieee80211_plain_dropped;

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
    if(e->fired || e->rel_fetch || sim_now() < e->at_ms) continue;
    e->fired = 1;
    switch(e->kind) {
      case SIM_EV_DEAUTH:      vap_deauth(e->ap, 0, (uint16_t)e->arg); break;
      case SIM_EV_DEAUTH_PROT: vap_deauth_protected(e->ap, 0, (uint16_t)e->arg); break;
      case SIM_EV_DISASSOC:    vap_deauth(e->ap, 1, (uint16_t)e->arg); break;
      case SIM_EV_AP_DOWN:     vap_set_up(e->ap, 0); break;
      case SIM_EV_AP_UP:       vap_set_up(e->ap, 1); break;
      case SIM_EV_AP_SECURITY:
        sim.aps[e->ap].security = e->arg;
        vap_restart(e->ap);
        break;
      case SIM_EV_AP_RESTART:  vap_restart(e->ap); break;
      case SIM_EV_AP_FORGET:   vap_forget(e->ap); if(e->arg) vap_mute(e->ap); break;
      case SIM_EV_SERVER_DROP:
      case SIM_EV_SERVER_CLOSE:
      case SIM_EV_SERVER_VANISH:
      case SIM_EV_EXPECT_XMPP:
      case SIM_EV_ARP:
      case SIM_EV_RFID:       break;   /* no network, no body, in this binary */
      case SIM_EV_EXPECT_REASSOC:
        if(sim.run_entries >= e->arg)
          sim_log("ok   reached RUN %d time%s, at least %d expected",
                  sim.run_entries, sim.run_entries == 1 ? "" : "s", e->arg);
        else {
          char why[160];
          snprintf(why, sizeof(why), "expected RUN to have been reached %d times, it was %d",
                   e->arg, sim.run_entries);
          sim_note_failure(why);
        }
        break;
      case SIM_EV_PTK_REKEY:  vap_ptk_rekey(e->ap); break;
      case SIM_EV_REKEY:      vap_rekey(e->ap); break;
      case SIM_EV_REAUTH:     vap_reauth(e->ap); break;
      case SIM_EV_DROP_EAPOL:  vap_drop_eapol(e->arg); break;
      case SIM_EV_INJECT_PLAIN: vap_inject_plain(e->ap, e->arg); break;
      case SIM_EV_FORGED_MSG1:
      case SIM_EV_RESEND_MSG3:
      case SIM_EV_EAPOL_OVERSIZED:
      case SIM_EV_EXPECT_PTK_INSTALLS:
      case SIM_EV_EXPECT_GROUP_INSTALLS:
      case SIM_EV_EXPECT_PTK_SYNCED:
      case SIM_EV_EXPECT_EAPOL_BADLEN:
      case SIM_EV_EXPECT_TKIP_REFUSED:
      case SIM_EV_EXPECT_SAE_REFUSED:
        sim_audit_event(e); break;
      case SIM_EV_EXPECT_PLAIN_DROPPED:
        if(ieee80211_plain_dropped >= e->arg)
          sim_log("ok   %u plaintext data frame(s) refused on the protected "
                  "link, at least %d expected", ieee80211_plain_dropped, e->arg);
        else {
          char why[160];
          snprintf(why, sizeof(why), "expected at least %d plaintext data "
                   "frame(s) refused, the rabbit refused %u",
                   e->arg, ieee80211_plain_dropped);
          sim_note_failure(why);
        }
        break;
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
      case SIM_EV_EXPECT_SAE:
        if(vap_last_sae_mode == e->arg)
          sim_log("ok   the last SAE commit used %s, as expected",
                  e->arg == 1 ? "hash-to-element" : "hunting and pecking");
        else {
          char why[160];
          snprintf(why, sizeof(why), "expected SAE %s, the AP saw %s",
                   e->arg == 1 ? "hash-to-element" : "hunting and pecking",
                   vap_last_sae_mode == 1 ? "hash-to-element"
                   : vap_last_sae_mode == 2 ? "hunting and pecking" : "no SAE commit at all");
          sim_note_failure(why);
        }
        break;
      case SIM_EV_REFUSE_STATE:
        if(ieee80211_state != e->arg)
          sim_log("ok   state is %s, and not %s, as expected",
                  sim_state_name(ieee80211_state), sim_state_name(e->arg));
        else {
          char why[160];
          snprintf(why, sizeof(why), "state must not be %s, but it is",
                   sim_state_name(e->arg));
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
  uint32_t sram_seed = 0, rssi_noise = 0;
  int eeprom_persist = 0;

  for(int i=1;i<argc;i++) {
    if(!strcmp(argv[i], "--no-reconnect")) reconnect = 0;
    else if(!strcmp(argv[i], "--rekey-ack")) vap_rekey_ack(1);
    else if(!strcmp(argv[i], "--ssid") && i+1 < argc) target_ssid = argv[++i];
    else if(!strcmp(argv[i], "--sram-seed") && i+1 < argc) sram_seed = (uint32_t)strtoul(argv[++i], NULL, 0);
    else if(!strcmp(argv[i], "--rssi-noise") && i+1 < argc) rssi_noise = (uint32_t)strtoul(argv[++i], NULL, 0);
    else if(!strcmp(argv[i], "--eeprom-persist")) eeprom_persist = 1;
    else if(argv[i][0] == '-') {
      fprintf(stderr,
        "usage: %s [scenario] [--ssid <name>] [--no-reconnect]\n"
        "\n"
        "  scenario        a scenario file; without one, one open AP on channel 6\n"
        "  --ssid          which network to join; default is the first AP's\n"
        "  --no-reconnect  do not retry after a link loss, to see where it stops\n"
        "  --rekey-ack     set Key Ack on a group rekey, which the standard forbids\n"
        "                  and which this firmware requires\n"
        "  --sram-seed N   what the retained RAM holds at power-up (0 = zeros)\n"
        "  --rssi-noise N  -1..+1 dB jitter on every frame's RSSI (0 = none)\n"
        "  --eeprom-persist write the config page back when the rabbit changes it\n"
        "                  (the entropy pool's seed); by default it stays a fixture\n",
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

  /* The config sector, if the scenario named one. Opened before the first
   * association, because that is when its passphrase is needed. */
  if(sim.conf[0] != 0)
    hw_eeprom_open(sim.conf);
  /* The scenarios share one config page and a run that stores the pool's
   * seed in it would hand the next run a seeded pool: a fixture, unless
   * a run is meant to be the next run's previous boot (tools/rng-boots.sh). */
  hw_eeprom_volatile(!eeprom_persist);

  /* The entropy pool, as main.c starts it: the retained RAM's power-up
   * contents -- zeros unless --sram-seed says otherwise, so a run stays
   * reproducible -- and the seed the config page may hold at 4064. */
  {
    static uint8_t sram[4096];
    uint8_t seed[NAB_RNG_SEED_LEN];
    sim_fill_noise(sram, sizeof sram, sram_seed);
    read_uc_flash(NAB_RNG_SEED_OFF, seed, sizeof seed);
    nab_rng_init(sram, sizeof sram, seed);
    sim_log("rng: pool started, %s seed in the config page, credit %u",
            nab_rng_seed_stored() ? "a" : "no", nab_rng_credit());
  }
  vap_set_rssi_noise(rssi_noise);

  if(vap_crypto_selftest() != 0) {
    fprintf(stderr, "the emulator's own crypto is wrong; not running\n");
    return 2;
  }

  /* Derive the PMK once, with the firmware's own PBKDF2, and give it to both
   * ends: the rabbit through rt2501_auth(), the AP through vap_set_pmk(). */
  if(sim.aps[0].security >= SIM_SEC_WPA_TKIP) {
    /* 40, not 32: mypassword_to_pmk() writes two whole SHA-1 blocks
     * and the firmware's own callers give it 40. ASan found the
     * overrun on 2026-11-08; on macOS it was the abort at exit. */
    uint8_t pmk[40];
    /* SAE needs the password itself, not the PMK, and where it comes from
     * is the whole point.
     *
     * This used to be `ieee80211_sae_password(sim.psk)` -- the harness
     * standing in for netPmk(). It passed every WPA3 scenario, and the
     * firmware it was testing could not associate with a WPA3-only UniFi:
     * netPmk runs when the password is TYPED, not when the rabbit boots, so
     * on real hardware ieee80211_sae_pw was always empty and SAE never
     * started. A harness that supplies what the firmware cannot is not a
     * test, and this one hid the bug for ten days.
     *
     * So it reads the passphrase where the bootloader's wifiAuth now reads
     * it: offset 92 of the config page, CONF_WIFIKEY0. No config page, no
     * passphrase, no SAE -- which is exactly what an unconfigured rabbit
     * does, and is asserted by scenarios/wpa3-sae-no-stored-password.txt. */
    const uint8_t *page = hw_eeprom_page();
    if(page != NULL && page[CONF_WIFIKEY0] != 0) {
      ieee80211_sae_password(page + CONF_WIFIKEY0);
      sim_log("SAE: passphrase read from the config page, offset %d",
              CONF_WIFIKEY0);
    } else {
      sim_log("SAE: the config page holds no passphrase — SAE cannot start, "
              "and an association here means PSK");
    }
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
        sim.run_entries++;
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
    sim_pace();
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
  if(vap_stats.ptk_rekeys_sent) {
    printf("ptk rekeys          %u sent, %u answered\n",
           vap_stats.ptk_rekeys_sent, vap_stats.ptk_rekeys_answered);
    if(vap_stats.ptk_rekeys_answered < vap_stats.ptk_rekeys_sent) {
      char why[128];
      snprintf(why, sizeof(why),
               "%u pairwise rekey(s) ignored — an AP that rekeys periodically "
               "gives up and deauthenticates",
               vap_stats.ptk_rekeys_sent - vap_stats.ptk_rekeys_answered);
      sim_note_failure(why);
    }
  }
  if(vap_stats.rekeys_sent)
    printf("group rekeys        %u sent, %u acknowledged\n",
           vap_stats.rekeys_sent, vap_stats.rekeys_acked);
  if(vap_stats.handshakes_completed)
    printf("handshakes done     %u\n", vap_stats.handshakes_completed);
  printf("keys installed      %u (pairwise %u, group %u)\n", vap_stats.keys_installed,
         vap_stats.ptk_installs, vap_stats.group_installs);
  if(vap_stats.ptk_mismatch)
    printf("key mismatch drops  %u (station and AP on different pairwise keys)\n",
           vap_stats.ptk_mismatch);
  if(vap_stats.forged_msg1 || vap_stats.msg3_resent || vap_stats.eapol_oversized)
    printf("attack frames       forged msg1 %u, msg3 resent %u, oversized %u\n",
           vap_stats.forged_msg1, vap_stats.msg3_resent, vap_stats.eapol_oversized);
  printf("channel switches    %u\n", vap_stats.channel_switches);
  if(vap_stats.rx_overruns)
    printf("rx overruns         %u\n", vap_stats.rx_overruns);
  if(vap_stats.rx_cipher_err)
    printf("rx cipher errors    %u\n", vap_stats.rx_cipher_err);
  if(vap_stats.plain_injected || ieee80211_plain_dropped)
    printf("plaintext injected  %u, refused %u\n", vap_stats.plain_injected,
           ieee80211_plain_dropped);
  printf("entropy pool        credit %u, %s\n", nab_rng_credit(),
         nab_rng_ready() ? "ready" : "not ready");
  {
    /* What kxBegin would draw next. An emulator's key, printed on purpose:
     * tools/rng-boots.sh compares it between boots. */
    uint8_t k[8];
    if(nab_rng_key_bytes(k, sizeof k))
      printf("  next key bytes    %02x%02x%02x%02x%02x%02x%02x%02x\n",
             k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7]);
  }
  /* A rekey the rabbit never answered is a failure even though it stayed
   * associated, because the symptom is silent: it keeps the group key it was
   * handed at association and goes deaf to broadcast traffic. Nothing about
   * its state says so. */
  if(vap_stats.rekeys_sent > vap_stats.rekeys_acked) {
    char why[128];
    snprintf(why, sizeof(why),
             "%u group rekey(s) went unanswered — the rabbit is still on the "
             "key it was given at association",
             vap_stats.rekeys_sent - vap_stats.rekeys_acked);
    sim_note_failure(why);
  }

  printf("failures            %d\n", sim_failures());

  return sim_failures() == 0 ? 0 : 1;
}
