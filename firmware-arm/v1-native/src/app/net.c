/*
 * net.c -- a stack the size of the rabbit: ARP, IPv4, ICMP echo, UDP with
 * DHCP and DNS, one TCP client, the HTTP poll of the Terrier's v1 endpoint.
 *
 * Nothing is buffered on the way in: a received frame is parsed straight
 * from the card's buffer, byte by byte (prism.c rx_*), so a 1400-byte
 * segment costs no RAM. On the way out one buffer holds the frame being
 * built, from its 802.3 header; the largest is the DHCP discovery.
 *
 * Own design, no code taken: uIP (BSD) was read for its shape (one
 * connection, no copies) and found heavier than this on a PIC18 once its
 * generic parts are in; Violet's firmware (1.4b, 0xA5E4 the poll builder)
 * and the emulator's gateway (emulator/src/gw.c) for what the other side
 * sends. Lesson 3 of v1-icsp/IMPROVEMENTS.md applied: the server's name is
 * resolved again when a connection fails, not once per power-on.
 */
#include "board.h"

#ifndef SERVER_NAME
#define SERVER_NAME     "p.nabaztag.com"
#endif
#define POLL_PATH       "/vl/FR/p3.jsp?sn="
#define FW_PATH         "/vl/v1/fw?sn="          /* ours: the image for this board (DESIGN.md 8) */
#define REQ_POLL        1
#define REQ_IMAGE       2
#define POLL_INTERVAL   MS_TO_TICKS(10000)
#define RETRY           MS_TO_TICKS(3000)
#define TX_SIZE         340

#define ETH_IP          0x0800
#define ETH_ARP         0x0806
#define IP_ICMP         1
#define IP_TCP          6
#define IP_UDP          17

#define TCP_FIN 0x01
#define TCP_SYN 0x02
#define TCP_RST 0x04
#define TCP_PSH 0x08
#define TCP_ACK 0x10

/* In RAM, filled at init: a pointer into RAM is 2 bytes on this port, a
 * pointer that may point anywhere is 3 and dispatched at run time; every
 * address helper takes the former, so these cannot live in code space. */
static __data uint8_t bcast_mac[6], bcast_ip[4];

static uint8_t  state;                   /* NET_* */
static uint8_t  our_ip[4], mask[4], gw_ip[4], dns_ip[4], server_ip[4];
static uint8_t  hop_ip[4], hop_mac[6], have_hop;
static uint16_t timer;                   /* ticks at the last attempt of the current step */
static uint8_t  tries;
static uint32_t xid;
static uint16_t dns_id, dns_port;
uint16_t polls_answered;
/* for net_report: frames net_rx refused (not SNAP), the last one's first
 * bytes, and the DHCP replies seen */
static uint16_t not_snap, dhcp_replies, ip_frames, arp_frames, dns_replies;
static uint8_t  connects, established, refused, resets, timeouts, max_state;
static uint8_t  last_head[8];

static uint8_t  tx[TX_SIZE];
static uint16_t txp, ip_at, tp_at;       /* cursor; where the IP and the transport header start */

/* --- building a frame ---------------------------------------------------- */

static void put8(uint8_t v) { if(txp < TX_SIZE) tx[txp] = v; txp++; }
static void put16(uint16_t v) { put8((uint8_t)(v >> 8)); put8((uint8_t)v); }
static void put32(uint32_t v) { put16((uint16_t)(v >> 16)); put16((uint16_t)v); }
static void putn(__data const uint8_t *p, uint8_t n) { while(n--) put8(*p++); }
static void putz(uint8_t n) { while(n--) put8(0); }
static void puts_code(const __code char *s) { while(*s) put8((uint8_t)*s++); }
static void put_hex_upper(uint8_t v)
{
  static const __code char hex[] = "0123456789ABCDEF";
  put8((uint8_t)hex[v >> 4]); put8((uint8_t)hex[v & 15]);
}

