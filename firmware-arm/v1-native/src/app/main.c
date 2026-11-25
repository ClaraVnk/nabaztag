/*
 * main.c -- a native firmware for the Nabaztag v1 (2005): the application,
 * linked from APP_BASE behind the loader. Boot, log, LEDs, serial flash,
 * the Wi-Fi card joined, DHCP, DNS, the Terrier polled over HTTP, the ears
 * moved once, an offered image downloaded (update.c), the watchdog fed. No
 * interrupt anywhere: one loop polls everything, as Violet's firmware did,
 * so the emulator's PIC core (which has none) runs it as is.
 */
#include "board.h"

/* The header the loader and mkimage.py read: who this image is for and how
 * long it is (mkimage.py fills the length after the link). */
__code __at(APP_BASE + APP_HDR_OFF) struct app_hdr app_hdr = {
  { 'N', 'B', 'V', '1' }, 1, BOARD_ID, APP_VERSION, 0, 0, { 0, 0, 0 }
};

/* The configuration page of the serial flash (DESIGN.md, memory map): page
 * 0, "NAB1", a format byte, a boot counter. Nothing else is written yet. */
/* The configuration page (format 2, 2026-11-25): the boot count, and the
 * network and server frame 15 set from the Terrier, taken at the next boot;
 * empty, the build's SSID and SERVER_NAME. */
struct config { uint8_t magic[4]; uint8_t format; uint8_t pad; uint16_t boots; char ssid[33]; char server[48]; };
__data char net_ssid[33], net_server[48];

static void copy_code(__data char *to, const __code char *from, uint8_t room)
{
  while(*from && --room) *to++ = *from++;
  *to = 0;
}

static void copy_data(__data char *to, __data const char *from, uint8_t room)
{
  while(*from && --room) *to++ = *from++;
  *to = 0;
}

static void config_boot(void)
{
  struct config c;
  uint8_t density = flash_init();
  log_s("flash: ");
  if(density == 0x09) log_line("AT45DB081B");
  else if(density == 0x0B) log_line("AT45DB161");
  else { log_line("none"); return; }
  flash_read(PG_CONFIG, 0, (__data uint8_t *)&c, sizeof(c));
  if(c.magic[0] != 'N' || c.magic[1] != 'A' || c.magic[2] != 'B' || c.magic[3] != '1' || (c.format != 1 && c.format != 2)) {
    c.magic[0] = 'N'; c.magic[1] = 'A'; c.magic[2] = 'B'; c.magic[3] = '1';
    c.boots = 0;
    log_s("config: new page; ");
  }
  if(c.format != 2) { c.ssid[0] = 0; c.server[0] = 0; }   /* format 1 had neither */
  c.format = 2; c.pad = 0;
  c.ssid[32] = 0; c.server[47] = 0;
  if(c.ssid[0]) copy_data(net_ssid, c.ssid, 33); else copy_code(net_ssid, SSID, 33);
  if(c.server[0]) copy_data(net_server, c.server, 48); else copy_code(net_server, SERVER_NAME, 48);
  c.boots++;
  flash_write(PG_CONFIG, (__data const uint8_t *)&c, sizeof(c));
  flash_read(PG_CONFIG, 0, (__data uint8_t *)&c, sizeof(c));
  log_s("config: boot #"); log_dec(c.boots); log_nl();
  log_s("config: network "); log_str(net_ssid); log_s(", server "); log_str(net_server); log_nl();
  update_init();
}

/* The sound directory's autoplay: sound 0 at boot (tools/v1-audio-proof.sh). */
static void sound_boot(void)
{
  struct snd_dir d;
  flash_read(PG_SOUNDS, 0, (__data uint8_t *)&d, 6 + sizeof(d.s[0]));
  if(d.magic[0] != 'S' || d.magic[1] != 'N' || d.magic[2] != 'D' || d.magic[3] != '1' || !d.count || !d.autoplay) return;
  oki_play(d.s[0].page, d.s[0].length);
}

static uint16_t leds, net_leds, order_until;
static uint8_t  ordered;
static void show(uint16_t on)
{
  net_leds = on;                        /* the network's LEDs, back after an order */
  if(ordered) return;
  if(on != leds) { leds = on; leds_set(on); }
}

/* Frame 15: settings from the Terrier, type-length-value (1 the server, 2
 * the network), written to the configuration page; they take effect at the
 * next boot (DESIGN.md section 8). */
static struct { uint8_t type, len, n; char v[48]; uint8_t got_ssid, got_server; char ssid[33], server[48]; } f15;

void config_frame_byte(uint8_t c, uint8_t last)
{
  if(f15.type == 0) { f15.type = c; f15.len = 0xFF; }
  else if(f15.len == 0xFF) { f15.len = c; f15.n = 0; if(!c) f15.type = 0; }
  else {
    if(f15.n < sizeof(f15.v) - 1) f15.v[f15.n] = (char)c;
    if(++f15.n == f15.len) {
      f15.v[f15.n < sizeof(f15.v) ? f15.n : sizeof(f15.v) - 1] = 0;
      if(f15.type == 1) { copy_data(f15.server, f15.v, 48); f15.got_server = 1; }
      if(f15.type == 2) { copy_data(f15.ssid, f15.v, 33); f15.got_ssid = 1; }
      f15.type = 0;
    }
  }
  if(last) {
    struct config c;
    flash_read(PG_CONFIG, 0, (__data uint8_t *)&c, sizeof(c));
    if(f15.got_server) copy_data(c.server, f15.server, 48);
    if(f15.got_ssid) copy_data(c.ssid, f15.ssid, 33);
    c.format = 2;
    flash_write(PG_CONFIG, (__data const uint8_t *)&c, sizeof(c));
    log_s("config: set, network "); log_str(c.ssid); log_s(", server "); log_str(c.server); log_line(" (next boot)");
    f15.type = 0; f15.got_server = f15.got_ssid = 0;
  }
}

