/**
 * @file vap.c
 * @brief A virtual access point, in place of the RT2501 USB radio.
 *
 * This is the whole point of the emulator. Everything above the radio is the
 * rabbit's real code — src/net/ieee80211.c decides when to scan, authenticate,
 * associate and give up, src/net/eapol.c runs the WPA handshake, src/vm
 * interprets the bytecode the warren served. Only the hardware underneath is
 * replaced: instead of src/usb/rt2501usb.c pushing USB URBs at a Ralink chip,
 * the frames the firmware transmits are parsed here, and the answers a real AP
 * would send are handed back to ieee80211_input().
 *
 * So a deauthentication, an AP that stops answering, a second AP on the same
 * SSID, a beacon advertising ciphers the firmware has never met — each is a
 * line in a scenario file instead of a trip to the wall socket.
 *
 * Open, WEP, WPA and WPA2 networks run end to end, four-way handshake
 * included: the AP derives the same PTK the rabbit does, signs message 3 with
 * the KCK and wraps a group key with the KEK, so the group-key path that used
 * to be reachable only by flashing a rabbit is now a scenario file.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "utils/debug.h"
#include "utils/mem.h"

#include "usb/hcd.h"
#include "usb/hcdmem.h"
#include "usb/usbh.h"
#include "usb/rt2501usb_hw.h"
#include "usb/rt2501usb.h"
#include "net/ieee80211.h"
#include "net/eapol.h"
#include "net/hash.h"

#include "sim.h"
#include "vap.h"
#include "vapcrypto.h"

uint8_t rt2501_mac[IEEE80211_ADDR_LEN] = {0x00,0x19,0xdb,0x00,0xbe,0xef};
PDEVINFO rt2501_dev;

/* The radio's own state, as the USB driver would see it. */
static int vap_radio_up = 1;
static uint8_t vap_channel = 1;
static uint8_t vap_filter_bssid[IEEE80211_ADDR_LEN];

/* Counters the run summary prints. */
struct vap_stats vap_stats;

/* Frames queued from an AP towards the rabbit. Queued rather than delivered
 * inline: ieee80211_input() transmits as it processes, and handing it the
 * reply from inside rt2501_tx() would recurse through the whole handshake in
 * one stack. The radio has a queue too, so this is also the truer model. */
#define VAP_QUEUE_DEPTH 32
#define VAP_FRAME_MAX   512

struct vap_queued {
  uint8_t  data[VAP_FRAME_MAX];
  uint32_t length;
  int16_t  rssi;
  uint8_t  channel;
};

static struct vap_queued vap_queue[VAP_QUEUE_DEPTH];
static int vap_queue_head, vap_queue_count;

static uint32_t vap_next_beacon_ms;
static int vap_eapol_drops;

/* --- helpers ---------------------------------------------------------- */

static const char *macstr(const uint8_t *m)
{
  static char buf[4][18];
  static int which;
  char *b = buf[which = (which + 1) & 3];
  snprintf(b, 18, "%02x:%02x:%02x:%02x:%02x:%02x", m[0],m[1],m[2],m[3],m[4],m[5]);
  return b;
}

static int ap_index_for_bssid(const uint8_t *bssid)
{
  for(int i=0;i<sim.nb_aps;i++)
    if(memcmp(sim.aps[i].bssid, bssid, IEEE80211_ADDR_LEN) == 0) return i;
  return -1;
}

/**
 * @brief Can the radio hear this AP right now?
 *
 * An AP that is powered off is silent, and one on another channel is
 * inaudible — which is exactly what makes a channel-by-channel scan take the
 * time it does, and worth modelling rather than wishing away.
 */
static int ap_audible(const struct sim_ap *a)
{
  return a->up && (a->channel == vap_channel);
}

static void queue_frame(const uint8_t *frame, uint32_t length, int16_t rssi,
                        uint8_t channel)
{
  struct vap_queued *q;
  if(length > VAP_FRAME_MAX) {
    sim_log("vap: dropping a %u-byte frame, the queue holds %d", length, VAP_FRAME_MAX);
    return;
  }
  if(vap_queue_count >= VAP_QUEUE_DEPTH) {
    vap_stats.rx_overruns++;
    return;
  }
  q = &vap_queue[(vap_queue_head + vap_queue_count) % VAP_QUEUE_DEPTH];
  memcpy(q->data, frame, length);
  q->length  = length;
  q->rssi    = rssi;
  q->channel = channel;
  vap_queue_count++;
}

/* --- frame construction ----------------------------------------------- */

static uint8_t *put_ie(uint8_t *p, uint8_t id, const void *data, uint8_t len)
{
  *p++ = id;
  *p++ = len;
  memcpy(p, data, len);
  return p + len;
}

static const uint8_t rsn_oui[3]  = {0x00,0x0f,0xac};
static const uint8_t wpa_oui[3]  = {0x00,0x50,0xf2};
static const uint8_t wpa_vendor[4] = {0x00,0x50,0xf2,0x01};

#define RSN_CIPHER_TKIP 0x02
#define RSN_CIPHER_CCMP 0x04
#define RSN_AKM_PSK     0x02
#define RSN_AKM_SAE     0x08

/**
 * @brief The RSN information element an AP of this flavour advertises.
 *
 * Laid out as 802.11 specifies: version, group cipher suite, pairwise count
 * and suites, AKM count and suites, RSN capabilities. Worth knowing while
 * reading the firmware's parser: ieee80211_input_mgt() starts reading at the
 * element body and takes the *version* field as the group-suite count. It
 * works only because the version is 1 and there is exactly one group suite.
 * Anything that changes either of those — and WPA3 does not — walks the
 * parser off by four bytes.
 */