static void frame_begin(__data const uint8_t *dst, uint16_t ethertype)
{
  txp = 0;
  putn(dst, 6); putn(our_mac, 6); put16(0);         /* 802.3 length, filled at the end */
  put8(0xAA); put8(0xAA); put8(0x03); putz(3);      /* LLC/SNAP, RFC 1042 */
  put16(ethertype);
}

static void frame_send(void)
{
  uint16_t n = txp - 14;
  tx[12] = (uint8_t)(n >> 8); tx[13] = (uint8_t)n;
  if(txp <= TX_SIZE) prism_tx(tx, txp);
  else log_line("net: frame too big");
}

/* One's complement sum over tx[from..from+len), the carry folded as it comes. */
static uint16_t csum(uint16_t from, uint16_t len, uint16_t seed)
{
  uint16_t sum = seed, w;
  while(len > 1) {
    w = ((uint16_t)tx[from] << 8) | tx[from + 1];
    sum += w; if(sum < w) sum++;
    from += 2; len -= 2;
  }
  if(len) { w = (uint16_t)tx[from] << 8; sum += w; if(sum < w) sum++; }
  return sum;
}

static void ip_begin(uint8_t proto, __data const uint8_t *dst, __data const uint8_t *dst_mac)
{
  frame_begin(dst_mac, ETH_IP);
  ip_at = txp;
  put8(0x45); put8(0); put16(0);                    /* total length later */
  put16(ticks()); put16(0x4000);                    /* id; don't fragment */
  put8(64); put8(proto); put16(0);                  /* checksum later */
  if(state == NET_DHCP) putz(4); else putn(our_ip, 4);
  putn(dst, 4);
  tp_at = txp;
}

/* Fill the lengths and both checksums; csum_at is the transport checksum's
 * offset from tp_at (6 for UDP, 16 for TCP), 0xFF for none. */
static void ip_send(uint8_t csum_at)
{
  uint16_t total = txp - ip_at, tlen = txp - tp_at, s;
  tx[ip_at + 2] = (uint8_t)(total >> 8); tx[ip_at + 3] = (uint8_t)total;
  if(csum_at != 0xFF) {
    /* the pseudo-header: addresses, protocol, transport length */
    s = csum(ip_at + 12, 8, 0);
    s += tx[ip_at + 9]; if(s < tx[ip_at + 9]) s++;
    s += tlen; if(s < tlen) s++;
    s = ~csum(tp_at, tlen, s);
    if(s == 0) s = 0xFFFF;
    tx[tp_at + csum_at] = (uint8_t)(s >> 8); tx[tp_at + csum_at + 1] = (uint8_t)s;
  }
  s = ~csum(ip_at, 20, 0);
  tx[ip_at + 10] = (uint8_t)(s >> 8); tx[ip_at + 11] = (uint8_t)s;
  frame_send();
}

static void udp_begin(uint16_t sport, uint16_t dport, __data const uint8_t *dst, __data const uint8_t *dst_mac)
{
  ip_begin(IP_UDP, dst, dst_mac);
  put16(sport); put16(dport); put16(0); put16(0);   /* length and checksum later */
}

static void udp_send(void)
{
  uint16_t n = txp - tp_at;
  tx[tp_at + 4] = (uint8_t)(n >> 8); tx[tp_at + 5] = (uint8_t)n;
  ip_send(6);
}

static uint8_t same_ip(__data const uint8_t *a, __data const uint8_t *b)
{
  return a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
}

static void copy4(__data uint8_t *d, __data const uint8_t *s) { d[0] = s[0]; d[1] = s[1]; d[2] = s[2]; d[3] = s[3]; }

/* --- ARP ----------------------------------------------------------------- */

static void arp_send(uint8_t op, __data const uint8_t *tha, __data const uint8_t *tpa, __data const uint8_t *dst_mac)
{
  frame_begin(dst_mac, ETH_ARP);
  put16(1); put16(ETH_IP); put8(6); put8(4); put16(op);
  putn(our_mac, 6); putn(our_ip, 4);
  putn(tha, 6); putn(tpa, 4);
  frame_send();
}

