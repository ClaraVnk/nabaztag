/*
 * prism.c -- the PCMCIA Wi-Fi card, an Intersil Prism 2/2.5 (HFA384x) in I/O
 * mode on an 8-bit bus the PIC drives by hand.
 *
 * Read from: Violet's 1.4b bus routines as the emulator models them
 * (emulator/v1/prism.c: a 16-bit register is two byte accesses, the odd
 * (high) one acts; COR in attribute memory; commands, RIDs and frame buffers
 * through the BAPs; events polled in EVSTAT, never an interrupt), and the
 * Prism 2 programmer's model as hostap's hostap_hw.c / hfa384x.h describe
 * it (GPL, read, nothing copied: register offsets, command codes, the
 * TX/RX descriptors). The card does 802.11 itself: the host writes 802.3
 * frames whose payload is LLC/SNAP (RFC 1042), and gets the same back.
 */
#include "board.h"

#define R_CMD      0x00
#define R_PARAM0   0x02
#define R_STATUS   0x08
#define R_INFOFID  0x10
#define R_SELECT0  0x18
#define R_OFFSET0  0x1C
#define R_RXFID    0x20
#define R_ALLOCFID 0x22
#define R_EVSTAT   0x30
#define R_EVACK    0x34
#define R_DATA0    0x36

#define EV_RX      0x0001
#define EV_TX      0x0002
#define EV_TXEXC   0x0004
#define EV_ALLOC   0x0008
#define EV_CMD     0x0010
#define EV_INFO    0x0080

#define CMD_INIT      0x0000
#define CMD_ENABLE    0x0001
#define CMD_ALLOC     0x000A
#define CMD_TX_RECLAIM 0x010B
#define CMD_ACCESS    0x0021
#define CMD_ACCESS_WR 0x0121

#define RID_PORTTYPE  0xFC00
#define RID_MAC       0xFC01
#define RID_SSID      0xFC02
#define RID_MAXLEN    0xFC07
#define RID_WEPFLAGS  0xFC28
#define RID_AUTH      0xFC2A
#define RID_TXRATES   0xFCB0
#define RID_STAID     0xFD20
#define RID_OWNCHAN   0xFC03
#define RID_SCALE     0xFC06
#define RID_BASICRATES 0xFC84
#define RID_FCB3      0xFCB3
#define RID_FCB4      0xFCB4
#define INFO_LINK     0xF200

#define TX_BUF_BYTES  1600
#define WAIT_LOOPS    20000u         /* ~100 ms of register polling */

__data uint8_t our_mac[6];
static uint16_t txfid;
static uint8_t  linked;
static uint8_t  info_count;             /* info frames since the join (telemetry) */
static uint16_t info_last, link_last;   /* the last info type, the last LinkStatus */

/* --- the bus ------------------------------------------------------------ */

static uint8_t rd8(uint8_t a)
{
  uint8_t v;
  CARD_DATA_TRIS = 0xFF;
  CARD_ADDR = a;
  CARD_IORD = 0;
  nop();
  v = CARD_DATA_IN;
  CARD_IORD = 1;
  return v;
}

static void wr8(uint8_t a, uint8_t v)
{
  CARD_ADDR = a;
  CARD_DATA_OUT = v;
  CARD_DATA_TRIS = 0;
  CARD_IOWR = 0;
  nop();
  CARD_IOWR = 1;
}

static uint16_t rd16(uint8_t r)
{
  uint8_t lo = rd8(r);
  return ((uint16_t)rd8(r + 1) << 8) | lo;
}

static void wr16(uint8_t r, uint16_t v)
{
  wr8(r, (uint8_t)v);
  wr8(r + 1, (uint8_t)(v >> 8));
}

static void attr_wr8(uint8_t a, uint8_t v)
{
  CARD_ADDR = a;
  CARD_DATA_OUT = v;
  CARD_DATA_TRIS = 0;
  CARD_WE = 0;
  nop();
  CARD_WE = 1;
}

/* Besides the bus, Violet's 1.4b drives three pins the emulator gives no
 * meaning to, measured with --trace-pins (2026-11-24): RG0 and RG3 low at
 * 3 ms, and RF4 low at 284 ms, 110 ms before its COR write. The card's
 * reset or power enable are likely among them; this firmware sets them the
 * same way and in the same order, and phase 2 reads the schematic. */