static uint8_t *put_rsn_ie(uint8_t *p, int security)
{
  uint8_t body[64];
  uint8_t *b = body;
  uint8_t pairwise[2];
  int nb_pairwise = 1, nb_akm = 1;
  uint8_t group;
  uint8_t akm[2];
  uint16_t caps = 0;

  switch(security) {
    case SIM_SEC_WPA2_TKIP:
      group = RSN_CIPHER_TKIP; pairwise[0] = RSN_CIPHER_TKIP; akm[0] = RSN_AKM_PSK;
      break;
    case SIM_SEC_WPA2_CCMP:
      group = RSN_CIPHER_CCMP; pairwise[0] = RSN_CIPHER_CCMP; akm[0] = RSN_AKM_PSK;
      break;
    case SIM_SEC_WPA2_MIXED:
      group = RSN_CIPHER_TKIP;
      pairwise[0] = RSN_CIPHER_CCMP; pairwise[1] = RSN_CIPHER_TKIP; nb_pairwise = 2;
      akm[0] = RSN_AKM_PSK;
      break;
    case SIM_SEC_WPA3_SAE:
      group = RSN_CIPHER_CCMP; pairwise[0] = RSN_CIPHER_CCMP; akm[0] = RSN_AKM_SAE;
      caps = 0x00c0;   /* MFPR|MFPC: management frame protection required */
      break;
    case SIM_SEC_WPA2_WPA3:
      group = RSN_CIPHER_CCMP; pairwise[0] = RSN_CIPHER_CCMP;
      akm[0] = RSN_AKM_PSK; akm[1] = RSN_AKM_SAE; nb_akm = 2;
      caps = 0x0080;   /* MFPC: protection capable, not required */
      break;
    default:
      return p;
  }

  *b++ = 0x01; *b++ = 0x00;                       /* version 1 */
  memcpy(b, rsn_oui, 3); b += 3; *b++ = group;    /* group cipher suite */
  *b++ = nb_pairwise; *b++ = 0x00;
  for(int i=0;i<nb_pairwise;i++) { memcpy(b, rsn_oui, 3); b += 3; *b++ = pairwise[i]; }
  *b++ = nb_akm; *b++ = 0x00;
  for(int i=0;i<nb_akm;i++) { memcpy(b, rsn_oui, 3); b += 3; *b++ = akm[i]; }
  *b++ = caps & 0xff; *b++ = (caps >> 8) & 0xff;

  return put_ie(p, IEEE80211_ELEMID_RSN, body, (uint8_t)(b - body));
}

/** @brief The WPA1 vendor element, for an AP that predates RSN. */
static uint8_t *put_wpa_ie(uint8_t *p, int security)
{
  uint8_t body[32];
  uint8_t *b = body;

  if(security != SIM_SEC_WPA_TKIP) return p;

  memcpy(b, wpa_vendor, 4); b += 4;
  *b++ = 0x01; *b++ = 0x00;                         /* WPA version 1 */
  memcpy(b, wpa_oui, 3); b += 3; *b++ = RSN_CIPHER_TKIP;   /* group  */
  *b++ = 0x01; *b++ = 0x00;
  memcpy(b, wpa_oui, 3); b += 3; *b++ = RSN_CIPHER_TKIP;   /* pairwise */
  *b++ = 0x01; *b++ = 0x00;
  memcpy(b, wpa_oui, 3); b += 3; *b++ = RSN_AKM_PSK;       /* AKM */

  return put_ie(p, IEEE80211_ELEMID_VENDOR, body, (uint8_t)(b - body));
}

static int ap_privacy(const struct sim_ap *a)
{
  return a->security != SIM_SEC_OPEN;
}

/**
 * @brief Build a beacon or a probe response.
 *
 * The two differ only in the subtype and in who they are addressed to, which
 * is also how ieee80211_input_mgt() treats them: one case falls through to
 * the other.
 */
static uint32_t build_beacon(uint8_t *out, const struct sim_ap *a,
                             int probe_response, const uint8_t *dest)
{
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;
  uint8_t *p = out + sizeof(*fr);
  uint16_t capinfo = IEEE80211_CAPINFO_ESS | IEEE80211_CAPINFO_SHORT_PREAMBLE;
  static const uint8_t rates[8] = {0x82,0x84,0x8b,0x96,0x24,0x30,0x48,0x6c};
  static const uint8_t xrates[4] = {0x0c,0x12,0x18,0x60};

  if(ap_privacy(a)) capinfo |= IEEE80211_CAPINFO_PRIVACY;
  if(a->security >= SIM_SEC_WPA2_TKIP) capinfo |= IEEE80211_CAPINFO_RSN;

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_MGT
              | (probe_response ? IEEE80211_FC0_SUBTYPE_PROBE_RESP
                                : IEEE80211_FC0_SUBTYPE_BEACON);
  fr->i_fc[1] = IEEE80211_FC1_DIR_NODS;
  memcpy(fr->i_addr1, probe_response ? dest : ieee80211_broadcast_address,
         IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr2, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr3, a->bssid, IEEE80211_ADDR_LEN);

  /* Fixed fields: timestamp, beacon interval, capability information. The
   * firmware skips all twelve bytes without reading them, but a real AP sends
   * them and a capture of this transcript should look like a real one. */
  uint64_t tsf = (uint64_t)sim_now() * 1000;
  for(int i=0;i<8;i++) *p++ = (tsf >> (8*i)) & 0xff;
  *p++ = 0x64; *p++ = 0x00;                       /* 100 TU */
  *p++ = capinfo & 0xff; *p++ = (capinfo >> 8) & 0xff;

  p = put_ie(p, IEEE80211_ELEMID_SSID, a->ssid, (uint8_t)strlen(a->ssid));
  p = put_ie(p, IEEE80211_ELEMID_RATES, rates, sizeof(rates));
  p = put_ie(p, IEEE80211_ELEMID_DSPARMS, &a->channel, 1);
  p = put_ie(p, IEEE80211_ELEMID_XRATES, xrates, sizeof(xrates));
  p = put_rsn_ie(p, a->security);
  p = put_wpa_ie(p, a->security);

  return (uint32_t)(p - out);
}

