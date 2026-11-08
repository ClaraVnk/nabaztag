/**
 * @file phone.c
 * @brief The phone a rabbit in config mode is configured from.
 *
 * Hold the head button at power-on and a Nabaztag does not look for a
 * network: it becomes one. `netSetmode IEEE80211_M_MASTER` turns the radio
 * into an open access point called "NabaztagXX", the bytecode starts a DHCP
 * server and an HTTP server on 192.168.0.1, and whoever joins and browses
 * there gets the configuration pages — among them `/u`, the upgrade page,
 * whose form POSTs a .sim to `/c` and `httpflash`.
 *
 * Nothing in the emulator could join that network, so the bootloader's
 * config mode had never run anywhere but on a rabbit. This is the station
 * that joins it: open-system authentication, association, then a TCP client
 * that uploads a file to the upgrade page the way a browser does, as a
 * multipart/form-data POST cut into segments the rabbit's own TCP window
 * (800 bytes) allows, one in flight at a time, each one acknowledged before
 * the next goes out. The medium does not lose frames, so one segment at a
 * time is also what a real phone ends up doing against a window that small.
 *
 * Everything the rabbit does with the upload is then the real path:
 * `tcpread` in the bytecode's HTTP server collects the segments until
 * Content-Length is reached, `cbhttp` routes `/c` to `httpflash`,
 * `getfirmware` turns the hex back into an image, and -- on an image with
 * the `bootloader` step -- `verifySig` decides before `flashFirmware`. What
 * this file is for is the question none of the OTA scenarios could answer:
 * whether that path has the memory to take an image of a given size, since
 * the hex form of a 118 KB image is 237 KB and the heap is 800.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "net/ieee80211.h"
#include "usb/rt2501usb.h"

#include "sim.h"
#include "vap.h"
#include "phone.h"

struct phone_stats phone_stats;

/* --- who the phone is ------------------------------------------------- */

static const uint8_t phone_mac[6] = {0x02, 0x00, 0x5e, 0x20, 0x00, 0x01};
static const uint32_t phone_ip = 0xc0a80002;     /* 192.168.0.2 */
static const uint32_t rabbit_ip = 0xc0a80001;    /* 192.168.0.1, netip_master */
static const uint16_t rabbit_port = 80;

static const uint8_t llc_snap[6] = {0xaa, 0xaa, 0x03, 0x00, 0x00, 0x00};
#define LLC_SNAP_LENGTH 8
#define ETHERTYPE_IP    0x0800
#define ETHERTYPE_ARP   0x0806

/* The rabbit's network, as the beacon announced it. */
static int     net_seen;
static uint8_t bssid[6];
static char    ssid[33];

enum { ST_OFF, ST_AUTH_SENT, ST_ASSOC_SENT, ST_JOINED };
static int      sta_state;
static uint32_t sta_sent_ms;

/* --- what it is uploading --------------------------------------------- */

static char     upload_path[512];
static uint32_t segment_size = 800;
static int      upload_wanted;      /* a scenario asked for it */

void phone_set_upload(const char *path)
{
  snprintf(upload_path, sizeof(upload_path), "%s", path ? path : "");
}

void phone_set_segment(uint32_t bytes)
{
  if(bytes >= 1 && bytes <= 1400) segment_size = bytes;
}

/* --- byte order ------------------------------------------------------- */

static uint16_t rd16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t rd32(const uint8_t *p)
{
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
       | ((uint32_t)p[2] << 8) | p[3];
}
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static void wr32(uint8_t *p, uint32_t v)
{
  p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16);
  p[2] = (uint8_t)(v >> 8);  p[3] = (uint8_t)v;
}