static void bus_init(void)
{
  CARD_IORD = 1; CARD_IOWR = 1; CARD_OE = 1; CARD_WE = 1;
  TRISE = 0;
  TRISFbits.TRISF5 = 0; TRISFbits.TRISF6 = 0; TRISFbits.TRISF7 = 0;
  TRISGbits.TRISG4 = 0;
  LATGbits.LATG0 = 0; LATGbits.LATG3 = 0;
  TRISGbits.TRISG0 = 0; TRISGbits.TRISG3 = 0;
  CARD_DATA_TRIS = 0xFF;
  delay_ms(100);
  LATFbits.LATF4 = 0; TRISFbits.TRISF4 = 0;
  delay_ms(100);
}

/* --- commands and BAPs -------------------------------------------------- */

static uint8_t wait_event(uint16_t bit)
{
  uint16_t n;
  for(n = 0; n < WAIT_LOOPS; n++) {
    if(rd16(R_EVSTAT) & bit) return 0;
    clrwdt();
  }
  return 1;
}

/* Returns the result code (STATUS bits 14..8), 0xFF on a timeout. */
static uint8_t cmd(uint16_t code, uint16_t p0)
{
  uint16_t st;
  wr16(R_PARAM0, p0);
  wr16(R_CMD, code);
  if(wait_event(EV_CMD)) return 0xFF;
  st = rd16(R_STATUS);
  wr16(R_EVACK, EV_CMD);
  return (uint8_t)((st >> 8) & 0x7F);
}

/* Select a buffer (FID or RID) in BAP n, at a byte offset; 0 when ready. */
static uint8_t bap_setup(uint8_t n, uint16_t sel, uint16_t off)
{
  uint16_t k, o;
  uint8_t rs = R_SELECT0 + 2 * n, ro = R_OFFSET0 + 2 * n;
  wr16(rs, sel);
  wr16(ro, off);
  for(k = 0; k < WAIT_LOOPS; k++) {
    o = rd16(ro);
    if(!(o & 0x8000)) return (o & 0x4000) ? 2 : 0;    /* busy clear; error bit */
  }
  return 1;
}

static uint16_t bap_rd(uint8_t n) { return rd16(R_DATA0 + 2 * n); }
static void bap_wr(uint8_t n, uint16_t v) { wr16(R_DATA0 + 2 * n, v); }

/* A RID read: words of its data land in buf (the length and type skipped). */
static uint8_t rid_read(uint16_t rid, __data uint16_t *buf, uint8_t words)
{
  uint16_t len;
  if(cmd(CMD_ACCESS, rid)) return 1;
  if(bap_setup(0, rid, 0)) return 2;
  /* Only the words the record holds (its length counts the type word):
   * past them the BAP gives stale bytes, and the first card report
   * (emulator, 2026-11-25) read the SSID record as an empty BSSID. */
  len = bap_rd(0);
  if(bap_rd(0) != rid) return 3;
  len = len ? len - 1 : 0;
  while(words--) *buf++ = len ? (len--, bap_rd(0)) : 0xFFFF;
  return 0;
}

static uint8_t rid_write(uint16_t rid, __data const uint16_t *buf, uint8_t words)
{
  if(bap_setup(0, rid, 0)) return 2;
  bap_wr(0, (uint16_t)(words + 1));
  bap_wr(0, rid);
  while(words--) bap_wr(0, *buf++);
  return cmd(CMD_ACCESS_WR, rid) ? 1 : 0;
}

static uint8_t rid_write1(uint16_t rid, uint16_t v)
{
  uint16_t w = v;
  return rid_write(rid, &w, 1);
}

/* --- init, join --------------------------------------------------------- */

uint8_t prism_init(void)
{
  uint16_t w[4];
  uint8_t r;
  bus_init();
  attr_wr8(0xE0, 0x01);                   /* COR: I/O mode, as Violet's firmware */
  delay_ms(50);                           /* (Violet: 0.2 s to INITIALIZE) */
  r = cmd(CMD_INIT, 0);
  if(r) { log_s("card: init failed "); log_hex8(r); log_nl(); return 1; }
  if(rid_read(RID_MAC, w, 3)) { log_line("card: no MAC"); return 2; }
  our_mac[0] = (uint8_t)w[0]; our_mac[1] = (uint8_t)(w[0] >> 8);
  our_mac[2] = (uint8_t)w[1]; our_mac[3] = (uint8_t)(w[1] >> 8);
  our_mac[4] = (uint8_t)w[2]; our_mac[5] = (uint8_t)(w[2] >> 8);
  log_s("card: mac "); log_mac(our_mac); log_nl();
  /* The station firmware (the WPA2 question, IMPROVEMENTS.md section 6):
   * component id, variant, major, minor. */
  if(!rid_read(RID_STAID, w, 4)) {
    log_s("card: station fw "); log_dec(w[2]); uart_putc('.'); log_dec(w[3]);
    uart_putc('.'); log_dec(w[1]); log_nl();
  }
  if(cmd(CMD_ALLOC, TX_BUF_BYTES) || wait_event(EV_ALLOC)) { log_line("card: no txbuf"); return 3; }
  txfid = rd16(R_ALLOCFID);
  wr16(R_EVACK, EV_ALLOC);
  return 0;
}