static void arp_rx(void)
{
  uint8_t sha[6], spa[4], tpa[4];
  uint16_t op;
  rx_skip(6); op = rx_be16();
  rx_bytes(sha, 6); rx_bytes(spa, 4); rx_skip(6); rx_bytes(tpa, 4);
  if(state == NET_DHCP) return;
  if(op == 1 && same_ip(tpa, our_ip)) arp_send(2, sha, spa, sha);
  if(op == 2 && same_ip(spa, hop_ip)) {
    uint8_t i;
    for(i = 0; i < 6; i++) hop_mac[i] = sha[i];
    have_hop = 1;
    log_s("arp: "); log_ip(hop_ip); log_s(" is "); log_mac(hop_mac); log_nl();
  }
}

/* The next hop for the server: itself on our subnet, the gateway otherwise. */
static void choose_hop(void)
{
  uint8_t i, local = 1;
  for(i = 0; i < 4; i++) if((server_ip[i] ^ our_ip[i]) & mask[i]) local = 0;
  copy4(hop_ip, local ? server_ip : gw_ip);
  have_hop = 0;
}

/* --- DHCP ---------------------------------------------------------------- */

static void dhcp_send(uint8_t type, __data const uint8_t *req_ip, __data const uint8_t *srv_ip)
{
  udp_begin(68, 67, bcast_ip, bcast_mac);
  put8(1); put8(1); put8(6); put8(0);               /* request, Ethernet, 6, hops */
  /* No broadcast flag, as Violet: the reply comes to our MAC. A UniFi AP
   * sends broadcasts at an OFDM rate Lazare's 802.11b card cannot hear:
   * asked as a broadcast, no offer ever reached it (2026-11-25). */
  put32(xid); put16(0); put16(0);                   /* secs; flags */
  putz(16);                                         /* ciaddr yiaddr siaddr giaddr */
  putn(our_mac, 6); putz(10);                       /* chaddr */
  putz(192);                                        /* sname, file */
  put8(0x63); put8(0x82); put8(0x53); put8(0x63);
  put8(53); put8(1); put8(type);
  put8(61); put8(7); put8(1); putn(our_mac, 6);    /* client id, as Violet's */
  put8(12); put8(8); puts_code("nabaztag");         /* host name */
  put8(55); put8(3); put8(1); put8(3); put8(6);     /* mask, router, DNS */
  if(req_ip) { put8(50); put8(4); putn(req_ip, 4); put8(54); put8(4); putn(srv_ip, 4); }
  put8(255);
  udp_send();
}

static void dhcp_rx(uint16_t len)
{
  uint8_t  b[16], type = 0, yiaddr[4], srv[4], o, ol;
  uint8_t  got_mask = 0, got_gw = 0, got_dns = 0, i;
  uint32_t x;
  if(len < 240) return;
  rx_skip(4);
  x = (uint32_t)rx_be16() << 16; x |= rx_be16();
  if(x != xid) return;
  rx_skip(8);                                       /* secs flags ciaddr */
  rx_bytes(yiaddr, 4);
  rx_skip(8);                                       /* siaddr giaddr */
  rx_bytes(b, 6);
  for(i = 0; i < 6; i++) if(b[i] != our_mac[i]) return;
  rx_skip(10 + 192 + 4);                            /* rest of chaddr, sname, file, cookie */
  len -= 240;
  while(len >= 2) {
    o = rx_byte(); len--;
    if(o == 255) break;
    if(o == 0) continue;
    ol = rx_byte(); len--;
    if(ol > len) return;
    len -= ol;
    if(o == 53 && ol == 1) type = rx_byte();
    else if(o == 1 && ol == 4) { rx_bytes(mask, 4); got_mask = 1; }
    else if(o == 3 && ol >= 4) { rx_bytes(gw_ip, 4); rx_skip(ol - 4); got_gw = 1; }
    else if(o == 6 && ol >= 4) { rx_bytes(dns_ip, 4); rx_skip(ol - 4); got_dns = 1; }
    else if(o == 54 && ol == 4) rx_bytes(srv, 4);
    else rx_skip(ol);
  }
  if(type == 2) {                                   /* an offer: ask for it */
    log_s("dhcp: offer "); log_ip(yiaddr); log_nl();
    dhcp_send(3, yiaddr, srv);
    timer = ticks();
  } else if(type == 5 && got_mask && got_gw) {      /* acknowledged: ours */
    copy4(our_ip, yiaddr);
    if(!got_dns) copy4(dns_ip, gw_ip);
    log_s("dhcp: "); log_ip(our_ip); log_s(" mask "); log_ip(mask);
    log_s(" gw "); log_ip(gw_ip); log_s(" dns "); log_ip(dns_ip); log_nl();
    state = NET_DNS;
    copy4(hop_ip, dns_ip); have_hop = 0;
    timer = ticks() - RETRY; tries = 0;
  }
}

