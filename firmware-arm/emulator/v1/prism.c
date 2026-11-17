/**
 * @file prism.c
 * @brief The PCMCIA Wi-Fi card: an Intersil Prism 2 (HFA384x) in I/O mode.
 *
 * What the v1 firmware does with it, seen through this model: write the COR
 * in attribute memory (0xE0 <- 01, I/O mode), INITIALIZE, then commands,
 * RIDs and frame buffers through the BAPs, polling EVSTAT -- it never
 * enables an interrupt. Registers are 16 bits, little-endian, at even
 * addresses; the bus is 8 bits wide, so a 16-bit access is two byte
 * accesses and the odd (high) one is the one that acts.
 *
 * The card does 802.11 itself, WEP included, so this model has no radio:
 * ENABLE reports "connected" and frames go straight to and from gw.c.
 */
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "prism.h"

#define R_CMD      0x00
#define R_PARAM0   0x02
#define R_PARAM1   0x04
#define R_PARAM2   0x06
#define R_STATUS   0x08
#define R_RESP0    0x0A
#define R_RESP1    0x0C
#define R_RESP2    0x0E
#define R_INFOFID  0x10
#define R_CONTROL  0x14
#define R_SELECT0  0x18
#define R_SELECT1  0x1A
#define R_OFFSET0  0x1C
#define R_OFFSET1  0x1E
#define R_RXFID    0x20
#define R_ALLOCFID 0x22
#define R_TXCFID   0x24
#define R_EVSTAT   0x30
#define R_INTEN    0x32
#define R_EVACK    0x34
#define R_DATA0    0x36
#define R_DATA1    0x38

#define EV_RX      0x0001
#define EV_TX      0x0002
#define EV_TXEXC   0x0004
#define EV_ALLOC   0x0008
#define EV_CMD     0x0010
#define EV_INFO    0x0080

#define NBUF       48
#define BUFSIZE    2400
#define NRID       64

struct fidbuf { uint16_t fid; int used; uint8_t data[BUFSIZE]; };
struct rid    { uint16_t type; uint16_t len; uint8_t data[256]; };

static uint16_t reg[32];             /* by word: reg[addr/2] */
static uint8_t  attr[256];
static struct fidbuf bufs[NBUF];
static struct rid rids[NRID];
static uint16_t next_fid = 0x0100;
static int enabled, port_type;
static int connected_reported;
static uint64_t link_at;             /* when to report the link */

/* A BAP: what is selected, where we are, and the bytes of a RID read. */
struct bap { uint16_t sel; uint16_t off; uint8_t *mem; unsigned size; int is_rid; struct rid *rid; uint8_t lo; };
static struct bap bap[2];
static uint8_t rid_scratch[2][260];

uint8_t prism_mac[6] = { 0x00, 0x19, 0xdb, 0x00, 0xbe, 0xef };
char prism_ssid[33] = "Terrier";

static struct fidbuf *fid_find(uint16_t fid)
{
  for(int i = 0; i < NBUF; i++) if(bufs[i].used && bufs[i].fid == fid) return &bufs[i];
  return NULL;
}

static struct fidbuf *fid_new(void)
{
  for(int i = 0; i < NBUF; i++) if(!bufs[i].used) {
    bufs[i].used = 1;
    bufs[i].fid = next_fid;
    next_fid += 2;
    if(next_fid >= 0x0F00) next_fid = 0x0100;
    memset(bufs[i].data, 0, BUFSIZE);
    return &bufs[i];
  }
  board_log("card: out of frame buffers");
  return NULL;
}

static void fid_free(uint16_t fid)
{
  struct fidbuf *b = fid_find(fid);
  if(b) b->used = 0;
}

static struct rid *rid_find(uint16_t type, int create)
{
  for(int i = 0; i < NRID; i++) if(rids[i].type == type) return &rids[i];
  if(!create) return NULL;
  for(int i = 0; i < NRID; i++) if(!rids[i].type) { rids[i].type = type; return &rids[i]; }
  return NULL;
}

