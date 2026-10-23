/**
 * @file gw.c
 * @brief The network behind the virtual access point.
 *
 * A Nabaztag's TCP/IP stack is not in its firmware. vnet.c is 153 lines and
 * does nothing but hand Ethernet payloads up to the bytecode and back down to
 * the radio; ARP, DHCP, DNS, HTTP and XMPP are all Metal, which is most of
 * why the bytecode is a hundred kilobytes. That makes this file the other
 * half of emulating a rabbit: the radio answers 802.11, and this answers
 * everything above it.
 *
 * So it is a small router. ARP, DHCP and ICMP echo are answered here; DNS is
 * answered here too, with one address, because the point is to get the rabbit
 * talking to a warren and not to run a resolver. TCP is proxied: every
 * connection the rabbit opens becomes a host socket to --warren, keeping the
 * port it asked for, so a real Terrier on the other side sees a real rabbit.
 *
 * The TCP here is deliberately thin. There is no congestion control and
 * nothing is retransmitted, because the medium between vap.c and the firmware
 * loses a frame only when a scenario says to lose it. What it does do is keep
 * the sequence numbers honest, because the bytecode's own TCP is the thing
 * under test and it will notice.
 */
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "common.h"
#include "net/ieee80211.h"

#include "sim.h"
#include "vap.h"
#include "gw.h"

struct gw_stats gw_stats;

/* --- the wire --------------------------------------------------------- */

#define LLC_SNAP_LENGTH 8
#define ETHERTYPE_IP    0x0800
#define ETHERTYPE_ARP   0x0806

static const uint8_t llc_snap[6] = {0xaa, 0xaa, 0x03, 0x00, 0x00, 0x00};

/* The gateway's own identity on the simulated LAN. */
static uint8_t  gw_mac[6] = {0x02, 0x00, 0x5e, 0x10, 0x00, 0xfe};
static uint32_t gw_ip     = 0x0a000001;   /* 10.0.0.1 */
static uint32_t gw_mask   = 0xffffff00;
static uint32_t rabbit_ip = 0x0a00002a;   /* 10.0.0.42 */

static char warren_host[256];

/* Per-packet logging, off unless --trace-net. Every IP packet and UDP
 * datagram at once is far too much to read when the question is "did it
 * reach its warren", and exactly what you want when the answer is no. */
static int gw_trace;

void gw_set_trace(int on) { gw_trace = on; }

/* Whether the gateway answers ARP at all. Off models the server's host being
 * down rather than the service: issue #23's finding 3 is that the bytecode
 * adds a socket to `ltcp` before ARP resolves, arptime() drops the request
 * after 10 s without calling back, and nothing ever frees the socket — so a
 * TCP attempt every 3 s leaks until the VM heap is exhausted and vmem.c does
 * ?OM Error -> sysReboot(), which is the orange-until-unplugged state the
 * reconnect work exists to remove. */
static int gw_arp_answers = 1;