/* --- DNS ----------------------------------------------------------------- */

static void dns_send(void)
{
  const __code char *s = SERVER_NAME;
  uint16_t at;
  dns_id = ticks() ^ 0x5A00;
  dns_port = 0xC000 | (ticks() & 0x0FFF);
  udp_begin(dns_port, 53, dns_ip, hop_mac);
  put16(dns_id); put16(0x0100); put16(1); put16(0); put16(0); put16(0);
  for(;;) {                                         /* labels */
    uint8_t n = 0;
    at = txp; put8(0);
    while(s[n] && s[n] != '.') { put8((uint8_t)s[n]); n++; }
    tx[at] = n;
    if(!s[n]) break;
    s += n + 1;
  }
  put8(0); put16(1); put16(1);                      /* root, type A, class IN */
  udp_send();
}

static void dns_skip_name(void)
{
  uint8_t n;
  for(;;) {
    n = rx_byte();
    if(n == 0) return;
    if(n & 0xC0) { rx_byte(); return; }             /* a pointer */
    rx_skip(n);
  }
}

static void dns_rx(void)
{
  uint16_t an, type, rdlen;
  if(rx_be16() != dns_id) return;
  rx_be16();                                        /* flags */
  if(rx_be16() != 1) return;                        /* one question */
  an = rx_be16(); rx_be16(); rx_be16();
  dns_skip_name(); rx_skip(4);
  while(an--) {
    dns_skip_name();
    type = rx_be16(); rx_skip(6);                   /* class, TTL */
    rdlen = rx_be16();
    if(type == 1 && rdlen == 4) {
      rx_bytes(server_ip, 4);
      log_s("dns: " SERVER_NAME " -> "); log_ip(server_ip); log_nl();
      state = NET_READY;
      choose_hop();
      timer = ticks() - POLL_INTERVAL; tries = 0;
      return;
    }
    rx_skip(rdlen);
  }
}

/* --- ICMP echo ----------------------------------------------------------- */

static void icmp_rx(__data const uint8_t *src, uint16_t len)
{
  uint16_t s;
  if(rx_byte() != 8 || len > 200) return;           /* an echo request we can hold */
  if(!have_hop) return;
  ip_begin(IP_ICMP, src, hop_mac);                  /* the hop's MAC answers for its subnet */
  put8(0); put8(0); put16(0);
  rx_skip(3);                                       /* code, checksum */
  len -= 4;
  while(len--) put8(rx_byte());
  s = ~csum(tp_at, txp - tp_at, 0);
  tx[tp_at + 2] = (uint8_t)(s >> 8); tx[tp_at + 3] = (uint8_t)s;
  ip_send(0xFF);
}

/* --- TCP, one client connection, and the HTTP poll on it ----------------- */

#define T_CLOSED   0
#define T_SYN_SENT 1
#define T_OPEN     2
#define T_FIN_WAIT 3