static void rid_set(uint16_t type, const void *data, uint16_t bytes)
{
  struct rid *r = rid_find(type, 1);
  memcpy(r->data, data, bytes);
  r->len = bytes;
}

static void rid_set_words(uint16_t type, int n, ...)
{
  uint8_t d[32];
  __builtin_va_list ap;
  __builtin_va_start(ap, n);
  for(int i = 0; i < n; i++) { unsigned v = __builtin_va_arg(ap, unsigned); d[2*i] = (uint8_t)v; d[2*i+1] = (uint8_t)(v >> 8); }
  __builtin_va_end(ap);
  rid_set(type, d, (uint16_t)(2 * n));
}

static const char *rid_name(uint16_t t)
{
  switch(t) {
    case 0xFC00: return "port type";
    case 0xFC01: return "own MAC";
    case 0xFC02: return "desired SSID";
    case 0xFC03: return "own channel";
    case 0xFC04: return "own SSID";
    case 0xFC05: return "ATIM window";
    case 0xFC06: return "system scale";
    case 0xFC07: return "max data length";
    case 0xFC08: return "WDS address";
    case 0xFC09: return "PM enabled";
    case 0xFC0E: return "TX rate control";
    case 0xFC20: return "WEP default key id";
    case 0xFC21: return "WEP key 0 (old)";
    case 0xFC23: return "WEP default key id";
    case 0xFC24: return "WEP key 0";
    case 0xFC25: return "WEP key 1";
    case 0xFC26: return "WEP key 2";
    case 0xFC27: return "WEP key 3";
    case 0xFC28: return "WEP flags";
    case 0xFC2A: return "authentication";
    case 0xFC84: return "basic rates";
    case 0xFC85: return "supported rates";
    case 0xFC86: return "fragmentation threshold";
    case 0xFCB0: return "TX rate";
    case 0xFD02: return "primary identity";
    case 0xFD0B: return "NIC identity";
    case 0xFD20: return "station identity";
    case 0xFD40: return "port status";
    case 0xFD41: return "current SSID";
    case 0xFD42: return "current BSSID";
    case 0xFD43: return "comms quality";
    case 0xFD44: return "current TX rate";
    case 0xFD4D: return "current channel";
  }
  return "?";
}

void prism_reset(void)
{
  memset(reg, 0, sizeof(reg));
  memset(attr, 0xFF, sizeof(attr));
  memset(bufs, 0, sizeof(bufs));
  memset(rids, 0, sizeof(rids));
  memset(bap, 0, sizeof(bap));
  enabled = 0;
  connected_reported = 0;
  /* What a Prism 2.5 answers before it is configured. */
  rid_set(0xFC01, prism_mac, 6);
  rid_set_words(0xFD0B, 4, 0x8013, 1, 1, 0);        /* NIC: Prism 2.5 */
  rid_set_words(0xFD02, 4, 0x0015, 1, 1, 1);        /* primary 1.1.1 */
  rid_set_words(0xFD20, 4, 0x001F, 1, 1, 7);        /* station 1.7.x */
  rid_set_words(0xFC00, 1, 1);                      /* BSS */
  rid_set_words(0xFC07, 1, 2304);
  rid_set_words(0xFD40, 1, 1);                      /* disabled */
}

static void event(uint16_t bits) { reg[R_EVSTAT / 2] |= bits; }

/* --- info frames --------------------------------------------------------- */

static void info_frame(uint16_t type, const uint8_t *body, uint16_t bytes)
{
  struct fidbuf *b = fid_new();
  uint16_t words = (uint16_t)(1 + (bytes + 1) / 2);
  if(!b) return;
  b->data[0] = (uint8_t)words; b->data[1] = (uint8_t)(words >> 8);
  b->data[2] = (uint8_t)type;  b->data[3] = (uint8_t)(type >> 8);
  memcpy(b->data + 4, body, bytes);
  reg[R_INFOFID / 2] = b->fid;
  event(EV_INFO);
}