/* Frame 12: five LEDs, top first, each byte bit 0 R, 1 G, 2 B, for
 * `seconds` (0: ten), then the network's LEDs again. LED i's bits in
 * leds_set(): R 15-3i, B 14-3i, G 13-3i. */
void leds_order(__data const uint8_t *five, uint8_t seconds)
{
  uint16_t on = 0;
  uint8_t i;
  for(i = 0; i < 5; i++) {
    uint8_t sh = 15 - 3 * i;
    if(five[i] & 1) on |= (uint16_t)1 << sh;
    if(five[i] & 4) on |= (uint16_t)1 << (sh - 1);
    if(five[i] & 2) on |= (uint16_t)1 << (sh - 2);
  }
  ordered = 1;
  order_until = ticks() + (uint16_t)(seconds ? seconds : 10) * TICKS_PER_S;
  leds = on; leds_set(on);
  log_s("leds: ordered "); log_hex8((uint8_t)(on >> 8)); log_hex8((uint8_t)on); log_nl();
}

/* EEPROM 0x7C: the boots since the image was written (2 bytes), RCON at this
 * start, and RCON's TO bit seen (a watchdog reset). */
static void boot_mark(void)
{
  uint16_t n;
  uint8_t rc = RCON;
  EEADRH = 0; EEADR = 0x7C; EECON1 = 0x01; n = EEDATA;
  EEADR = 0x7D; EECON1 = 0x01; n |= (uint16_t)EEDATA << 8;
  n = n == 0xFFFF ? 1 : n + 1;
  ee_put(0x7C, (uint8_t)n); ee_put(0x7D, (uint8_t)(n >> 8));
  ee_put(0x7E, rc);
  RCON |= 0x1F;                           /* TO, PD, POR, BOR set again: the next start shows its own */
}

void main(void)
{
  uint8_t r, ear_demo = 0, last_net = 0xFF, join_r = 0xFF, reported = 0;
  uint16_t t0, last_report, vol_at = 0;
  uint16_t last_polls = 0;

  boot_mark();
  ADCON1 = 0x0E;                /* AN0 analog (the volume), the rest digital */
  CMCON = 0x07;                 /* comparators off */
  timer_init();
  uart_init();
  spi_init();
  ears_init();
  input_init();
  log_nl(); log_line("nabv1 native " FW_VERSION);
#ifdef APP_BROKEN
  for(;;) ;                     /* a test image that never feeds the watchdog (the loader's fallback proof) */
#endif
  show(LED_BOT_R);
  config_boot();
  oki_init();
  sound_boot();

  r = prism_init();
  if(r) { join_r = 0x80 | r; log_s("card: init error "); log_dec(r); log_nl(); }
  else {
    show(LED_BOT_B);
    r = prism_join(net_ssid);
    join_r = r;
    if(r) { log_s("card: join error "); log_dec(r); log_nl(); }
    else { log_s("card: joining "); log_str(net_ssid); log_nl(); }
  }
  net_init();
  t0 = last_report = ticks();

  for(;;) {
    clrwdt();
    /* 30 s without an association: what the card says goes to the EEPROM
     * (prism_report), for a rabbit with no readable serial log. */
    if(!(reported & 1) && !prism_link() && (uint16_t)(ticks() - t0) > 30 * TICKS_PER_S) {
      reported |= 1;
      prism_report(join_r);
    }
    /* Every step of the network into the EEPROM (net_report) at 40 s, then
     * every 40 s: Lazare's first try of it left nothing, the firmware never
     * ran 2 minutes in one go (2026-11-25). The boot count and the reset's
     * cause (RCON) are written once at the start, beside it. */
    if((uint16_t)(ticks() - last_report) > 40 * TICKS_PER_S) {
      last_report = ticks();
      net_report();
    }
    timer_poll();
    prism_poll();
    net_poll();
    ears_poll();
    input_poll();
    if((uint16_t)(ticks() - vol_at) > 2 * TICKS_PER_S) { vol_at = ticks(); input_volume(); }
    if(ordered && (int16_t)(ticks() - order_until) >= 0) {
      ordered = 0;
      leds = ~net_leds;                 /* forces the write */
      show(net_leds);
    }

    r = net_state();
    if(r != last_net) {
      last_net = r;
      show(r == NET_DOWN ? (prism_link() ? LED_BOT_G : LED_BOT_B)
         : r == NET_DHCP ? LED_BOT_G
         : r == NET_DNS  ? (LED_BOT_G | LED_MID_G)
         :                 (LED_BOT_G | LED_MID_G | LED_ML_G));
    }
    if(polls_answered != last_polls) {
      last_polls = polls_answered;
      show(net_leds | LED_TOP_G);
      update_confirm();
      /* Once the Terrier has answered: each ear two holes, one motor at a
       * time (two at once restarted Lazare, 2026-11-24). */
#ifndef NO_EAR_DEMO
      if(ear_demo == 0) { ear_demo = 1; ear_move(0, 2); }
#else
      /* NO_EAR_DEMO: a rabbit whose motor restarts it (Lazare's right ear,
       * a hardware fault, 2026-11-24) is never sent turning on its own. */
      ear_demo = 2;
#endif
    }
    if(ear_demo == 1 && !ears_busy()) { ear_demo = 2; ear_move(1, 2); }
  }
}