static struct {
  uint8_t  st;
  uint16_t lport;
  uint32_t snd_nxt, rcv_nxt;
  uint16_t unacked;                                 /* bytes of the request not yet acknowledged */
  uint16_t body;                                    /* body bytes received so far */
  uint8_t  http;                                    /* 0 status line, 1 headers, 2 body */
  uint8_t  crlf;                                    /* the \r\n\r\n detector */
  uint8_t  frame;                                   /* 0 want 7F, 1 type, 2-4 length bytes, 5 skipping */
  uint8_t  ftype;
  uint32_t flen;
  uint8_t  line[24];
  uint8_t  linelen;
  uint8_t  kind;                                    /* REQ_POLL or REQ_IMAGE */
  uint8_t  ok;                                      /* the status line said 2xx */
  uint16_t word;                                    /* frame 10's two bytes */
} t;

static void tcp_send(uint8_t flags, uint8_t with_request)
{
  ip_begin(IP_TCP, server_ip, hop_mac);
  put16(t.lport); put16(80);
  put32(t.snd_nxt); put32(flags & TCP_ACK ? t.rcv_nxt : 0);
  put8(flags & TCP_SYN ? 0x60 : 0x50); put8(flags);
  put16(2048); put16(0); put16(0);                  /* window, checksum, urgent */
  if(flags & TCP_SYN) { put8(2); put8(4); put16(1400); }   /* MSS */
  if(with_request) {
    uint8_t i;
    puts_code(t.kind == REQ_IMAGE ? "GET " FW_PATH : "GET " POLL_PATH);
    for(i = 0; i < 6; i++) put_hex_upper(our_mac[i]);
    /* st=00 on the first poll after a start only, as Violet: the Terrier
     * counts a start at each st=00, and Lazare's first native polls showed
     * one every ten seconds (2026-11-25). */
    if(t.kind == REQ_IMAGE) puts_code("&v=" FW_VERSION "&b=" STR(BOARD_ID));
    else puts_code(polls_answered ? "&v=" FW_VERSION "&st=01&tc=00000000" : "&v=" FW_VERSION "&st=00&tc=00000000");
    puts_code(" HTTP/1.0\r\nUser-Agent: VIOLET\r\nHost: " SERVER_NAME "\r\n\r\n");
    t.unacked = txp - tp_at - 20;
  }
  ip_send(16);
}

static void tcp_close(const __code char *why)
{
  if(why[0] == 'r' && why[2] == 'f') refused++;
  else if(why[0] == 'r') resets++;
  else if(why[0] == 't') timeouts++;
  t.st = T_CLOSED;
  log_s("http: "); log_line(why);
  timer = ticks();
  if(t.kind == REQ_IMAGE) update_end(0);
}

static void http_open(uint8_t kind)
{
  t.kind = kind; t.ok = 0;
  if(kind == REQ_IMAGE) update_begin();
  t.lport = 0x1000 | (ticks() & 0x0FFF);
  t.snd_nxt = ((uint32_t)ticks() << 16) | (uint16_t)(our_mac[5] << 8) | our_mac[4];
  t.unacked = 0; t.body = 0; t.http = 0; t.crlf = 0; t.frame = 0; t.linelen = 0;
  t.st = T_SYN_SENT;
  tcp_send(TCP_SYN, 0);
  t.snd_nxt++;
  timer = ticks();
}

/* One byte of the answer: the status line is logged, the headers skipped,
 * the body's frames (7F, then type + 3-byte length each, then FF) listed. */