static void link_status(uint16_t st)
{
  if(st == 1) {
    static const uint8_t bssid[6] = { 0x02, 0x00, 0x5e, 0x10, 0x00, 0x01 };
    uint8_t ss[34] = { 0 };
    size_t n = strlen(prism_ssid);
    ss[0] = (uint8_t)n;
    memcpy(ss + 2, prism_ssid, n);
    rid_set_words(0xFD40, 1, port_type == 3 ? 3 : 4);   /* IBSS / BSS connected */
    rid_set(0xFD41, ss, 34);
    rid_set(0xFD42, bssid, 6);
    rid_set_words(0xFD43, 3, 60, 30, 90);
    rid_set_words(0xFD44, 1, 11);
    rid_set_words(0xFD4D, 1, 6);
  }
  uint8_t d[2] = { (uint8_t)st, (uint8_t)(st >> 8) };
  board_log("card: link status %u (%s)", st, st == 1 ? "connected" : st == 2 ? "disconnected" : "other");
  info_frame(0xF200, d, 2);
}

/* --- commands ------------------------------------------------------------ */

static void command(uint16_t cmd)
{
  uint16_t code = cmd & 0x3F, p0 = reg[R_PARAM0 / 2];
  uint16_t result = 0;

  reg[R_RESP0 / 2] = 0;
  switch(code) {
    case 0x00:                                     /* INITIALIZE */
      board_log("card: initialize");
      enabled = 0;
      break;
    case 0x01: {                                   /* ENABLE */
      struct rid *pt = rid_find(0xFC00, 0);
      struct rid *ss = rid_find(0xFC02, 0);
      char ssid[33] = "";
      port_type = pt ? pt->data[0] : 1;
      if(ss && ss->len >= 2) {
        unsigned n = ss->data[0] | (ss->data[1] << 8);
        if(n > 32) n = 32;
        memcpy(ssid, ss->data + 2, n);
        ssid[n] = 0;
      }
      board_log("card: enable, port type %d (%s), SSID \"%s\"", port_type,
                port_type == 1 ? "BSS" : port_type == 3 ? "ad-hoc" : "?", ssid);
      enabled = 1;
      connected_reported = 0;
      link_at = board_ms() + 300;
      break;
    }
    case 0x02:                                     /* DISABLE */
      board_log("card: disable");
      enabled = 0;
      break;
    case 0x0A: {                                   /* ALLOCATE */
      struct fidbuf *b = fid_new();
      if(b) {
        reg[R_ALLOCFID / 2] = b->fid;
        event(EV_ALLOC);
        if(B.trace_bus) board_log("card: allocate %u bytes -> fid %04X", p0, b->fid);
      } else result = 0x7F;
      break;
    }
    case 0x0B: {                                   /* TRANSMIT */
      struct fidbuf *b = fid_find(p0);
      if(b) prism_transmitted(b->data, BUFSIZE);
      reg[R_TXCFID / 2] = p0;
      event(EV_TX);
      if(!(cmd & 0x100)) {}                        /* no reclaim: buffer stays */
      break;
    }
    case 0x11:                                     /* INQUIRE */
      board_log("card: inquire %04X", p0);
      if(p0 == 0xF101) prism_scan_results();
      break;
    case 0x21: {                                   /* ACCESS */
      struct rid *r = rid_find(p0, 0);
      if(cmd & 0x100) {
        if(r) board_log("card: RID %04X %s <- %u bytes", p0, rid_name(p0), r->len);
      } else if(!r) {
        board_log("card: RID %04X %s read, which this card does not know", p0, rid_name(p0));
      }
      break;
    }
    case 0x38:                                     /* MONITOR */
      break;
    default:
      board_log("card: command %04X not modelled", cmd);
      break;
  }
  reg[R_STATUS / 2] = (uint16_t)(code | (result << 8));
  reg[R_CMD / 2] = cmd & 0x7FFF;
  event(EV_CMD);
}

/* --- BAPs ---------------------------------------------------------------- */