static uint32_t build_auth_resp(uint8_t *out, const struct sim_ap *a,
                                uint16_t status)
{
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;
  uint8_t *p = out + sizeof(*fr);

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_MGT | IEEE80211_FC0_SUBTYPE_AUTH;
  fr->i_fc[1] = IEEE80211_FC1_DIR_NODS;
  memcpy(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr2, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr3, a->bssid, IEEE80211_ADDR_LEN);

  *p++ = 0x00; *p++ = 0x00;                     /* open system */
  *p++ = 0x02; *p++ = 0x00;                     /* sequence 2  */
  *p++ = status & 0xff; *p++ = (status >> 8) & 0xff;

  return (uint32_t)(p - out);
}

static uint32_t build_assoc_resp(uint8_t *out, const struct sim_ap *a,
                                 uint16_t status, uint16_t aid)
{
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;
  uint8_t *p = out + sizeof(*fr);
  uint16_t capinfo = IEEE80211_CAPINFO_ESS;
  static const uint8_t rates[8] = {0x82,0x84,0x8b,0x96,0x24,0x30,0x48,0x6c};

  if(ap_privacy(a)) capinfo |= IEEE80211_CAPINFO_PRIVACY;

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_MGT | IEEE80211_FC0_SUBTYPE_ASSOC_RESP;
  fr->i_fc[1] = IEEE80211_FC1_DIR_NODS;
  memcpy(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr2, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr3, a->bssid, IEEE80211_ADDR_LEN);

  *p++ = capinfo & 0xff; *p++ = (capinfo >> 8) & 0xff;
  *p++ = status & 0xff;  *p++ = (status >> 8) & 0xff;
  *p++ = aid & 0xff;     *p++ = (aid >> 8) & 0xff;
  p = put_ie(p, IEEE80211_ELEMID_RATES, rates, sizeof(rates));

  return (uint32_t)(p - out);
}

static uint32_t build_deauth(uint8_t *out, const struct sim_ap *a,
                             int disassoc, uint16_t reason)
{
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;
  uint8_t *p = out + sizeof(*fr);

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_MGT
              | (disassoc ? IEEE80211_FC0_SUBTYPE_DISASSOC
                          : IEEE80211_FC0_SUBTYPE_DEAUTH);
  fr->i_fc[1] = IEEE80211_FC1_DIR_NODS;
  memcpy(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr2, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr3, a->bssid, IEEE80211_ADDR_LEN);

  *p++ = reason & 0xff; *p++ = (reason >> 8) & 0xff;

  return (uint32_t)(p - out);
}

/* --- the four-way handshake, from the access point's side -------------- */

/* The pairwise master key, which both ends must already share: the rabbit
 * gets it through netAuth (eapol_init() copies ieee80211_key straight into
 * pmk, so the rabbit is configured with a PMK and not a passphrase), and the
 * harness derives it from the scenario's passphrase with the firmware's own
 * mypassword_to_pmk(). */
static uint8_t vap_pmk[EAPOL_MASTER_KEY_LENGTH];
static int vap_pmk_set;

enum {
  HS_IDLE,        /* nothing started                       */
  HS_SENT_MSG1,   /* waiting for message 2                 */
  HS_SENT_MSG3,   /* waiting for message 4                 */
  HS_DONE,
};

struct vap_handshake {
  int      stage;
  uint8_t  anonce[EAPOL_NONCE_LENGTH];
  uint8_t  snonce[EAPOL_NONCE_LENGTH];
  uint8_t  ptk[64];          /* kck[16] | kek[16] | tk[16] | mic[16] */
  uint64_t replay;
};

static struct vap_handshake vap_hs[SIM_MAX_APS];

/* A group key the AP hands out. Fixed rather than random so a transcript can
 * be compared byte for byte between runs. */
static const uint8_t vap_gtk[16] = {
  0x47,0x54,0x4b,0x00,0x11,0x22,0x33,0x44,
  0x55,0x66,0x77,0x88,0x99,0xaa,0xbb,0xcc
};

void vap_set_pmk(const uint8_t *pmk)
{
  memcpy(vap_pmk, pmk, EAPOL_MASTER_KEY_LENGTH);
  vap_pmk_set = 1;
}

/* The EAPOL-Key body is 95 bytes before the key data. The firmware's
 * `struct eapol_key_frame` declares a 24-byte key_data member inside itself,
 * so sizeof() is 24 too many for this purpose. */
#define EAPOL_KEY_BODY_LENGTH (sizeof(struct eapol_key_frame) - EAPOL_RSN_LENGTH)

static int vap_desc_ver(const struct sim_ap *a)
{
  /* 2 when the pairwise cipher is CCMP, so the MIC is HMAC-SHA1; 1 for TKIP,
   * where it is HMAC-MD5. eapol_input_msg1() reads this field to decide which
   * cipher the AP is actually using, so it has to be right. */
  switch(a->security) {
    case SIM_SEC_WPA_TKIP:
    case SIM_SEC_WPA2_TKIP:
      return 1;
    default:
      return 2;
  }
}