void gw_set_arp(int on)
{
  gw_arp_answers = on;
  sim_log("gw: ARP %s", on ? "answered again" : "no longer answered — the "
          "server's host is down, not just its service");
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

static const char *ipstr(uint32_t ip)
{
  static char buf[4][16];
  static int which;
  char *b = buf[which = (which + 1) & 3];
  snprintf(b, 16, "%u.%u.%u.%u", (ip >> 24) & 0xff, (ip >> 16) & 0xff,
           (ip >> 8) & 0xff, ip & 0xff);
  return b;
}

/** @brief The one's-complement sum every one of these protocols uses. */
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

/* --- sending to the rabbit -------------------------------------------- */

#define GW_FRAME_MAX 1600

/** @brief Wrap a packet in LLC/SNAP and hand it to the radio. */
static void to_rabbit(uint16_t ethertype, const uint8_t *packet, uint32_t length)
{
  uint8_t frame[GW_FRAME_MAX];

  if(LLC_SNAP_LENGTH + length > sizeof(frame)) {
    sim_log("gw: a %u-byte packet will not fit in a frame", length);
    return;
  }
  memcpy(frame, llc_snap, sizeof(llc_snap));
  wr16(frame + 6, ethertype);
  memcpy(frame + LLC_SNAP_LENGTH, packet, length);
  gw_stats.bytes_to_rabbit += length;
  vap_to_station(gw_mac, frame, LLC_SNAP_LENGTH + length);
}

/**
 * @brief Build and send an IPv4 packet, computing both checksums.
 *
 * @param protocol  1 ICMP, 6 TCP, 17 UDP
 * @param payload   the transport header and its data, with its own checksum
 *                  field already zeroed
 */
static void send_ip(uint32_t src, uint32_t dst, uint8_t protocol,
                    uint8_t *payload, uint32_t length, int transport_checksum)
{
  uint8_t packet[GW_FRAME_MAX];
  static uint16_t ident;

  if(20 + length > sizeof(packet)) {
    sim_log("gw: a %u-byte payload will not fit in a packet", length);
    return;
  }

  packet[0] = 0x45;                       /* version 4, 20-byte header */
  packet[1] = 0x00;                       /* no differentiated services */
  wr16(packet + 2, (uint16_t)(20 + length));
  wr16(packet + 4, ++ident);
  wr16(packet + 6, 0x0000);               /* no fragmentation */
  packet[8] = 64;                         /* TTL */
  packet[9] = protocol;
  wr16(packet + 10, 0);                   /* checksum, filled in below */
  wr32(packet + 12, src);
  wr32(packet + 16, dst);
  memcpy(packet + 20, payload, length);

  if(transport_checksum) {
    /* The pseudo-header: source, destination, protocol, transport length. */
    uint32_t seed = (src >> 16) + (src & 0xffff) + (dst >> 16) + (dst & 0xffff)
                  + protocol + length;
    uint16_t sum = checksum(packet + 20, length, seed);
    /* UDP and TCP keep their checksum at a different offset; ICMP has no
     * pseudo-header and is handled by its caller. */
    uint32_t offset = (protocol == 6) ? 16 : 6;
    wr16(packet + 20 + offset, sum == 0 ? 0xffff : sum);
  }

  wr16(packet + 10, checksum(packet, 20, 0));
  to_rabbit(ETHERTYPE_IP, packet, 20 + length);
}

/* --- ARP -------------------------------------------------------------- */

/**
 * @brief Answer any ARP request, with our own MAC.
 *
 * Proxy ARP for the whole subnet. The rabbit only ever needs its gateway, but
 * a static configuration whose gateway is somewhere else entirely should still
 * work rather than fail in a way that looks like a firmware bug.
 */
static void handle_arp(const uint8_t *arp, uint32_t length)
{
  uint8_t reply[28];
  uint32_t target, sender;

  if(length < 28) return;
  if(rd16(arp) != 0x0001 || rd16(arp + 2) != ETHERTYPE_IP) return;
  if(rd16(arp + 6) != 1) return;          /* only requests */
  if(!gw_arp_answers) {
    gw_stats.arp_unanswered++;
    return;
  }

  sender = rd32(arp + 14);
  target = rd32(arp + 24);
  gw_stats.arp_requests++;

  /* Nobody owns 0.0.0.0, and answering for it does real damage: a rabbit
   * whose gateway is still unset ARPs for 0.0.0.0, and an answer tells it
   * that gateway exists. It then unicasts its DHCP discovery to us with a
   * destination address of 0.0.0.0, which no DHCP server will recognise, and
   * sits in its DHCP retry loop for ever. Found exactly that way. */
  if(target == 0 || target == 0xffffffff) {
    if(gw_trace)
      sim_log("gw: ARP for %s, which nobody owns — not answering", ipstr(target));
    return;
  }
  if(gw_trace)
    sim_log("gw: ARP who has %s (asked by %s)", ipstr(target), ipstr(sender));

  wr16(reply, 0x0001);                    /* Ethernet */
  wr16(reply + 2, ETHERTYPE_IP);
  reply[4] = 6; reply[5] = 4;
  wr16(reply + 6, 2);                     /* reply */
  memcpy(reply + 8, gw_mac, 6);
  wr32(reply + 14, target);
  memcpy(reply + 18, arp + 8, 6);         /* back to whoever asked */
  wr32(reply + 24, sender);
  to_rabbit(ETHERTYPE_ARP, reply, sizeof(reply));
}

/* --- DHCP ------------------------------------------------------------- */

static void handle_dhcp(const uint8_t *udp_payload, uint32_t length)
{
  uint8_t out[548];
  uint8_t *options;
  uint8_t message_type = 0;
  const uint8_t *p, *end;
  uint32_t n;

  /* A BOOTP message is 236 fixed bytes plus the four-byte magic cookie, and
   * then options. A sender that pads nothing still reaches 240. */
  if(length < 240) {
    sim_log("gw: a %u-byte DHCP message, too short to be BOOTP", length);
    return;
  }
  if(udp_payload[0] != 1) {
    sim_log("gw: a DHCP message with op %u, not a request", udp_payload[0]);
    return;
  }

  /* Walk the options for the message type: 1 discover, 3 request. */
  p = udp_payload + 240;
  end = udp_payload + length;
  while(p + 1 < end && *p != 0xff) {
    if(*p == 0) { p++; continue; }
    if(*p == 53 && p[1] >= 1) message_type = p[2];
    p += 2 + p[1];
  }
  if(message_type != 1 && message_type != 3) {
    sim_log("gw: DHCP message type %u, which this server does not answer",
            message_type);
    return;
  }

  memset(out, 0, sizeof(out));
  out[0] = 2;                             /* a reply */
  out[1] = udp_payload[1];                /* hardware type */
  out[2] = udp_payload[2];                /* hardware address length */
  memcpy(out + 4, udp_payload + 4, 4);    /* the transaction id, echoed */
  wr32(out + 16, rabbit_ip);              /* yiaddr: here is your address */
  wr32(out + 20, gw_ip);                  /* siaddr */
  memcpy(out + 28, udp_payload + 28, 16); /* chaddr */
  out[236] = 0x63; out[237] = 0x82; out[238] = 0x53; out[239] = 0x63;

  options = out + 240;
  *options++ = 53; *options++ = 1;
  *options++ = (message_type == 1) ? 2 : 5;      /* offer, or acknowledge */
  *options++ = 1;  *options++ = 4; wr32(options, gw_mask);   options += 4;
  *options++ = 3;  *options++ = 4; wr32(options, gw_ip);     options += 4;
  *options++ = 6;  *options++ = 4; wr32(options, gw_ip);     options += 4;
  *options++ = 51; *options++ = 4; wr32(options, 86400);     options += 4;
  *options++ = 54; *options++ = 4; wr32(options, gw_ip);     options += 4;
  *options++ = 0xff;

  n = (uint32_t)(options - out);

  {
    uint8_t udp[8 + sizeof(out)];
    wr16(udp, 67);                        /* from the server port */
    wr16(udp + 2, 68);
    wr16(udp + 4, (uint16_t)(8 + n));
    wr16(udp + 6, 0);
    memcpy(udp + 8, out, n);
    gw_stats.dhcp_offers++;
    sim_log("gw: DHCP %s — %s, mask %s, gateway and DNS %s",
            message_type == 1 ? "offer" : "acknowledge", ipstr(rabbit_ip),
            ipstr(gw_mask), ipstr(gw_ip));
    /* To the broadcast address: the rabbit has no address to be reached at. */
    send_ip(gw_ip, 0xffffffff, 17, udp, 8 + n, 1);
  }
}

/* --- DNS -------------------------------------------------------------- */

/**
 * @brief Answer an A query with the gateway's own address.
 *
 * Every name resolves here, which is the point: the rabbit's configuration
 * can name whatever host a warren is published under, and the connection it
 * then opens is proxied to --warren anyway. Anything else would mean caring
 * about names in two places.
 */
static void handle_dns(uint32_t src, uint16_t sport, const uint8_t *query,
                       uint32_t length)
{
  uint8_t out[512];
  uint32_t qlen = 0;
  const uint8_t *p;
  char name[256];
  uint32_t namelen = 0;

  if(length < 12) return;
  if((query[2] & 0x80) != 0) return;      /* already a response */
  if(rd16(query + 4) != 1) return;        /* exactly one question */

  /* Measure the question, and read the name for the log. */
  p = query + 12;
  while(p < query + length && *p != 0) {
    uint8_t label = *p;
    if(label > 63 || p + 1 + label > query + length) return;
    if(namelen) name[namelen++] = '.';
    if(namelen + label < sizeof(name)) {
      memcpy(name + namelen, p + 1, label);
      namelen += label;
    }
    p += 1 + label;
  }
  name[namelen] = 0;
  p++;                                     /* the root label */
  if(p + 4 > query + length) return;
  qlen = (uint32_t)(p + 4 - (query + 12));

  gw_stats.dns_queries++;
  sim_log("gw: DNS %s -> %s", name, ipstr(gw_ip));

  memcpy(out, query, 12 + qlen);
  out[2] = 0x81; out[3] = 0x80;            /* a response, recursion available */
  wr16(out + 6, 1);                        /* one answer */
  wr16(out + 8, 0);
  wr16(out + 10, 0);

  {
    uint8_t *a = out + 12 + qlen;
    wr16(a, 0xc00c); a += 2;               /* a pointer back to the question */
    wr16(a, 1); a += 2;                    /* type A */
    wr16(a, 1); a += 2;                    /* class IN */
    wr32(a, 60); a += 4;                   /* time to live */
    wr16(a, 4); a += 2;
    wr32(a, gw_ip); a += 4;

    {
      uint32_t n = (uint32_t)(a - out);
      uint8_t udp[8 + 512];
      wr16(udp, 53);
      wr16(udp + 2, sport);
      wr16(udp + 4, (uint16_t)(8 + n));
      wr16(udp + 6, 0);
      memcpy(udp + 8, out, n);
      send_ip(gw_ip, src, 17, udp, 8 + n, 1);
    }
  }
}

/* --- ICMP ------------------------------------------------------------- */

static void handle_icmp(uint32_t src, uint32_t dst, const uint8_t *icmp,
                        uint32_t length)
{
  uint8_t out[GW_FRAME_MAX];

  if(length < 8 || icmp[0] != 8) return;   /* echo requests only */
  if(length > sizeof(out)) return;

  gw_stats.pings++;
  sim_log("gw: the rabbit pinged %s", ipstr(dst));

  memcpy(out, icmp, length);
  out[0] = 0;                              /* echo reply */
  wr16(out + 2, 0);
  wr16(out + 2, checksum(out, length, 0)); /* ICMP has no pseudo-header */
  send_ip(dst, src, 1, out, length, 0);
}

/* --- TCP -------------------------------------------------------------- */

#define GW_MAX_CONNECTIONS 8
#define GW_MSS             1400

enum {
  TCP_FREE,
  TCP_CONNECTING,     /* the host socket is still coming up */
  TCP_OPEN,
  TCP_CLOSING,        /* the rabbit or the host has sent a FIN */
};

struct gw_conn {
  int      state;
  int      fd;
  uint32_t rabbit_ip;
  uint16_t rabbit_port;
  uint32_t dest_ip;
  uint16_t dest_port;
  uint32_t snd_nxt;   /* the next sequence number we will send */
  uint32_t rcv_nxt;   /* the next sequence number we expect */
  int      fin_sent;
};

static struct gw_conn conns[GW_MAX_CONNECTIONS];

/**
 * @brief How many connections to the XMPP port are open.
 *
 * Used to tell a resource download from the boot server's own fetches: the
 * bytecode asks for bc.jsp and locate.jsp before it has a session at all.
 */
static int xmpp_conn_count(void)
{
  int n = 0;
  for(int i = 0; i < GW_MAX_CONNECTIONS; i++)
    if(conns[i].state == TCP_OPEN && conns[i].dest_port == 5222) n++;
  return n;
}

static struct gw_conn *conn_find(uint32_t ip, uint16_t port)
{
  for(int i = 0; i < GW_MAX_CONNECTIONS; i++)
    if(conns[i].state != TCP_FREE
       && conns[i].rabbit_ip == ip && conns[i].rabbit_port == port)
      return &conns[i];
  return NULL;
}

static struct gw_conn *conn_new(void)
{
  for(int i = 0; i < GW_MAX_CONNECTIONS; i++)
    if(conns[i].state == TCP_FREE) return &conns[i];
  return NULL;
}

static void conn_drop(struct gw_conn *c)
{
  if(c->fd >= 0) close(c->fd);
  memset(c, 0, sizeof(*c));
  c->fd = -1;
  c->state = TCP_FREE;
}

/**
 * @brief Send one TCP segment to the rabbit.
 *
 * @param flags  the usual bits: 0x01 FIN, 0x02 SYN, 0x04 RST, 0x10 ACK
 */
static void tcp_send(struct gw_conn *c, uint8_t flags, const uint8_t *data,
                     uint32_t length)
{
  uint8_t segment[20 + GW_MSS];

  if(length > GW_MSS) length = GW_MSS;

  wr16(segment, c->dest_port);
  wr16(segment + 2, c->rabbit_port);
  wr32(segment + 4, c->snd_nxt);
  wr32(segment + 8, c->rcv_nxt);
  segment[12] = 0x50;                      /* a 20-byte header */
  segment[13] = flags;
  wr16(segment + 14, 8192);                /* a window we never shrink */
  wr16(segment + 16, 0);                   /* checksum, filled in by send_ip */
  wr16(segment + 18, 0);                   /* no urgent pointer */
  if(length) memcpy(segment + 20, data, length);

  if(flags & 0x02) c->snd_nxt++;           /* a SYN takes a sequence number */
  c->snd_nxt += length;
  if(flags & 0x01) c->snd_nxt++;           /* and so does a FIN */

  send_ip(c->dest_ip, c->rabbit_ip, 6, segment, 20 + length, 1);
}

/** @brief Open a host socket to the warren, keeping the port asked for. */
static int host_connect(uint16_t port)
{
  struct addrinfo hints, *result, *candidate;
  char service[8];
  int fd = -1;

  if(warren_host[0] == 0) return -1;

  memset(&hints, 0, sizeof(hints));
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  snprintf(service, sizeof(service), "%u", port);

  if(getaddrinfo(warren_host, service, &hints, &result) != 0) {
    sim_log("gw: cannot resolve %s", warren_host);
    return -1;
  }
  for(candidate = result; candidate != NULL; candidate = candidate->ai_next) {
    fd = socket(candidate->ai_family, candidate->ai_socktype, 0);
    if(fd < 0) continue;
    if(connect(fd, candidate->ai_addr, candidate->ai_addrlen) == 0) break;
    close(fd);
    fd = -1;
  }
  freeaddrinfo(result);

  if(fd >= 0) {
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  }
  return fd;
}

static void handle_tcp(uint32_t src, uint32_t dst, const uint8_t *tcp,
                       uint32_t length)
{
  uint16_t sport, dport;
  uint32_t seq, ack;
  uint8_t flags;
  uint32_t header_length, data_length;
  const uint8_t *data;
  struct gw_conn *c;

  if(length < 20) return;
  sport = rd16(tcp);
  dport = rd16(tcp + 2);
  seq   = rd32(tcp + 4);
  ack   = rd32(tcp + 8);
  header_length = (uint32_t)((tcp[12] >> 4) * 4);
  if(header_length < 20 || header_length > length) return;
  flags = tcp[13];
  data = tcp + header_length;
  data_length = length - header_length;
  (void)ack;

  c = conn_find(src, sport);

  if(flags & 0x02) {                       /* SYN: the rabbit is dialling */
    if(c != NULL) conn_drop(c);
    c = conn_new();
    if(c == NULL) {
      sim_log("gw: no room for another connection, refusing %s:%u",
              ipstr(dst), dport);
      return;
    }
    memset(c, 0, sizeof(*c));
    c->rabbit_ip = src;
    c->rabbit_port = sport;
    c->dest_ip = dst;
    c->dest_port = dport;
    c->rcv_nxt = seq + 1;
    c->snd_nxt = 0x1000;                   /* any starting point will do */
    c->fd = host_connect(dport);
    if(c->fd < 0) {
      gw_stats.tcp_refused++;
      sim_log("gw: nothing answers on port %u, refusing the rabbit", dport);
      c->state = TCP_OPEN;                 /* so tcp_send has somewhere to be */
      tcp_send(c, 0x04 | 0x10, NULL, 0);   /* RST */
      conn_drop(c);
      return;
    }
    c->state = TCP_OPEN;
    gw_stats.tcp_opened++;
    sim_log("gw: the rabbit opened %s:%u -> %s:%u", ipstr(src), sport,
            warren_host, dport);
    /* A connection to anything but the XMPP port, while a session is up, is
     * the bytecode fetching a resource: RequestResource, the ST and CH
     * commands' download. That is the moment a "fetch <ms>" event counts
     * from. The boot server's own bc.jsp and locate.jsp happen before there
     * is a session, so they do not count. */
    if(dport != 5222 && xmpp_conn_count() > 0) sim_note_fetch();
    tcp_send(c, 0x02 | 0x10, NULL, 0);     /* SYN-ACK */
    return;
  }

  if(c == NULL) {
    /* Nothing here knows this connection. A RST is the honest answer, and
     * it is what the rabbit's own stack would get from a real host. */
    struct gw_conn scratch;
    memset(&scratch, 0, sizeof(scratch));
    scratch.fd = -1;
    scratch.rabbit_ip = src;
    scratch.rabbit_port = sport;
    scratch.dest_ip = dst;
    scratch.dest_port = dport;
    scratch.rcv_nxt = seq + data_length;
    scratch.state = TCP_OPEN;
    tcp_send(&scratch, 0x04 | 0x10, NULL, 0);
    return;
  }

  if(data_length > 0) {
    if(seq != c->rcv_nxt) {
      /* Out of order, which on a lossless medium means a retransmission.
       * Acknowledge what we have and drop the rest. */
      tcp_send(c, 0x10, NULL, 0);
      return;
    }
    gw_stats.bytes_from_rabbit += data_length;
    if(c->fd >= 0) {
      ssize_t written = write(c->fd, data, data_length);
      if(written < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
        sim_log("gw: the warren hung up while being written to");
        tcp_send(c, 0x04 | 0x10, NULL, 0);
        conn_drop(c);
        return;
      }
    }
    c->rcv_nxt += data_length;
    tcp_send(c, 0x10, NULL, 0);
  }

  if(flags & 0x01) {                       /* FIN */
    c->rcv_nxt++;
    sim_log("gw: the rabbit closed %s:%u", ipstr(src), sport);
    tcp_send(c, 0x10, NULL, 0);
    if(c->fd >= 0) shutdown(c->fd, SHUT_WR);
    if(!c->fin_sent) {
      c->fin_sent = 1;
      tcp_send(c, 0x01 | 0x10, NULL, 0);
    }
    gw_stats.tcp_closed++;
    c->state = TCP_CLOSING;
    return;
  }

  if(flags & 0x04) {                       /* RST */
    sim_log("gw: the rabbit reset %s:%u", ipstr(src), sport);
    conn_drop(c);
  }
}

/* --- UDP -------------------------------------------------------------- */

static void handle_udp(uint32_t src, uint32_t dst, const uint8_t *udp,
                       uint32_t length)
{
  uint16_t sport, dport, udp_length;

  if(length < 8) return;
  sport = rd16(udp);
  dport = rd16(udp + 2);
  udp_length = rd16(udp + 4);
  if(udp_length < 8 || udp_length > length) udp_length = (uint16_t)length;

  gw_stats.udp_datagrams++;
  if(gw_trace)
    sim_log("gw: UDP %u -> %u, declared %u, carried %u", sport, dport,
            rd16(udp + 4), length);

  if(dport == 67) { handle_dhcp(udp + 8, udp_length - 8u); return; }
  if(dport == 53) { handle_dns(src, sport, udp + 8, udp_length - 8u); return; }

  sim_log("gw: a UDP datagram for %s:%u, %u bytes — nothing listens there",
          ipstr(dst), dport, udp_length - 8);
}

/* --- the way in ------------------------------------------------------- */

void gw_from_rabbit(const uint8_t *llc_frame, uint32_t length)
{
  uint16_t ethertype;
  const uint8_t *packet;
  uint32_t packet_length;

  if(length < LLC_SNAP_LENGTH) return;
  if(memcmp(llc_frame, llc_snap, sizeof(llc_snap)) != 0) {
    gw_stats.unknown_ethertype++;
    return;
  }
  ethertype = rd16(llc_frame + 6);
  packet = llc_frame + LLC_SNAP_LENGTH;
  packet_length = length - LLC_SNAP_LENGTH;

  if(ethertype == ETHERTYPE_ARP) { handle_arp(packet, packet_length); return; }
  if(ethertype != ETHERTYPE_IP) {
    gw_stats.unknown_ethertype++;
    sim_log("gw: ethertype 0x%04x, which this gateway does not speak", ethertype);
    return;
  }

  if(packet_length < 20) return;
  {
    uint32_t header_length = (uint32_t)((packet[0] & 0x0f) * 4);
    uint32_t total = rd16(packet + 2);
    uint32_t src = rd32(packet + 12);
    uint32_t dst = rd32(packet + 16);
    uint8_t protocol = packet[9];

    if(gw_trace)
      sim_log("gw: IP %s -> %s proto %u, header %u, total %u, carried %u",
              ipstr(src), ipstr(dst), protocol, header_length, total,
              packet_length);
    if(header_length < 20 || header_length > packet_length) return;
    if(total > packet_length) total = packet_length;

    switch(protocol) {
      case 1:  handle_icmp(src, dst, packet + header_length, total - header_length); break;
      case 6:  handle_tcp(src, dst, packet + header_length, total - header_length); break;
      case 17: handle_udp(src, dst, packet + header_length, total - header_length); break;
      default:
        gw_stats.unknown_protocol++;
        sim_log("gw: IP protocol %u, which this gateway does not speak", protocol);
        break;
    }
  }
}

/* --- the way back ----------------------------------------------------- */

void gw_poll(void)
{
  uint8_t buffer[GW_MSS];

  for(int i = 0; i < GW_MAX_CONNECTIONS; i++) {
    struct gw_conn *c = &conns[i];
    ssize_t n;

    if(c->state != TCP_OPEN && c->state != TCP_CLOSING) continue;
    if(c->fd < 0) continue;

    n = read(c->fd, buffer, sizeof(buffer));
    if(n > 0) {
      tcp_send(c, 0x10 | 0x08, buffer, (uint32_t)n);   /* ACK, PSH */
      continue;
    }
    if(n == 0) {
      /* The warren closed. Pass the FIN on, once. */
      if(!c->fin_sent) {
        c->fin_sent = 1;
        sim_log("gw: the warren closed %s:%u", ipstr(c->rabbit_ip), c->rabbit_port);
        tcp_send(c, 0x01 | 0x10, NULL, 0);
        gw_stats.tcp_closed++;
      }
      if(c->state == TCP_CLOSING) conn_drop(c);
      continue;
    }
    if(errno != EAGAIN && errno != EWOULDBLOCK) {
      sim_log("gw: %s:%u broke (%s)", ipstr(c->rabbit_ip), c->rabbit_port,
              strerror(errno));
      tcp_send(c, 0x04 | 0x10, NULL, 0);
      conn_drop(c);
    }
  }
}

void gw_init(const char *host)
{
  for(int i = 0; i < GW_MAX_CONNECTIONS; i++) {
    conns[i].fd = -1;
    conns[i].state = TCP_FREE;
  }
  memset(&gw_stats, 0, sizeof(gw_stats));
  snprintf(warren_host, sizeof(warren_host), "%s", host ? host : "");

  sim_log("gw: %s is the gateway, the rabbit will be offered %s",
          ipstr(gw_ip), ipstr(rabbit_ip));
  if(warren_host[0])
    sim_log("gw: every connection the rabbit opens goes to %s, on the port it "
            "asked for", warren_host);
  else
    sim_log("gw: no --warren, so nothing can be connected to — ARP, DHCP, DNS "
            "and ping still answer");
}

void gw_set_subnet(const char *gateway, const char *rabbit)
{
  unsigned a, b, c, d;
  if(gateway && sscanf(gateway, "%u.%u.%u.%u", &a, &b, &c, &d) == 4)
    gw_ip = (a << 24) | (b << 16) | (c << 8) | d;
  if(rabbit && sscanf(rabbit, "%u.%u.%u.%u", &a, &b, &c, &d) == 4)
    rabbit_ip = (a << 24) | (b << 16) | (c << 8) | d;
}

/**
 * @brief Drop every connection the rabbit has open, as a server going away
 *        does: a FIN on each, which is what the add-on's socket closing looks
 *        like from the rabbit's side.
 *
 * Deterministic, in virtual time, which racing a real process is not. It is
 * how issue #23's findings 1 and 2 are reached: the bytecode's
 * XmppSessionIsDisconnected runs, and what happens next is the question.
 */
void gw_server_drop(void)
{
  gw_server_close(0);
}

/**
 * @brief Close the way a server shutting down closes.
 *
 * @param graceful  1 to write "</stream:stream>" to the XMPP connection
 *                  before the FIN, which is what an add-on announcing its
 *                  own shutdown does.
 *
 * It matters because the two produce a different number of calls into the
 * bytecode. A bare FIN gives one XmppSessionIsDisconnected; a stream close
 * followed by a TCP close gives two, and issue #23's finding 2 is that the
 * second one replays the whole stop -- another ear reset and a freshly drawn
 * 5-35 s delay -- because the re-entry guard tests `run`, which
 * stopEverythingAndResetToIdle has just overwritten. pacifox picked this
 * sequence as the realistic one: it needs nothing from the user and happens
 * on every server restart.
 */
void gw_server_close(int graceful)
{
  static const char stream_close[] = "</stream:stream>";
  int dropped = 0, announced = 0;

  for(int i = 0; i < GW_MAX_CONNECTIONS; i++) {
    struct gw_conn *c = &conns[i];
    if(c->state != TCP_OPEN && c->state != TCP_CLOSING) continue;
    if(graceful && c->dest_port == 5222 && !c->fin_sent) {
      tcp_send(c, 0x10 | 0x08, (const uint8_t *)stream_close,
               (uint32_t)(sizeof(stream_close) - 1));
      announced++;
    }
    if(!c->fin_sent) {
      c->fin_sent = 1;
      tcp_send(c, 0x01 | 0x10, NULL, 0);      /* FIN, as a close does */
      gw_stats.tcp_closed++;
      dropped++;
    }
    if(c->fd >= 0) { close(c->fd); c->fd = -1; }
    c->state = TCP_CLOSING;
  }
  sim_log("gw: the server went away — %d connection%s closed%s", dropped,
          dropped == 1 ? "" : "s",
          announced ? ", after announcing </stream:stream>" : "");
}

void gw_close(void)
{
  for(int i = 0; i < GW_MAX_CONNECTIONS; i++)
    if(conns[i].state != TCP_FREE) conn_drop(&conns[i]);
}