static void bap_seek(int n)
{
  struct bap *p = &bap[n];
  uint16_t sel = p->sel;
  p->mem = NULL;
  p->is_rid = sel >= 0xF000 || (sel >= 0xFC00);
  if(p->is_rid) {
    struct rid *r = rid_find(sel, 1);
    uint16_t words = (uint16_t)(1 + (r->len + 1) / 2);
    p->rid = r;
    rid_scratch[n][0] = (uint8_t)words; rid_scratch[n][1] = (uint8_t)(words >> 8);
    rid_scratch[n][2] = (uint8_t)sel;   rid_scratch[n][3] = (uint8_t)(sel >> 8);
    memcpy(rid_scratch[n] + 4, r->data, r->len);
    p->mem = rid_scratch[n];
    p->size = sizeof(rid_scratch[n]);
  } else {
    struct fidbuf *b = fid_find(sel);
    if(b) { p->mem = b->data; p->size = BUFSIZE; }
    else board_log("card: BAP%d select %04X, no such buffer", n, sel);
  }
}

/* A RID written through a BAP takes effect on the ACCESS write command;
 * keep the bytes as they arrive. */
static void bap_store(int n, uint16_t word)
{
  struct bap *p = &bap[n];
  if(!p->mem) return;
  if((unsigned)p->off + 1u < p->size) { p->mem[p->off] = (uint8_t)word; p->mem[p->off + 1] = (uint8_t)(word >> 8); }
  if(p->is_rid && p->off >= 4) {
    unsigned at = p->off - 4u;
    if(at + 1 < sizeof(p->rid->data)) {
      p->rid->data[at] = (uint8_t)word; p->rid->data[at + 1] = (uint8_t)(word >> 8);
      if(at + 2 > p->rid->len) p->rid->len = (uint16_t)(at + 2);
    }
  }
  if(p->is_rid && p->off == 0) p->rid->len = (uint16_t)((word ? word - 1 : 0) * 2);
  p->off += 2;
}

static uint16_t bap_load(int n)
{
  struct bap *p = &bap[n];
  uint16_t w = 0;
  if(p->mem && (unsigned)p->off + 1u < p->size) w = (uint16_t)(p->mem[p->off] | (p->mem[p->off + 1] << 8));
  p->off += 2;
  return w;
}

/* --- the bus ------------------------------------------------------------- */

uint8_t prism_io_read(uint8_t a)
{
  uint8_t v;
  a &= 0x3F;
  if(a == R_DATA0 || a == R_DATA1) {
    int n = a == R_DATA1;
    uint16_t w = bap_load(n);
    bap[n].lo = (uint8_t)(w >> 8);
    bap[n].off -= 2;
    return (uint8_t)w;
  }
  if(a == R_DATA0 + 1 || a == R_DATA1 + 1) {
    int n = a == R_DATA1 + 1;
    bap[n].off += 2;
    return bap[n].lo;
  }
  if(a == R_OFFSET0 || a == R_OFFSET0 + 1 || a == R_OFFSET1 || a == R_OFFSET1 + 1) {
    int n = a >= R_OFFSET1;
    uint16_t w = (uint16_t)(bap[n].off & 0x0FFF);
    return (a & 1) ? (uint8_t)(w >> 8) : (uint8_t)w;
  }
  v = (a & 1) ? (uint8_t)(reg[a / 2] >> 8) : (uint8_t)reg[a / 2];
  return v;
}

static uint8_t pending_lo[64];

void prism_io_write(uint8_t a, uint8_t v)
{
  a &= 0x3F;
  if(!(a & 1)) { pending_lo[a] = v; return; }
  uint16_t w = (uint16_t)(pending_lo[a - 1] | (v << 8));
  uint8_t r = (uint8_t)(a - 1);
  switch(r) {
    case R_CMD:
      if(B.trace_bus) board_log("card: command %04X param %04X", w, reg[R_PARAM0 / 2]);
      command(w);
      return;
    case R_EVACK:
      reg[R_EVSTAT / 2] &= (uint16_t)~w;
      if(w & EV_INFO) fid_free(reg[R_INFOFID / 2]);
      if(w & EV_RX) fid_free(reg[R_RXFID / 2]);
      return;
    case R_SELECT0: case R_SELECT1:
      bap[r == R_SELECT1].sel = w;
      return;
    case R_OFFSET0: case R_OFFSET1: {
      int n = r == R_OFFSET1;
      bap[n].off = w & 0x0FFF;
      bap_seek(n);
      return;
    }
    case R_DATA0: case R_DATA1:
      bap_store(r == R_DATA1, w);
      return;
  }
  reg[r / 2] = w;
}