static void http_byte(uint8_t c)
{
  if(t.http == 0) {
    if(c == '\n') {
      uint8_t i;
      log_s("http: ");
      for(i = 0; i < t.linelen; i++) uart_putc((char)t.line[i]);
      log_nl();
      t.ok = t.linelen > 9 && t.line[9] == '2';
      t.http = 1; t.crlf = 2;
    } else if(c != '\r' && t.linelen < sizeof(t.line)) t.line[t.linelen++] = c;
    return;
  }
  if(t.http == 1) {
    if(c == '\r') return;
    if(c == '\n') { if(++t.crlf == 2) t.http = 2; }
    else t.crlf = 0;
    return;
  }
  t.body++;
  if(t.kind == REQ_IMAGE) { if(t.ok) update_byte(c); return; }
  switch(t.frame) {
    case 0:
      if(c == 0x7F) { log_line("answer: 7F"); t.frame = 1; }
      else { log_s("answer: "); log_hex8(c); log_nl(); t.frame = 6; }
      break;
    case 1:
      if(c == 0xFF) { log_line("answer: FF end"); t.frame = 6; break; }
      t.ftype = c; t.flen = 0; t.frame = 2;
      break;
    case 2: case 3: case 4:
      t.flen = (t.flen << 8) | c;
      if(++t.frame == 5) {
        log_s("frame "); log_hex8(t.ftype); log_s(" len "); log_dec((uint16_t)t.flen); log_nl();
        if(t.flen == 0) t.frame = 1;
      }
      break;
    case 5:
      if(t.ftype == 3 && t.flen == 1) { log_s("poll interval "); log_dec(c); log_nl(); }
      if(t.ftype == 0x10) {                         /* ours: the image version the Terrier holds */
        if(t.flen == 2) t.word = (uint16_t)c << 8;
        else if(t.flen == 1) update_offer(t.word | c);
      }
      if(--t.flen == 0) t.frame = 1;
      break;
    default: break;
  }
}

static void tcp_rx(uint16_t len)
{
  uint16_t sport, dport, hl, dlen;
  uint32_t seq, ack;
  uint8_t flags;
  sport = rx_be16(); dport = rx_be16();
  seq = (uint32_t)rx_be16() << 16; seq |= rx_be16();
  ack = (uint32_t)rx_be16() << 16; ack |= rx_be16();
  hl = (rx_byte() >> 4) * 4; flags = rx_byte();
  rx_skip(6);                                       /* window, checksum, urgent */
  if(hl > 20) rx_skip(hl - 20);
  if(dport != t.lport || sport != 80 || t.st == T_CLOSED || len < hl) return;
  dlen = len - hl;
  if(flags & TCP_RST) { tcp_close(t.st == T_SYN_SENT ? "refused" : "reset"); return; }
  if(t.st == T_SYN_SENT) {
    if((flags & (TCP_SYN | TCP_ACK)) != (TCP_SYN | TCP_ACK) || ack != t.snd_nxt) return;
    established++;
    t.rcv_nxt = seq + 1;
    t.st = T_OPEN;
    tcp_send(TCP_ACK | TCP_PSH, 1);                 /* the request rides on the handshake's ACK */
    t.snd_nxt += t.unacked;
    timer = ticks();
    return;
  }
  if(flags & TCP_ACK && ack == t.snd_nxt) t.unacked = 0;
  if(t.st == T_FIN_WAIT) { if(ack == t.snd_nxt) t.st = T_CLOSED; return; }
  if(seq != t.rcv_nxt) { tcp_send(TCP_ACK, 0); return; }   /* out of order: say where we are */
  while(dlen--) http_byte(rx_byte());
  t.rcv_nxt += len - hl;
  timer = ticks();
  if(flags & TCP_FIN) {
    t.rcv_nxt++;
    tcp_send(TCP_FIN | TCP_ACK, 0);
    t.snd_nxt++;
    t.st = T_FIN_WAIT;
    tries = 0;
    if(t.kind == REQ_IMAGE) update_end(1);
    else {
      polls_answered++;
      log_s("poll "); log_dec(polls_answered); log_s(" done, "); log_dec(t.body); log_line(" B");
    }
  } else if(len > hl) tcp_send(TCP_ACK, 0);
}

/* --- dispatch ------------------------------------------------------------ */