uint8_t prism_join(const __code char *ssid)
{
  uint16_t w[17];
  uint8_t n = 0, i;
  __data uint8_t *b = (__data uint8_t *)(w + 1);
  for(i = 0; i < 32; i++) b[i] = 0;
  while(ssid[n] && n < 32) { b[n] = (uint8_t)ssid[n]; n++; }
  w[0] = n;
  /* What Violet's 1.4b writes, in its order and with its values (the
   * emulator's RID trace, 2026-11-25): on Lazare our first set (no FC03,
   * FC06, FC84, FCB3, FCB4; 2304 and automatic rates) never associated,
   * the belly stuck at blue. Copied as they are before anything is
   * simplified; FCB3 and FCB4 are undocumented. */
  if(rid_write1(RID_PORTTYPE, 1)) return 1;          /* BSS: an access point */
  if(rid_write(RID_SSID, w, 17)) return 2;
  if(rid_write1(RID_OWNCHAN, 0)) return 3;
  if(rid_write1(RID_SCALE, 3)) return 3;
  if(rid_write1(RID_MAXLEN, 1514)) return 3;
  if(rid_write1(RID_BASICRATES, 0x000F)) return 6;
  if(rid_write1(RID_TXRATES, 0x0001)) return 6;      /* 1 Mb/s, as Violet's */
  if(rid_write1(RID_FCB3, 0x000F)) return 6;
  if(rid_write1(RID_FCB4, 0x000F)) return 6;
  if(rid_write1(RID_AUTH, 1)) return 4;              /* open system */
  if(rid_write1(RID_WEPFLAGS, 0)) return 5;          /* no WEP (phase 1) */
  linked = 0;
  info_count = 0; info_last = 0; link_last = 0;
  return cmd(CMD_ENABLE, 0) ? 7 : 0;
}

uint8_t prism_link(void) { return linked; }

/* What the card says when it does not associate, kept in the PIC's data
 * EEPROM from 0x40 (the loader's key is at 0x00-0x1F), read back over ICSP:
 * Lazare's native firmware stayed at blue twice (2026-11-24/25) and the
 * rabbit has no readable serial log, so the card's own answers go where a
 * PICkit can read them (as v1-icsp/wifi-probe.py did). Written once.
 *   40 'C' 1          magic, format
 *   42 join result, info frames seen, last info type (2), last LinkStatus (2)
 *   48 FD40 port status (2)     4A FD4D current channel (2)
 *   4C FD42 current BSSID (6)   52 FD43 comms quality, signal, noise (6)
 *   58 FD41 current SSID: length then 8 bytes */
static void ee_put(uint8_t a, uint8_t v)
{
  EEADRH = 0; EEADR = a; EEDATA = v;
  EECON1 = 0x04;                          /* the data EEPROM, WREN */
  __asm__("movlw 0x55\n\tmovwf _EECON2\n\tmovlw 0xaa\n\tmovwf _EECON2\n\tbsf _EECON1, 1");
  while(EECON1 & 0x02) clrwdt();
  EECON1 = 0;
}

static void ee_words(uint8_t a, __data const uint16_t *w, uint8_t n)
{
  while(n--) { ee_put(a++, (uint8_t)*w); ee_put(a++, (uint8_t)(*w >> 8)); w++; }
}