static int vap_is_wpa2(const struct sim_ap *a)
{
  return a->security >= SIM_SEC_WPA2_TKIP;
}

static void vap_put_replay(uint8_t *dst, uint64_t counter)
{
  for(int i = 0; i < EAPOL_RPC_LENGTH; i++)
    dst[EAPOL_RPC_LENGTH - 1 - i] = (uint8_t)(counter >> (8 * i));
}

/**
 * @brief Sign an EAPOL-Key frame the way the rabbit will check it.
 *
 * Over the EAPOL header and body — everything from the version byte — for
 * body_length + 4 bytes, with the MIC field zeroed. eapol_input_msg3() checks
 * it over exactly that span, so getting the length wrong here looks from the
 * rabbit's side like a wrong password.
 */
static void vap_sign(struct eapol_frame *fr, uint32_t body_length,
                     const uint8_t *kck, int desc_ver)
{
  uint8_t *eapol = (uint8_t *)fr + LLC_LENGTH;

  memset(fr->key_frame.key_mic, 0, EAPOL_KEYMIC_LENGTH);
  if(desc_ver == 2) {
    uint8_t full[20];
    hmac_sha1(kck, EAPOL_MICK_LENGTH, eapol, body_length + 4, full);
    memcpy(fr->key_frame.key_mic, full, EAPOL_KEYMIC_LENGTH);
  } else {
    hmac_md5(kck, EAPOL_MICK_LENGTH, eapol, body_length + 4,
             fr->key_frame.key_mic);
  }
}