static void ip_rx(uint16_t len)
{
  uint8_t vhl, proto, src[4], dst[4];
  uint16_t total, hl;
  if(len < 20) return;
  vhl = rx_byte(); rx_byte(); total = rx_be16();
  rx_skip(5); proto = rx_byte(); rx_be16();
  rx_bytes(src, 4); rx_bytes(dst, 4);
  hl = (vhl & 15) * 4;
  if((vhl >> 4) != 4 || hl < 20 || total < hl || total > len) return;
  if(hl > 20) rx_skip(hl - 20);
  total -= hl;
  if(state != NET_DHCP && !same_ip(dst, our_ip) && !same_ip(dst, bcast_ip)) return;
  if(proto == IP_UDP) {
    uint16_t sport = rx_be16(), dport = rx_be16(), ulen = rx_be16();
    rx_be16();
    if(ulen < 8 || ulen > total) return;
    if(dport == 68 && sport == 67 && state == NET_DHCP) { dhcp_replies++; dhcp_rx(ulen - 8); }
    else if(sport == 53 && dport == dns_port && state == NET_DNS) { dns_replies++; dns_rx(); }
  } else if(proto == IP_TCP && state == NET_READY && same_ip(src, server_ip)) {
    tcp_rx(total);
  } else if(proto == IP_ICMP && state != NET_DHCP) {
    icmp_rx(src, total);
  }
}

void net_rx(uint16_t len)
{
  uint16_t type;
  uint8_t i;
  if(len < 8) return;
  for(i = 0; i < 8; i++) last_head[i] = rx_byte();
  if(last_head[0] != 0xAA || last_head[1] != 0xAA || last_head[2] != 0x03) { not_snap++; return; }
  type = ((uint16_t)last_head[6] << 8) | last_head[7];
  len -= 8;
  if(type == ETH_ARP && len >= 28) { arp_frames++; arp_rx(); }
  else if(type == ETH_IP) { ip_frames++; ip_rx(len); }
}

/* --- the clock ----------------------------------------------------------- */

void net_init(void)
{
  uint8_t i;
  for(i = 0; i < 6; i++) bcast_mac[i] = 0xFF;
  for(i = 0; i < 4; i++) bcast_ip[i] = 0xFF;
  state = NET_DOWN;
}

uint8_t net_state(void) { return state; }

/* Every step of the way, 2 minutes after the start, whatever happened
 * (Lazare associated and went no further, 2026-11-25; Clara: "all the
 * steps, to limit the round trips"). EEPROM from 0x80, read with
 * v1-icsp/card-report.py. Written once.
 *   80 'N' 2   82 state now, furthest state, tries, have_hop
 *   86 rx frames, rx bad, tx frames, tx exceptions (2 each)
 *   8E not SNAP, ARP, IP, DHCP replies, DNS replies (2 each)
 *   98 last rx status (2)   9A the last frame's first 8 bytes
 *   A2 our IP, A6 mask, AA gateway, AE DNS, B2 the Terrier's IP (4 each)
 *   B6 connects, established, refused, resets, timeouts, TCP state, HTTP
 *      phase, status 2xx (1 each)   BE polls answered (2)
 *   C0 EV_RX raised, its BAP refused, every EVSTAT bit seen (2 each)
 *   C6 transmits never confirmed, the last frame's status word, the transmit
 *      command's STATUS (2 each)
 *   CC the last frame handed to the card, its first 16 bytes
 *   DC BSSID (6), E2 port status (2), E4 SSID length + 8 bytes, EE our MAC (6) */
