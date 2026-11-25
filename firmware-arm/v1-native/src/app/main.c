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
struct config { uint8_t magic[4]; uint8_t format; uint8_t pad; uint16_t boots; };

static void config_boot(void)
{
  struct config c;
  uint8_t density = flash_init();
  log_s("flash: ");
  if(density == 0x09) log_line("AT45DB081B");
  else if(density == 0x0B) log_line("AT45DB161");
  else { log_line("none"); return; }
  flash_read(PG_CONFIG, 0, (__data uint8_t *)&c, sizeof(c));
  if(c.magic[0] != 'N' || c.magic[1] != 'A' || c.magic[2] != 'B' || c.magic[3] != '1' || c.format != 1) {
    c.magic[0] = 'N'; c.magic[1] = 'A'; c.magic[2] = 'B'; c.magic[3] = '1';
    c.format = 1; c.pad = 0; c.boots = 0;
    log_s("config: new page; ");
  }
  c.boots++;
  flash_write(PG_CONFIG, (__data const uint8_t *)&c, sizeof(c));
  flash_read(PG_CONFIG, 0, (__data uint8_t *)&c, sizeof(c));
  log_s("config: boot #"); log_dec(c.boots); log_nl();
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

static uint16_t leds;
static void show(uint16_t on) { if(on != leds) { leds = on; leds_set(on); } }

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
  uint16_t t0, last_report;
  uint16_t last_polls = 0;

  boot_mark();
  ADCON1 = 0x0E;                /* AN0 analog (the volume), the rest digital */
  CMCON = 0x07;                 /* comparators off */
  timer_init();
  uart_init();
  spi_init();
  ears_init();
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
    r = prism_join(SSID);
    join_r = r;
    if(r) { log_s("card: join error "); log_dec(r); log_nl(); }
    else log_line("card: joining " SSID);
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
      show(leds | LED_TOP_G);
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