static uint16_t checksum(const uint8_t *data, uint32_t length, uint32_t seed)
{
  uint32_t sum = seed;
  uint32_t i;

  for(i = 0; i + 1 < length; i += 2)
    sum += ((uint32_t)data[i] << 8) | data[i + 1];
  if(i < length)
    sum += (uint32_t)data[i] << 8;
  while(sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
  return (uint16_t)~sum;
}

/* --- 802.11, station side --------------------------------------------- */

#define PHONE_FRAME_MAX 1600

static void send_mgt(uint8_t subtype, const uint8_t *body, uint32_t length)
{
  uint8_t out[PHONE_FRAME_MAX];
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;

  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_MGT | subtype;
  fr->i_fc[1] = IEEE80211_FC1_DIR_NODS;
  memcpy(fr->i_addr1, bssid, 6);
  memcpy(fr->i_addr2, phone_mac, 6);
  memcpy(fr->i_addr3, bssid, 6);
  memcpy(out + sizeof(*fr), body, length);
  vap_inject(out, (uint32_t)sizeof(*fr) + length);
}

/** @brief An Ethernet payload to the rabbit, To DS, as a station sends it. */
static void send_data(const uint8_t *dest_mac, uint16_t ethertype,
                      const uint8_t *packet, uint32_t length)
{
  uint8_t out[PHONE_FRAME_MAX];
  struct ieee80211_frame *fr = (struct ieee80211_frame *)out;
  uint8_t *p = out + sizeof(*fr);

  if(sizeof(*fr) + LLC_SNAP_LENGTH + length > sizeof(out)) {
    sim_log("phone: a %u-byte packet will not fit in a frame", length);
    return;
  }
  memset(fr, 0, sizeof(*fr));
  fr->i_fc[0] = IEEE80211_FC0_TYPE_DATA | IEEE80211_FC0_SUBTYPE_DATA;
  fr->i_fc[1] = IEEE80211_FC1_DIR_TODS;
  memcpy(fr->i_addr1, bssid, 6);          /* the AP */
  memcpy(fr->i_addr2, phone_mac, 6);      /* us */
  memcpy(fr->i_addr3, dest_mac, 6);       /* the destination behind it */
  memcpy(p, llc_snap, 6);
  wr16(p + 6, ethertype);
  memcpy(p + LLC_SNAP_LENGTH, packet, length);
  vap_inject(out, (uint32_t)(sizeof(*fr) + LLC_SNAP_LENGTH + length));
}

static void send_auth_request(void)
{
  uint8_t body[6];
  /* Algorithm 0 (open), sequence 1, status 0: little-endian, as 802.11 is. */
  body[0] = 0; body[1] = 0;
  body[2] = 1; body[3] = 0;
  body[4] = 0; body[5] = 0;
  send_mgt(IEEE80211_FC0_SUBTYPE_AUTH, body, sizeof(body));
  sta_state = ST_AUTH_SENT;
  sta_sent_ms = sim_now();
  sim_log("phone: authenticating to \"%s\"", ssid);
}

static void send_assoc_request(void)
{
  uint8_t body[64];
  uint8_t *p = body;
  uint8_t n = (uint8_t)strlen(ssid);
  static const uint8_t rates[4] = {0x82, 0x84, 0x8b, 0x96};

  *p++ = 0x01; *p++ = 0x00;               /* capability: ESS */
  *p++ = 0x0a; *p++ = 0x00;               /* listen interval */
  *p++ = IEEE80211_ELEMID_SSID; *p++ = n; memcpy(p, ssid, n); p += n;
  *p++ = IEEE80211_ELEMID_RATES; *p++ = 4; memcpy(p, rates, 4); p += 4;
  send_mgt(IEEE80211_FC0_SUBTYPE_ASSOC_REQ, body, (uint32_t)(p - body));
  sta_state = ST_ASSOC_SENT;
  sta_sent_ms = sim_now();
}

/* --- IP, the phone's end ---------------------------------------------- */

static void send_ip(uint8_t protocol, uint8_t *payload, uint32_t length)
{
  uint8_t packet[PHONE_FRAME_MAX];
  static uint16_t ident = 0x4000;
  uint32_t seed;
  uint16_t sum;

  if(20 + length > sizeof(packet)) return;
  packet[0] = 0x45; packet[1] = 0;
  wr16(packet + 2, (uint16_t)(20 + length));
  wr16(packet + 4, ++ident);
  wr16(packet + 6, 0x4000);               /* don't fragment */
  packet[8] = 64; packet[9] = protocol;
  wr16(packet + 10, 0);
  wr32(packet + 12, phone_ip);
  wr32(packet + 16, rabbit_ip);
  memcpy(packet + 20, payload, length);

  seed = (phone_ip >> 16) + (phone_ip & 0xffff) + (rabbit_ip >> 16)
       + (rabbit_ip & 0xffff) + protocol + length;
  sum = checksum(packet + 20, length, seed);
  wr16(packet + 20 + 16, sum == 0 ? 0xffff : sum);   /* TCP only, here */
  wr16(packet + 10, checksum(packet, 20, 0));
  send_data(bssid, ETHERTYPE_IP, packet, 20 + length);
}

static void handle_arp(const uint8_t *arp, uint32_t length)
{
  uint8_t reply[28];

  if(length < 28) return;
  if(rd16(arp) != 1 || rd16(arp + 2) != ETHERTYPE_IP) return;
  if(rd16(arp + 6) != 1) return;          /* requests only */
  if(rd32(arp + 24) != phone_ip) return;  /* for us */

  wr16(reply, 1); wr16(reply + 2, ETHERTYPE_IP);
  reply[4] = 6; reply[5] = 4;
  wr16(reply + 6, 2);
  memcpy(reply + 8, phone_mac, 6);  wr32(reply + 14, phone_ip);
  memcpy(reply + 18, arp + 8, 6);   memcpy(reply + 24, arp + 14, 4);
  send_data(arp + 8, ETHERTYPE_ARP, reply, sizeof(reply));
  phone_stats.arp_answered++;
}

/* --- TCP: one client connection, one request --------------------------- */

enum {
  TCP_CLOSED,
  TCP_SYN_SENT,
  TCP_SENDING,        /* the request is going out, one segment in flight */
  TCP_RECEIVING,      /* everything sent, reading the answer */
  TCP_FIN_WAIT,       /* we answered the rabbit's FIN with ours */
  TCP_DONE,
};

#define TCP_RETRANSMIT_MS 3000
#define RESPONSE_MAX      65536

static struct {
  int      state;
  uint16_t port;
  uint32_t iss;        /* initial sequence number */
  uint32_t snd_una;    /* oldest unacknowledged byte */
  uint32_t snd_nxt;
  uint32_t rcv_nxt;
  uint8_t *request;
  uint32_t request_length;
  uint32_t sent;       /* bytes of the request acknowledged */
  uint32_t in_flight;  /* bytes of the segment waiting for its ACK */
  uint32_t last_tx_ms;
  uint8_t  response[RESPONSE_MAX];
  uint32_t response_length;
} tcp;

#define TCP_FIN 0x01
#define TCP_SYN 0x02
#define TCP_RST 0x04
#define TCP_PSH 0x08
#define TCP_ACK 0x10

static void tcp_send(uint8_t flags, uint32_t seq, const uint8_t *data, uint32_t length)
{
  uint8_t segment[20 + 1400];

  if(length > 1400) length = 1400;
  wr16(segment, tcp.port);
  wr16(segment + 2, rabbit_port);
  wr32(segment + 4, seq);
  wr32(segment + 8, (flags & TCP_ACK) ? tcp.rcv_nxt : 0);
  segment[12] = 0x50;
  segment[13] = flags;
  wr16(segment + 14, 8192);
  wr16(segment + 16, 0);
  wr16(segment + 18, 0);
  if(length) memcpy(segment + 20, data, length);
  send_ip(6, segment, 20 + length);
  tcp.last_tx_ms = sim_now();
}

static void tcp_send_next_segment(int resend)
{
  uint32_t n = tcp.request_length - tcp.sent;

  if(n == 0) return;
  if(n > segment_size) n = segment_size;
  tcp.in_flight = n;
  tcp_send(TCP_ACK | TCP_PSH, tcp.snd_una, tcp.request + tcp.sent, n);
  tcp.snd_nxt = tcp.snd_una + n;
  if(resend) phone_stats.segments_resent++;
  else {
    phone_stats.segments_sent++;
    phone_stats.bytes_sent += n;
  }
}

static int tcp_open(void)
{
  /* The request is already built; everything else starts over. */
  tcp.sent = tcp.in_flight = tcp.response_length = 0;
  tcp.rcv_nxt = 0;
  tcp.port  = (uint16_t)(40000 + (sim_now() % 1000));
  tcp.iss   = 0x10000000u + sim_now();
  tcp.snd_una = tcp.iss;
  tcp.snd_nxt = tcp.iss + 1;
  tcp.state = TCP_SYN_SENT;
  tcp_send(TCP_SYN, tcp.iss, NULL, 0);
  return 0;
}

/** @brief The request a browser builds from the upgrade page's form. */
static int build_request(const char *path)
{
  FILE *f;
  long size;
  uint8_t *file;
  const char *boundary = "----phone7f3a9c2e1b";
  const char *name;
  char head[512], part1[512], part2[256];
  uint32_t body_length, n1, n2;

  f = fopen(path, "rb");
  if(f == NULL) {
    sim_log("phone: cannot open %s", path);
    return -1;
  }
  fseek(f, 0, SEEK_END);
  size = ftell(f);
  fseek(f, 0, SEEK_SET);
  if(size <= 0) { fclose(f); return -1; }
  file = malloc((size_t)size);
  if(file == NULL || fread(file, 1, (size_t)size, f) != (size_t)size) {
    fclose(f); free(file); return -1;
  }
  fclose(f);

  name = strrchr(path, '/');
  name = name ? name + 1 : path;

  /* The two parts the form has: the file input `mtenFWUpload`, then the
   * submit button `Upgrade`. Same field names on the 2006 page and on the
   * modernized one; the rabbit reads neither, it looks for -violet-. */
  n1 = (uint32_t)snprintf(part1, sizeof(part1),
      "--%s\r\n"
      "Content-Disposition: form-data; name=\"mtenFWUpload\"; filename=\"%s\"\r\n"
      "Content-Type: application/octet-stream\r\n\r\n", boundary, name);
  n2 = (uint32_t)snprintf(part2, sizeof(part2),
      "\r\n--%s\r\n"
      "Content-Disposition: form-data; name=\"Upgrade\"\r\n\r\n"
      "1\r\n"
      "--%s--\r\n", boundary, boundary);
  body_length = n1 + (uint32_t)size + n2;

  snprintf(head, sizeof(head),
      "POST /c HTTP/1.1\r\n"
      "Host: 192.168.0.1\r\n"
      "User-Agent: Mozilla/5.0 (a phone, in the emulator)\r\n"
      "Accept: text/html\r\n"
      "Connection: close\r\n"
      "Content-Type: multipart/form-data; boundary=%s\r\n"
      "Content-Length: %u\r\n\r\n", boundary, body_length);

  tcp.request_length = (uint32_t)strlen(head) + body_length;
  tcp.request = malloc(tcp.request_length);
  if(tcp.request == NULL) { free(file); return -1; }
  memcpy(tcp.request, head, strlen(head));
  memcpy(tcp.request + strlen(head), part1, n1);
  memcpy(tcp.request + strlen(head) + n1, file, (size_t)size);
  memcpy(tcp.request + strlen(head) + n1 + size, part2, n2);
  free(file);

  sim_log("phone: POST /c with %s, %ld bytes of file in a %u-byte request, "
          "%u-byte segments", name, size, tcp.request_length, segment_size);
  return 0;
}

static void response_report(void)
{
  char line[160];
  uint32_t i, n;
  const char *body;

  tcp.response[tcp.response_length < RESPONSE_MAX ? tcp.response_length : RESPONSE_MAX - 1] = 0;
  for(n = 0; n < tcp.response_length && n < sizeof(line) - 1; n++) {
    if(tcp.response[n] == '\r' || tcp.response[n] == '\n') break;
    line[n] = (char)tcp.response[n];
  }
  line[n] = 0;
  if(tcp.response_length == 0) {
    sim_log("phone: the rabbit closed without answering");
    return;
  }
  if(sscanf(line, "HTTP/%*d.%*d %d", &phone_stats.response_status) != 1)
    phone_stats.response_status = 0;
  sim_log("phone: answer \"%s\", %u bytes", line, tcp.response_length);

  body = strstr((const char *)tcp.response, "\r\n\r\n");
  if(body == NULL) return;
  body += 4;
  if(*body == 0) {
    /* httpflash returns nil when it hands the image to flashFirmware: the
     * header goes out and nothing follows. A browser shows a blank page. */
    sim_log("phone: an empty page -- what httpflash answers when it flashes");
    return;
  }
  /* The first heading, which is what the person holding the phone reads. */
  {
    const char *h = strstr(body, "<h1>");
    if(h) {
      h += 4;
      for(i = 0; i < sizeof(line) - 1 && h[i] && h[i] != '<'; i++) line[i] = h[i];
      line[i] = 0;
      sim_log("phone: the page says \"%s\"", line);
    } else {
      for(i = 0; i < 60 && body[i]; i++) line[i] = (body[i] >= 32 && body[i] < 127) ? body[i] : ' ';
      line[i] = 0;
      sim_log("phone: the page starts \"%s\"", line);
    }
  }
}

static void tcp_finish(void)
{
  tcp.state = TCP_DONE;
  phone_stats.uploads_finished++;
  phone_stats.upload_finished_ms = sim_now();
  response_report();
  sim_log("phone: upload done in %u ms: %u segments (%u resent), %u bytes up, %u down",
          phone_stats.upload_finished_ms - phone_stats.upload_started_ms,
          phone_stats.segments_sent, phone_stats.segments_resent,
          phone_stats.bytes_sent, phone_stats.bytes_received);
  free(tcp.request);
  tcp.request = NULL;
}

static void handle_tcp(const uint8_t *seg, uint32_t length, uint32_t ip_total)
{
  uint16_t sport, dport;
  uint32_t seq, ack, hlen, dlen;
  uint8_t flags;

  if(length < 20) return;
  sport = rd16(seg); dport = rd16(seg + 2);
  seq = rd32(seg + 4); ack = rd32(seg + 8);
  hlen = (uint32_t)((seg[12] >> 4) * 4);
  flags = seg[13];
  if(sport != rabbit_port || dport != tcp.port) return;
  if(hlen < 20 || hlen > length) return;
  dlen = (ip_total < length ? ip_total : length) - hlen;

  if(flags & TCP_RST) {
    sim_log("phone: the rabbit reset the connection");
    tcp_finish();
    return;
  }

  switch(tcp.state) {
    case TCP_SYN_SENT:
      if((flags & (TCP_SYN | TCP_ACK)) == (TCP_SYN | TCP_ACK) && ack == tcp.iss + 1) {
        tcp.rcv_nxt = seq + 1;
        tcp.snd_una = tcp.iss + 1;
        tcp_send(TCP_ACK, tcp.snd_una, NULL, 0);
        tcp.state = TCP_SENDING;
        sim_log("phone: connected to 192.168.0.1:80");
        tcp_send_next_segment(0);
      }
      break;

    case TCP_SENDING:
    case TCP_RECEIVING:
      if((flags & TCP_ACK) && tcp.in_flight && ack == tcp.snd_una + tcp.in_flight) {
        tcp.sent += tcp.in_flight;
        tcp.snd_una += tcp.in_flight;
        tcp.in_flight = 0;
        if(tcp.sent >= tcp.request_length && tcp.state == TCP_SENDING) {
          tcp.state = TCP_RECEIVING;
          sim_log("phone: request sent, %u bytes in %u segments; waiting for the rabbit",
                  tcp.sent, phone_stats.segments_sent);
        }
      }
      if(dlen > 0 && seq == tcp.rcv_nxt) {
        if(tcp.response_length + dlen <= RESPONSE_MAX) {
          memcpy(tcp.response + tcp.response_length, seg + hlen, dlen);
          tcp.response_length += dlen;
        }
        phone_stats.bytes_received += dlen;
        tcp.rcv_nxt += dlen;
        tcp_send(TCP_ACK, tcp.snd_una, NULL, 0);
      }
      if((flags & TCP_FIN) && seq + dlen == tcp.rcv_nxt) {
        tcp.rcv_nxt++;
        tcp_send(TCP_FIN | TCP_ACK, tcp.snd_una, NULL, 0);
        tcp.state = TCP_FIN_WAIT;
        tcp_finish();
      }
      break;

    case TCP_FIN_WAIT:
    case TCP_DONE:
    default:
      break;
  }
}

static void handle_ip(const uint8_t *packet, uint32_t length)
{
  uint32_t hlen, total;

  if(length < 20) return;
  hlen = (uint32_t)((packet[0] & 0x0f) * 4);
  total = rd16(packet + 2);
  if(hlen < 20 || hlen > length) return;
  if(total > length) total = length;
  if(rd32(packet + 16) != phone_ip) return;
  if(packet[9] == 6)
    handle_tcp(packet + hlen, length - hlen, total - hlen);
}

/* --- frames from the rabbit ------------------------------------------- */

void phone_from_rabbit(const uint8_t *frame, uint32_t length)
{
  const struct ieee80211_frame *fr = (const struct ieee80211_frame *)frame;
  const uint8_t *body;
  uint32_t body_length;

  if(length < sizeof(*fr)) return;
  body = frame + sizeof(*fr);
  body_length = length - (uint32_t)sizeof(*fr);

  if((fr->i_fc[0] & IEEE80211_FC0_TYPE_MASK) == IEEE80211_FC0_TYPE_MGT) {
    switch(fr->i_fc[0] & IEEE80211_FC0_SUBTYPE_MASK) {
      case IEEE80211_FC0_SUBTYPE_BEACON:
      case IEEE80211_FC0_SUBTYPE_PROBE_RESP: {
        /* Fixed fields: timestamp 8, interval 2, capability 2; then IEs. */
        const uint8_t *p = body + 12, *end = body + body_length;
        phone_stats.beacons_heard++;
        if(net_seen) return;
        while(p + 2 <= end && p + 2 + p[1] <= end) {
          if(p[0] == IEEE80211_ELEMID_SSID && p[1] < sizeof(ssid)) {
            memcpy(ssid, p + 2, p[1]);
            ssid[p[1]] = 0;
          }
          p += 2 + p[1];
        }
        memcpy(bssid, fr->i_addr3, 6);
        net_seen = 1;
        sim_log("phone: the rabbit is an access point, \"%s\" (%02x:%02x:%02x:%02x:%02x:%02x), open",
                ssid, bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);
        break;
      }
      case IEEE80211_FC0_SUBTYPE_AUTH:
        if(memcmp(fr->i_addr1, phone_mac, 6) != 0 || body_length < 6) return;
        if(sta_state != ST_AUTH_SENT) return;
        if(body[4] == 0 && body[5] == 0) {
          phone_stats.auth_done++;
          sim_log("phone: authenticated");
          send_assoc_request();
        } else
          sim_log("phone: authentication refused, status %u", body[4] | (body[5] << 8));
        break;
      case IEEE80211_FC0_SUBTYPE_ASSOC_RESP:
        if(memcmp(fr->i_addr1, phone_mac, 6) != 0 || body_length < 6) return;
        if(sta_state != ST_ASSOC_SENT) return;
        if(body[2] == 0 && body[3] == 0) {
          sta_state = ST_JOINED;
          phone_stats.assoc_done++;
          sim_log("phone: associated, AID %u -- on the rabbit's network as 192.168.0.2",
                  (body[4] | (body[5] << 8)) & 0x3fff);
        } else
          sim_log("phone: association refused, status %u", body[2] | (body[3] << 8));
        break;
      case IEEE80211_FC0_SUBTYPE_DEAUTH:
        if(memcmp(fr->i_addr1, phone_mac, 6) != 0) return;
        sim_log("phone: deauthenticated by the rabbit, reason %u", body_length >= 2 ? body[0] | (body[1] << 8) : 0);
        sta_state = ST_OFF;
        break;
      default:
        break;
    }
    return;
  }

  if((fr->i_fc[0] & IEEE80211_FC0_TYPE_MASK) != IEEE80211_FC0_TYPE_DATA) return;
  if((fr->i_fc[0] & IEEE80211_FC0_SUBTYPE_MASK) != IEEE80211_FC0_SUBTYPE_DATA) return;
  if(sta_state != ST_JOINED) return;
  if(memcmp(fr->i_addr1, phone_mac, 6) != 0 && !IEEE80211_IS_MULTICAST(fr->i_addr1)) return;
  if(body_length < LLC_SNAP_LENGTH || memcmp(body, llc_snap, 6) != 0) return;

  switch(rd16(body + 6)) {
    case ETHERTYPE_ARP: handle_arp(body + LLC_SNAP_LENGTH, body_length - LLC_SNAP_LENGTH); break;
    case ETHERTYPE_IP:  handle_ip(body + LLC_SNAP_LENGTH, body_length - LLC_SNAP_LENGTH); break;
    default: break;
  }
}

/* --- the loop ---------------------------------------------------------- */

int phone_start_upload(void)
{
  upload_wanted = 1;
  if(upload_path[0] == 0) {
    sim_log("phone: nothing to upload -- run with --upload <file.sim>");
    return -1;
  }
  if(sta_state != ST_JOINED) {
    sim_log("phone: not on the rabbit's network yet, the upload waits");
    return -1;
  }
  if(tcp.state != TCP_CLOSED) return -1;
  if(build_request(upload_path) != 0) return -1;
  phone_stats.uploads_started++;
  phone_stats.upload_started_ms = sim_now();
  upload_wanted = 0;
  return tcp_open();
}

void phone_poll(void)
{
  if(ieee80211_mode != IEEE80211_M_MASTER) return;
  if(!net_seen) return;

  switch(sta_state) {
    case ST_OFF:
      send_auth_request();
      break;
    case ST_AUTH_SENT:
    case ST_ASSOC_SENT:
      if(sim_now() - sta_sent_ms > 2000) {
        sim_log("phone: no answer from the rabbit, trying again");
        sta_state = ST_OFF;
      }
      break;
    case ST_JOINED:
      if(upload_wanted && tcp.state == TCP_CLOSED) phone_start_upload();
      break;
  }

  if(sta_state != ST_JOINED) return;

  switch(tcp.state) {
    case TCP_SYN_SENT:
      if(sim_now() - tcp.last_tx_ms > TCP_RETRANSMIT_MS) {
        sim_log("phone: no SYN/ACK, retrying");
        tcp_send(TCP_SYN, tcp.iss, NULL, 0);
      }
      break;
    case TCP_SENDING:
      if(tcp.in_flight == 0) tcp_send_next_segment(0);
      else if(sim_now() - tcp.last_tx_ms > TCP_RETRANSMIT_MS) tcp_send_next_segment(1);
      break;
    default:
      break;
  }
}