void net_report(void)
{
  uint16_t w[5];
  uint8_t i;
  ee_put(0x80, 'N'); ee_put(0x81, 2);
  ee_put(0x82, state); ee_put(0x83, max_state); ee_put(0x84, tries); ee_put(0x85, have_hop);
  w[0] = rx_frames; w[1] = rx_bad; w[2] = tx_frames; w[3] = tx_exc; ee_words(0x86, w, 4);
  w[0] = not_snap; w[1] = arp_frames; w[2] = ip_frames; w[3] = dhcp_replies; w[4] = dns_replies; ee_words(0x8E, w, 5);
  ee_words(0x98, &rx_status_last, 1);
  for(i = 0; i < 8; i++) ee_put(0x9A + i, last_head[i]);
  for(i = 0; i < 4; i++) {
    ee_put(0xA2 + i, our_ip[i]); ee_put(0xA6 + i, mask[i]); ee_put(0xAA + i, gw_ip[i]);
    ee_put(0xAE + i, dns_ip[i]); ee_put(0xB2 + i, server_ip[i]);
  }
  ee_put(0xB6, connects); ee_put(0xB7, established); ee_put(0xB8, refused); ee_put(0xB9, resets);
  ee_put(0xBA, timeouts); ee_put(0xBB, t.st); ee_put(0xBC, t.http); ee_put(0xBD, t.ok);
  ee_words(0xBE, &polls_answered, 1);
  w[0] = rx_events; w[1] = rx_bap_fail; w[2] = ev_seen; ee_words(0xC0, w, 3);
  w[0] = tx_lost; w[1] = tx_status_last; w[2] = tx_cmd_status; ee_words(0xC6, w, 3);
  for(i = 0; i < 16; i++) ee_put(0xCC + i, tx_head[i]);
  prism_report_link(0xDC);
  log_line("net: report in EEPROM 0x80");
}

void net_poll(void)
{
  uint16_t age;
  if(!prism_link()) {
    if(state != NET_DOWN) { log_line("net: link lost"); state = NET_DOWN; t.st = T_CLOSED; }
    return;
  }
  if(state == NET_DOWN) {
    state = NET_DHCP;
    xid = ((uint32_t)our_mac[2] << 24) | ((uint32_t)our_mac[3] << 16) | ((uint32_t)our_mac[4] << 8) | our_mac[5];
    xid ^= ticks();
    tries = 0;
    timer = ticks() - RETRY;
  }
  age = ticks() - timer;
  if(state > max_state) max_state = state;
  switch(state) {
    case NET_DHCP:
      if(age >= RETRY) {
        if(++tries > 20) { log_line("dhcp: give up"); state = NET_DOWN; prism_join(SSID); return; }
        log_line("dhcp: discover");
        dhcp_send(1, 0, 0);
        timer = ticks();
      }
      break;
    case NET_DNS:
      if(age >= RETRY) {
        if(!have_hop) { arp_send(1, bcast_mac, hop_ip, bcast_mac); timer = ticks() - RETRY + MS_TO_TICKS(500); return; }
        if(++tries > 20) { log_line("dns: none, dhcp again"); state = NET_DOWN; return; }
        log_line("dns: query");
        dns_send();
        timer = ticks();
      }
      break;
    case NET_READY:
      if(t.st == T_CLOSED) {
        if(update_wanted() && have_hop) {           /* the image, right after the poll that offered it */
          log_line("http: image");
          http_open(REQ_IMAGE);
          return;
        }
        if(age < POLL_INTERVAL) return;
        if(!have_hop) { arp_send(1, bcast_mac, hop_ip, bcast_mac); timer = ticks() - POLL_INTERVAL + MS_TO_TICKS(500); return; }
        if(++tries > 3) {                           /* the server is not there: ask the name again */
          log_line("http: resolve again");
          state = NET_DNS; tries = 0; copy4(hop_ip, dns_ip); have_hop = 0; timer = ticks() - RETRY;
          return;
        }
        log_s("http: connect "); log_ip(server_ip); log_nl();
        connects++;
        http_open(REQ_POLL);
      } else if(t.st == T_SYN_SENT) {
        if(age >= RETRY) { t.snd_nxt--; tcp_send(TCP_SYN, 0); t.snd_nxt++; timer = ticks(); log_line("http: syn again"); }
      } else if(t.st == T_OPEN) {
        if(t.unacked && age >= RETRY) { t.snd_nxt -= t.unacked; tcp_send(TCP_ACK | TCP_PSH, 1); t.snd_nxt += t.unacked; timer = ticks(); }
        else if(age >= 3 * RETRY) { tcp_send(TCP_RST | TCP_ACK, 0); tcp_close("timeout"); }
      } else if(t.st == T_FIN_WAIT) {
        if(age >= RETRY) t.st = T_CLOSED;
      }
      break;
    default: break;
  }
}