uint8_t prism_attr_read(uint8_t a)
{
  return attr[a];
}

void prism_attr_write(uint8_t a, uint8_t v)
{
  if(a == 0xE0) board_log("card: COR <- %02X (%s)", v, (v & 0x3F) ? "I/O mode" : "memory mode");
  attr[a] = v;
}

void prism_tick(void)
{
  if(enabled && !connected_reported && board_ms() >= link_at && !(reg[R_EVSTAT / 2] & EV_INFO)) {
    connected_reported = 1;
    link_status(1);
  }
}

/* --- frames -------------------------------------------------------------- */

int prism_link_up(void) { return enabled && connected_reported; }

/* A frame for the rabbit: the Prism RX descriptor, an 802.11 header, the
 * 802.3 header the card builds for its host, and the payload (LLC/SNAP). */
void prism_receive(const uint8_t *dst, const uint8_t *src, const uint8_t *llc, unsigned len)
{
  struct fidbuf *b;
  if(!enabled) return;
  if(reg[R_EVSTAT / 2] & EV_RX) {
    if(B.trace_bus) board_log("card: a frame arrived before the last one was taken, queued");
  }
  b = fid_new();
  if(!b) return;
  /* 0 status, 2 time, 6 silence, 7 signal, 8 rate, 9 rxflow, 10 reserved */
  b->data[7] = 0x40;
  b->data[8] = 110;
  /* 14 frame control: data, from DS */
  b->data[14] = 0x08; b->data[15] = 0x02;
  memcpy(b->data + 18, dst, 6);
  memcpy(b->data + 24, src, 6);
  memcpy(b->data + 30, src, 6);
  b->data[44] = (uint8_t)len; b->data[45] = (uint8_t)(len >> 8);
  memcpy(b->data + 46, dst, 6);
  memcpy(b->data + 52, src, 6);
  b->data[58] = (uint8_t)(len >> 8); b->data[59] = (uint8_t)len;
  memcpy(b->data + 60, llc, len < BUFSIZE - 60 ? len : BUFSIZE - 60);
  prism_rx_queue(b->fid);
}

/* RX frames wait here until the firmware has acknowledged the last one. */
static uint16_t rxq[NBUF];
static int rxq_n;

void prism_rx_queue(uint16_t fid)
{
  if(rxq_n < NBUF) rxq[rxq_n++] = fid;
}

void prism_rx_pump(void)
{
  if(rxq_n == 0 || (reg[R_EVSTAT / 2] & EV_RX)) return;
  reg[R_RXFID / 2] = rxq[0];
  memmove(rxq, rxq + 1, (size_t)(--rxq_n) * sizeof(rxq[0]));
  event(EV_RX);
}

void prism_scan_results(void)
{
  /* One AP. Prism 2 ScanResult: reserved, scan reason, then per AP:
   * chid, anl, sl, bssid[6], beacon interval, capinfo, ssid len, ssid[32],
   * rates[10], rate. */
  uint8_t d[4 + 62];
  memset(d, 0, sizeof(d));
  d[2] = 1;
  uint8_t *ap = d + 4;
  ap[0] = 6;  ap[2] = 0x10; ap[4] = 0x50;
  static const uint8_t bssid[6] = { 0x02, 0x00, 0x5e, 0x10, 0x00, 0x01 };
  memcpy(ap + 6, bssid, 6);
  ap[12] = 100;
  ap[14] = 0x01;
  ap[16] = (uint8_t)strlen(prism_ssid);
  memcpy(ap + 18, prism_ssid, strlen(prism_ssid));
  ap[50] = 0x82; ap[51] = 0x84; ap[52] = 0x8b; ap[53] = 0x96;
  ap[60] = 0x96;
  board_log("card: scan results, one network \"%s\"", prism_ssid);
  info_frame(0xF101, d, sizeof(d));
}