void prism_report(uint8_t join_result)
{
  uint16_t w[6];
  uint8_t i;
  ee_put(0x40, 'C'); ee_put(0x41, 1);
  ee_put(0x42, join_result); ee_put(0x43, info_count);
  ee_words(0x44, &info_last, 1); ee_words(0x46, &link_last, 1);
  for(i = 0; i < 6; i++) w[i] = 0xFFFF;
  rid_read(0xFD40, w, 1); ee_words(0x48, w, 1);
  rid_read(0xFD4D, w, 1); ee_words(0x4A, w, 1);
  rid_read(0xFD42, w, 3); ee_words(0x4C, w, 3);
  rid_read(0xFD43, w, 3); ee_words(0x52, w, 3);
  rid_read(0xFD41, w, 5); ee_words(0x58, w, 5);
  log_line("card: report in EEPROM 0x40");
}

/* --- receiving: the frame in BAP1, read in order ------------------------ */

static uint16_t rx_pos, rx_end;
static uint8_t  rx_hi, rx_have_hi;

void rx_seek(uint16_t offset)
{
  bap_setup(1, rd16(R_RXFID), offset & 0xFFFE);
  rx_have_hi = 0;
  rx_pos = offset & 0xFFFE;
  if(offset & 1) rx_byte();
}

uint8_t rx_byte(void)
{
  uint8_t v;
  if(rx_have_hi) { rx_have_hi = 0; v = rx_hi; }
  else {
    v = rd8(R_DATA0 + 2);                 /* the low byte: the word is loaded */
    rx_hi = rd8(R_DATA0 + 3);             /* the high byte: the BAP advances */
    rx_have_hi = 1;
  }
  rx_pos++;
  return v;
}

uint16_t rx_be16(void)
{
  uint8_t hi = rx_byte();
  return ((uint16_t)hi << 8) | rx_byte();
}

void rx_skip(uint16_t n) { while(n--) rx_byte(); }
void rx_bytes(__data uint8_t *dst, uint8_t n) { while(n--) *dst++ = rx_byte(); }
uint16_t rx_remaining(void) { return rx_end > rx_pos ? rx_end - rx_pos : 0; }

static void handle_rx(void)
{
  uint16_t fid = rd16(R_RXFID), status, len;
  if(bap_setup(1, fid, 0)) goto done;
  status = bap_rd(1);
  if(status & 0x0003) goto done;          /* FCS or other receive error */
  if(bap_setup(1, fid, 44)) goto done;
  rx_have_hi = 0;
  rx_pos = 44;
  len = rx_byte(); len |= (uint16_t)rx_byte() << 8;   /* data length, little-endian */
  rx_skip(14);                            /* the 802.3 header: dst, src, length */
  rx_end = 60 + len;
  net_rx(len);
done:
  wr16(R_EVACK, EV_RX);
}

static void handle_info(void)
{
  uint16_t fid = rd16(R_INFOFID), type;
  if(!bap_setup(1, fid, 0)) {
    bap_rd(1);                            /* length */
    type = bap_rd(1);
    info_count++; info_last = type;
    if(type == INFO_LINK) {
      uint16_t st = bap_rd(1);
      link_last = st;
      linked = st == 1;
      log_s("card: link "); log_dec(st); log_line(linked ? " up" : " down");
    }
  }
  wr16(R_EVACK, EV_INFO);
}

void prism_poll(void)
{
  uint16_t ev = rd16(R_EVSTAT);
  if(ev & EV_INFO) handle_info();
  if(ev & EV_RX) handle_rx();
  if(ev & EV_TXEXC) { log_line("card: tx exception"); wr16(R_EVACK, EV_TXEXC); }
  if(ev & EV_TX) wr16(R_EVACK, EV_TX);
}

/* --- transmitting: one buffer, reclaimed after each frame ---------------- */

void prism_tx(__data const uint8_t *frame, uint16_t len)
{
  uint8_t i;
  uint16_t n;
  if(bap_setup(0, txfid, 0)) { log_line("card: tx bap"); return; }
  for(i = 0; i < 6; i++) bap_wr(0, 0);    /* status .. retry/rate */
  bap_wr(0, 0x0006);                      /* tx_control: 802.3 frame, TX OK/EX events */
  for(i = 0; i < 15; i++) bap_wr(0, 0);   /* the 802.11 header: the card's job */
  bap_wr(0, len - 14);                    /* data length, little-endian */
  for(n = 0; n + 1 < len; n += 2) bap_wr(0, (uint16_t)frame[n] | ((uint16_t)frame[n + 1] << 8));
  if(n < len) bap_wr(0, frame[n]);
  if(cmd(CMD_TX_RECLAIM, txfid)) { log_line("card: tx cmd"); return; }
  if(wait_event(EV_TX | EV_TXEXC)) log_line("card: tx lost");
}