/** @brief Wrap an EAPOL body in an 802.11 data frame from the AP. */
static uint32_t vap_wrap_data(uint8_t *out, const struct sim_ap *a,
                              const uint8_t *body, uint32_t body_length)
{
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_DATA | IEEE80211_FC0_SUBTYPE_DATA;
  fr->i_fc[1] = IEEE80211_FC1_DIR_FROMDS;
  /* From DS: addr1 is us, addr2 the BSSID, addr3 the original sender.
   * ieee80211_input_data() refuses anything else in managed mode. */
  memcpy(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr2, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(fr->i_addr3, a->bssid, IEEE80211_ADDR_LEN);
  memcpy(out + sizeof(*fr), body, body_length);
  return (uint32_t)sizeof(*fr) + body_length;
}

static void vap_send_msg1(int ap)
{
  struct sim_ap *a = &sim.aps[ap];
  struct vap_handshake *hs = &vap_hs[ap];
  uint8_t body[sizeof(struct eapol_frame) + 64];
  uint8_t out[VAP_FRAME_MAX];
  struct eapol_frame *fr = (struct eapol_frame *)body;
  uint32_t body_length = (uint32_t)EAPOL_KEY_BODY_LENGTH;
  uint32_t n;

  if(!vap_pmk_set) {
    sim_log("AP %s: no PMK, cannot start a handshake", macstr(a->bssid));
    sim_note_failure("the harness never handed the virtual AP a PMK");
    return;
  }

  memset(body, 0, sizeof(body));
  /* An ANonce that is distinguishable in a dump and stable across runs. */
  for(uint32_t i = 0; i < EAPOL_NONCE_LENGTH; i++)
    hs->anonce[i] = (uint8_t)(0xa0 + i);
  hs->replay = 1;

  memcpy(fr->llc, eapol_llc, LLC_LENGTH);
  fr->protocol_version = vap_is_wpa2(a) ? EAPOL_VERSION2 : EAPOL_VERSION;
  fr->packet_type = EAPOL_TYPE_KEY;
  fr->body_length[0] = (uint8_t)((body_length >> 8) & 0xff);
  fr->body_length[1] = (uint8_t)(body_length & 0xff);

  fr->key_frame.descriptor_type =
      vap_is_wpa2(a) ? EAPOL_DTYPE_WPA2KEY : EAPOL_DTYPE_WPAKEY;
  fr->key_frame.key_info.key_desc_ver = vap_desc_ver(a);
  fr->key_frame.key_info.key_type = 1;      /* pairwise */
  fr->key_frame.key_info.key_ack  = 1;
  fr->key_frame.key_length[0] = 0;
  fr->key_frame.key_length[1] = 16;
  vap_put_replay(fr->key_frame.replay_counter, hs->replay);
  memcpy(fr->key_frame.key_nonce, hs->anonce, EAPOL_NONCE_LENGTH);
  /* No MIC on message 1: the station has no PTK yet. */

  n = vap_wrap_data(out, a, body, LLC_LENGTH + 4 + body_length);
  sim_log("AP %s: EAPOL 1/4, ANonce a0..bf, key_desc_ver %d",
          macstr(a->bssid), vap_desc_ver(a));
  queue_frame(out, n, a->rssi, a->channel);
  vap_stats.eapol_to_sta++;
  hs->stage = HS_SENT_MSG1;
}

static void vap_send_msg3(int ap)
{
  struct sim_ap *a = &sim.aps[ap];
  struct vap_handshake *hs = &vap_hs[ap];
  uint8_t body[sizeof(struct eapol_frame) + 128];
  uint8_t out[VAP_FRAME_MAX];
  struct eapol_frame *fr = (struct eapol_frame *)body;
  uint8_t *key_data = (uint8_t *)&fr->key_frame + EAPOL_KEY_BODY_LENGTH;
  uint32_t key_data_length = 0;
  uint32_t body_length;
  uint32_t n;

  memset(body, 0, sizeof(body));
  hs->replay = 2;

  if(vap_is_wpa2(a)) {
    /* WPA2 delivers the group key in message 3, as a KDE inside the key
     * data, wrapped with the KEK. This is the path crypto/aes128.c and the
     * wpa2_gtk modification exist for, and it was previously only reachable
     * by flashing a rabbit and watching whether broadcast traffic arrived. */
    uint8_t kde[24];
    uint8_t *p = kde;
    *p++ = 0xdd;
    *p++ = 0x16;                                  /* 4 OUI + 2 + 16 */
    *p++ = 0x00; *p++ = 0x0f; *p++ = 0xac;        /* RSN OUI */
    *p++ = 0x01;                                  /* data type 1: GTK */
    *p++ = 0x01;                                  /* key id 1 */
    *p++ = 0x00;                                  /* reserved */
    memcpy(p, vap_gtk, sizeof(vap_gtk));

    if(!vap_aes_key_wrap(hs->ptk + 16, kde, sizeof(kde), key_data)) {
      sim_note_failure("the AP could not wrap the group key");
      return;
    }
    key_data_length = sizeof(kde) + 8;
  }

  body_length = (uint32_t)EAPOL_KEY_BODY_LENGTH + key_data_length;

  memcpy(fr->llc, eapol_llc, LLC_LENGTH);
  fr->protocol_version = vap_is_wpa2(a) ? EAPOL_VERSION2 : EAPOL_VERSION;
  fr->packet_type = EAPOL_TYPE_KEY;
  fr->body_length[0] = (uint8_t)((body_length >> 8) & 0xff);
  fr->body_length[1] = (uint8_t)(body_length & 0xff);

  fr->key_frame.descriptor_type =
      vap_is_wpa2(a) ? EAPOL_DTYPE_WPA2KEY : EAPOL_DTYPE_WPAKEY;
  fr->key_frame.key_info.key_desc_ver = vap_desc_ver(a);
  fr->key_frame.key_info.key_type = 1;
  fr->key_frame.key_info.install  = 1;
  fr->key_frame.key_info.key_ack  = 1;
  fr->key_frame.key_info.key_mic  = 1;
  fr->key_frame.key_info.secure   = 1;
  fr->key_frame.key_info.ekd      = vap_is_wpa2(a) ? 1 : 0;
  fr->key_frame.key_length[0] = 0;
  fr->key_frame.key_length[1] = 16;
  vap_put_replay(fr->key_frame.replay_counter, hs->replay);
  memcpy(fr->key_frame.key_nonce, hs->anonce, EAPOL_NONCE_LENGTH);
  key_data[-2] = (uint8_t)((key_data_length >> 8) & 0xff);
  key_data[-1] = (uint8_t)(key_data_length & 0xff);

  vap_sign(fr, body_length, hs->ptk, vap_desc_ver(a));

  n = vap_wrap_data(out, a, body, LLC_LENGTH + 4 + body_length);
  sim_log("AP %s: EAPOL 3/4, %u bytes of key data%s", macstr(a->bssid),
          key_data_length, key_data_length ? " (wrapped GTK)" : "");
  queue_frame(out, n, a->rssi, a->channel);
  vap_stats.eapol_to_sta++;
  hs->stage = HS_SENT_MSG3;
}

/**
 * @brief An EAPOL-Key frame arriving from the rabbit.
 *
 * Message 2 carries the SNonce, which is the last thing the AP needs to
 * derive the PTK; from there the MIC on the rabbit's own frame can be
 * verified, and a mismatch is reported rather than ignored, because that is
 * the difference between "the password is wrong" and "the derivation is".
 */
static void vap_eapol_from_sta(int ap, const uint8_t *payload, uint32_t length)
{
  struct sim_ap *a = &sim.aps[ap];
  struct vap_handshake *hs = &vap_hs[ap];
  const struct eapol_frame *fr = (const struct eapol_frame *)payload;
  uint32_t body_length;

  if(length < LLC_LENGTH + 4 + EAPOL_KEY_BODY_LENGTH) {
    sim_log("AP %s: a %u-byte EAPOL frame, too short to be a key message",
            macstr(a->bssid), length);
    return;
  }
  body_length = ((uint32_t)fr->body_length[0] << 8) | fr->body_length[1];

  if(hs->stage == HS_SENT_MSG1) {
    uint8_t mine[20];
    uint8_t received[EAPOL_KEYMIC_LENGTH];
    uint8_t copy[512];
    struct eapol_frame *scratch;

    memcpy(hs->snonce, fr->key_frame.key_nonce, EAPOL_NONCE_LENGTH);
    vap_compute_ptk(vap_pmk, hs->anonce, a->bssid, hs->snonce, rt2501_mac,
                    hs->ptk, EAPOL_PTK_LENGTH);
    sim_log("AP %s: EAPOL 2/4 received, PTK derived (KCK %02x%02x%02x%02x...)",
            macstr(a->bssid), hs->ptk[0], hs->ptk[1], hs->ptk[2], hs->ptk[3]);

    /* Verify the rabbit's MIC over its own frame, with the MIC zeroed. The
     * span is body_length + 4, which the firmware computes from the length
     * IT declared — and that length counts the key_data member twice. Using
     * the rabbit's own arithmetic is the point: we are checking that it
     * agrees with itself, not that it agrees with the standard. */
    if(length > sizeof(copy)) {
      sim_log("AP %s: EAPOL 2/4 is %u bytes, too long to verify", macstr(a->bssid), length);
    } else {
      memcpy(copy, payload, length);
      scratch = (struct eapol_frame *)copy;
      memcpy(received, scratch->key_frame.key_mic, EAPOL_KEYMIC_LENGTH);
      memset(scratch->key_frame.key_mic, 0, EAPOL_KEYMIC_LENGTH);
      if(LLC_LENGTH + body_length + 4 > length) {
        sim_log("AP %s: EAPOL 2/4 declares %u body bytes but carries %u — "
                "not verifying", macstr(a->bssid), body_length,
                length - LLC_LENGTH - 4);
      } else {
        if(vap_desc_ver(a) == 2) {
          hmac_sha1(hs->ptk, EAPOL_MICK_LENGTH, copy + LLC_LENGTH,
                    body_length + 4, mine);
        } else {
          hmac_md5(hs->ptk, EAPOL_MICK_LENGTH, copy + LLC_LENGTH,
                   body_length + 4, mine);
        }
        if(memcmp(mine, received, EAPOL_KEYMIC_LENGTH) == 0) {
          sim_log("AP %s: the rabbit's MIC on 2/4 is correct", macstr(a->bssid));
          vap_stats.mics_verified++;
        } else {
          sim_log("AP %s: the rabbit's MIC on 2/4 does not match", macstr(a->bssid));
          vap_stats.mics_failed++;
        }
      }
    }

    vap_send_msg3(ap);
    return;
  }

  if(hs->stage == HS_SENT_MSG3) {
    hs->stage = HS_DONE;
    sim_log("AP %s: EAPOL 4/4 received, the handshake is complete",
            macstr(a->bssid));
    vap_stats.handshakes_completed++;
    return;
  }

  sim_log("AP %s: an EAPOL frame with no handshake in progress",
          macstr(a->bssid));
}

/* --- the station's frames, arriving at the AP -------------------------- */

static void ap_handle_mgt(int ap, const struct ieee80211_frame *fr,
                          const uint8_t *frame, uint32_t length)
{
  struct sim_ap *a = &sim.aps[ap];
  uint8_t out[VAP_FRAME_MAX];
  uint32_t n;
  uint8_t subtype = fr->i_fc[0] & IEEE80211_FC0_SUBTYPE_MASK;

  switch(subtype) {
    case IEEE80211_FC0_SUBTYPE_PROBE_REQ: {
      /* Answer only if the SSID matches or is the wildcard. */
      const uint8_t *ie = frame + sizeof(*fr);
      const uint8_t *end = frame + length;
      int matched = 1;
      while(ie + 2 <= end) {
        if(ie[0] == IEEE80211_ELEMID_SSID && ie[1] > 0) {
          matched = (ie[1] == strlen(a->ssid))
                 && (memcmp(ie + 2, a->ssid, ie[1]) == 0);
        }
        ie += ie[1] + 2;
      }
      if(!matched) return;
      n = build_beacon(out, a, 1, rt2501_mac);
      sim_log("AP %s: probe response, SSID \"%s\", %s",
              macstr(a->bssid), a->ssid, sim_security_name(a->security));
      queue_frame(out, n, a->rssi, a->channel);
      vap_stats.probe_responses++;
      break;
    }

    case IEEE80211_FC0_SUBTYPE_AUTH: {
      const uint8_t *body = frame + sizeof(*fr);
      uint16_t algorithm, seq;
      if(length < sizeof(*fr) + 6) return;
      algorithm = body[0] | (body[1] << 8);
      seq       = body[2] | (body[3] << 8);
      if(seq != 1) return;             /* only the station's first message */
      if(algorithm != IEEE80211_AUTH_ALG_OPEN && a->security != SIM_SEC_WEP) {
        sim_log("AP %s: refusing auth algorithm %u", macstr(a->bssid), algorithm);
        n = build_auth_resp(out, a, IEEE80211_STATUS_ALG);
        queue_frame(out, n, a->rssi, a->channel);
        return;
      }
      a->authenticated = 1;
      sim_log("AP %s: auth ok (algorithm %u)", macstr(a->bssid), algorithm);
      n = build_auth_resp(out, a, IEEE80211_STATUS_SUCCESS);
      queue_frame(out, n, a->rssi, a->channel);
      vap_stats.auth_responses++;
      break;
    }

    case IEEE80211_FC0_SUBTYPE_ASSOC_REQ:
    case IEEE80211_FC0_SUBTYPE_REASSOC_REQ: {
      if(!a->authenticated) {
        sim_log("AP %s: assoc without auth, refused", macstr(a->bssid));
        n = build_deauth(out, a, 0, IEEE80211_REASON_ASSOC_NOT_AUTHED);
        queue_frame(out, n, a->rssi, a->channel);
        return;
      }
      a->associated = 1;
      a->assoc_id = (uint32_t)ap + 1;
      sim_log("AP %s: associated, aid %u", macstr(a->bssid), a->assoc_id);
      n = build_assoc_resp(out, a, IEEE80211_STATUS_SUCCESS, (uint16_t)a->assoc_id);
      queue_frame(out, n, a->rssi, a->channel);
      vap_stats.assoc_responses++;

      if(a->security >= SIM_SEC_WPA_TKIP) {
        /* An AP sends message 1 as soon as the station is associated. */
        vap_hs[ap].stage = HS_IDLE;
        vap_send_msg1(ap);
      }
      break;
    }

    case IEEE80211_FC0_SUBTYPE_DEAUTH:
    case IEEE80211_FC0_SUBTYPE_DISASSOC:
      a->associated = 0;
      a->authenticated = 0;
      sim_log("AP %s: the rabbit %s", macstr(a->bssid),
              subtype == IEEE80211_FC0_SUBTYPE_DEAUTH ? "deauthenticated"
                                                      : "disassociated");
      break;

    default:
      break;
  }
}

static void ap_handle_data(int ap, const struct ieee80211_frame *fr,
                           const uint8_t *frame, uint32_t length)
{
  struct sim_ap *a = &sim.aps[ap];
  const uint8_t *payload = frame + sizeof(*fr);
  uint32_t payload_length = length - sizeof(*fr);

  if(!a->associated) {
    sim_log("AP %s: data frame from an unassociated station, deauthenticating",
            macstr(a->bssid));
    uint8_t out[VAP_FRAME_MAX];
    uint32_t n = build_deauth(out, a, 0, IEEE80211_REASON_NOT_ASSOCED);
    queue_frame(out, n, a->rssi, a->channel);
    return;
  }

  if(payload_length >= LLC_LENGTH
     && memcmp(payload, eapol_llc, LLC_LENGTH) == 0) {
    vap_stats.eapol_from_sta++;
    if(vap_eapol_drops > 0) {
      vap_eapol_drops--;
      sim_log("AP %s: swallowing an EAPOL frame (%d more to go)",
              macstr(a->bssid), vap_eapol_drops);
      return;
    }
    vap_eapol_from_sta(ap, payload, payload_length);
    return;
  }

  vap_stats.data_from_sta++;
}

/* --- the radio API the firmware calls --------------------------------- */

int32_t rt2501_driver_install(void)
{
  vap_radio_up = 1;
  DBG_WIFI("RT2501 virtual radio installed (testvm)"EOL);
  return OK;
}

int32_t rt2501_state(void)
{
  /* Mirrors src/usb/rt2501usb.c exactly, so a test reads the same state the
   * bytecode reads through netState(). */
  if(!vap_radio_up)
    return RT2501_S_BROKEN;
  if(ieee80211_mode == IEEE80211_M_MASTER)
    return RT2501_S_MASTER;
  switch(ieee80211_state) {
    case IEEE80211_S_IDLE:  return RT2501_S_IDLE;
    case IEEE80211_S_SCAN:  return RT2501_S_SCAN;
    case IEEE80211_S_AUTH:
    case IEEE80211_S_ASSOC:
    case IEEE80211_S_EAPOL: return RT2501_S_CONNECTING;
    case IEEE80211_S_RUN:   return RT2501_S_CONNECTED;
    default:                return RT2501_S_BROKEN;
  }
}

void rt2501_timer(void)
{
  if(!vap_radio_up) return;
  ieee80211_timer();
}

void rt2501_switch_channel(uint8_t channel)
{
  if(channel != vap_channel) {
    vap_channel = channel;
    vap_stats.channel_switches++;
  }
}

uint8_t rt2501_set_bssid(const uint8_t *bssid)
{
  memcpy(vap_filter_bssid, bssid, IEEE80211_ADDR_LEN);
  return 1;
}

void rt2501_make_tx_descriptor(PTXD_STRUC txd, uint8_t CipherAlg,
                               uint8_t KeyTable, uint8_t KeyIdx, uint8_t Ack,
                               uint8_t Fragment, uint8_t InsTimestamp,
                               uint8_t RetryMode, uint8_t Ifs, uint32_t Rate,
                               uint32_t Length, uint8_t QueIdx,
                               uint8_t PacketId)
{
  /* The descriptor only matters here for its length: rt2501_tx() is handed
   * descriptor plus frame and has to know where one ends. */
  memset((void *)txd, 0, sizeof(*txd));
  txd->CipherAlg = CipherAlg;
  txd->KeyTable  = KeyTable;
  txd->KeyIndex  = KeyIdx;
  txd->ACK       = Ack;
  txd->DataByteCnt = Length;
  (void)Fragment; (void)InsTimestamp; (void)RetryMode; (void)Ifs;
  (void)Rate; (void)QueIdx; (void)PacketId;
}

int8_t rt2501_tx(void *buffer, uint32_t length)
{
  const uint8_t *frame;
  uint32_t frame_length;
  const struct ieee80211_frame *fr;
  int ap;

  if(!vap_radio_up) return 0;
  if(length <= sizeof(TXD_STRUC)) return 0;

  frame = (const uint8_t *)buffer + sizeof(TXD_STRUC);
  frame_length = length - sizeof(TXD_STRUC);
  if(frame_length < sizeof(struct ieee80211_frame)) return 0;
  fr = (const struct ieee80211_frame *)frame;

  vap_stats.tx_frames++;

  /* Which AP, if any, is listening? addr1 is the immediate recipient; a
   * broadcast probe request is heard by every audible AP. */
  if(IEEE80211_IS_MULTICAST(fr->i_addr1)) {
    for(ap = 0; ap < sim.nb_aps; ap++)
      if(ap_audible(&sim.aps[ap]))
        ap_handle_mgt(ap, fr, frame, frame_length);
    return 1;
  }

  ap = ap_index_for_bssid(fr->i_addr1);
  if(ap < 0) {
    sim_log("vap: a frame for %s, which is nobody's BSSID", macstr(fr->i_addr1));
    return 1;
  }
  if(!ap_audible(&sim.aps[ap])) {
    /* Transmitted into the void. The hardware would report the missing ACK;
     * the firmware's own retry and timeout logic is what we are watching. */
    vap_stats.tx_unheard++;
    return 1;
  }

  switch(fr->i_fc[0] & IEEE80211_FC0_TYPE_MASK) {
    case IEEE80211_FC0_TYPE_MGT:
      ap_handle_mgt(ap, fr, frame, frame_length);
      break;
    case IEEE80211_FC0_TYPE_DATA:
      ap_handle_data(ap, fr, frame, frame_length);
      break;
    default:
      break;
  }
  return 1;
}

uint8_t rt2501_beacon(void *buffer, uint32_t length)
{
  (void)buffer; (void)length;
  return 1;     /* master mode: the rabbit's own beacon goes nowhere useful */
}

int32_t rt2501_set_key(uint8_t index, uint8_t *key, uint8_t *txmic,
                       uint8_t *rxmic, uint8_t cipher)
{
  (void)key; (void)txmic; (void)rxmic;
  sim_log("radio: key %u installed, cipher %u", index, cipher);
  vap_stats.keys_installed++;
  return OK;
}

uint16_t rt2501_txtime(uint32_t len, uint8_t rate)
{
  /* Microseconds on the air. The firmware only puts this in the duration
   * field, so an honest approximation is enough: DSSS preamble plus payload
   * at the rate given in half-megabit units. */
  if(rate == 0) rate = 2;
  return (uint16_t)(192 + (len * 8 * 2) / rate);
}

/* --- the simulation's own entry points -------------------------------- */

void vap_init(void)
{
  vap_radio_up = 1;
  vap_channel = sim.aps[0].channel;
  vap_queue_head = vap_queue_count = 0;
  vap_next_beacon_ms = 0;
  vap_eapol_drops = 0;
  memset(&vap_stats, 0, sizeof(vap_stats));
  memset(vap_hs, 0, sizeof(vap_hs));

  sim_log("radio: MAC %s, %d AP%s declared", macstr(rt2501_mac), sim.nb_aps,
          sim.nb_aps == 1 ? "" : "s");
  for(int i=0;i<sim.nb_aps;i++)
    sim_log("  AP %s  SSID \"%s\"  channel %u  %s  %d dBm%s",
            macstr(sim.aps[i].bssid), sim.aps[i].ssid, sim.aps[i].channel,
            sim_security_name(sim.aps[i].security), sim.aps[i].rssi,
            sim.aps[i].pmf ? "  (PMF)" : "");
}

void vap_deauth(int ap, int disassoc, uint16_t reason)
{
  uint8_t out[VAP_FRAME_MAX];
  uint32_t n;
  struct sim_ap *a;

  for(int i=0;i<sim.nb_aps;i++) {
    if(ap >= 0 && i != ap) continue;
    a = &sim.aps[i];
    if(!a->up) continue;
    if(a->pmf && !disassoc) {
      /* 802.11w: a station that negotiated management frame protection
       * ignores an unprotected deauthentication. Modelled so a WPA3 scenario
       * does not silently "work" for the wrong reason. */
      sim_log("AP %s: PMF is on, an unprotected deauth would be ignored — "
              "not sending it", macstr(a->bssid));
      continue;
    }
    n = build_deauth(out, a, disassoc, reason);
    sim_log("AP %s: sending %s, reason %u", macstr(a->bssid),
            disassoc ? "disassociation" : "deauthentication", reason);
    queue_frame(out, n, a->rssi, a->channel);
    a->associated = 0;
    a->authenticated = 0;
    memset(&vap_hs[i], 0, sizeof(vap_hs[i]));
    vap_stats.deauths_sent++;
  }
}

void vap_set_up(int ap, int up)
{
  for(int i=0;i<sim.nb_aps;i++) {
    if(ap >= 0 && i != ap) continue;
    sim.aps[i].up = up;
    if(!up) {
      sim.aps[i].associated = 0;
      sim.aps[i].authenticated = 0;
      memset(&vap_hs[i], 0, sizeof(vap_hs[i]));
    }
    sim_log("AP %s: %s", macstr(sim.aps[i].bssid), up ? "powered on" : "powered off");
  }
}

void vap_drop_eapol(int count)
{
  vap_eapol_drops = count;
  sim_log("radio: the next %d EAPOL frame%s will be swallowed", count,
          count == 1 ? "" : "s");
}

/**
 * @brief One pass of the radio: beacons out, queued frames in.
 *
 * Called from the main loop, once per virtual tick.
 */
void vap_poll(void)
{
  if(!vap_radio_up) return;

  /* Beacons, every 100 ms of virtual time, from every audible AP. This is
   * what keeps a connected rabbit from deciding the AP has gone: see
   * IEEE80211_RUN_TIMEOUT in ieee80211_timer(). */
  if(sim_now() >= vap_next_beacon_ms) {
    uint8_t out[VAP_FRAME_MAX];
    vap_next_beacon_ms = sim_now() + 100;
    for(int i=0;i<sim.nb_aps;i++) {
      if(!ap_audible(&sim.aps[i])) continue;
      uint32_t n = build_beacon(out, &sim.aps[i], 0, NULL);
      queue_frame(out, n, sim.aps[i].rssi, sim.aps[i].channel);
      vap_stats.beacons_sent++;
    }
  }

  /* Deliver what is queued, as the USB receive callback would. */
  while(vap_queue_count > 0) {
    struct vap_queued *q = &vap_queue[vap_queue_head];
    vap_queue_head = (vap_queue_head + 1) % VAP_QUEUE_DEPTH;
    vap_queue_count--;
    if(q->channel != vap_channel) continue;   /* the radio moved on */
    vap_stats.rx_frames++;
    ieee80211_input(q->data, q->length, q->rssi);
  }
}
