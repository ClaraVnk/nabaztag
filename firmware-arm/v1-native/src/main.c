/*
 * main.c -- a native firmware for the Nabaztag v1 (2005), phase 1: the
 * skeleton end to end. Boot, log, LEDs, serial flash, the Wi-Fi card joined,
 * DHCP, DNS, the Terrier polled over HTTP, the ears moved once, the watchdog
 * fed. No interrupt anywhere: one loop polls everything, as Violet's
 * firmware did, so the emulator's PIC core (which has none) runs it as is.
 */
#include "board.h"

/* The configuration page of the serial flash (DESIGN.md, memory map): page
 * 0, "NAB1", a format byte, a boot counter. Phase 1 writes nothing else. */
#define CFG_PAGE   0
struct config { uint8_t magic[4]; uint8_t format; uint8_t pad; uint16_t boots; };

static void config_boot(void)
{
  struct config c;
  uint8_t density = flash_init();
  log_s("flash: ");
  if(density == 0x09) log_line("AT45DB081B, 264-byte pages");
  else if(density == 0x0B) log_line("AT45DB161, 528-byte pages");
  else { log_line("not found"); return; }
  flash_read(CFG_PAGE, 0, (__data uint8_t *)&c, sizeof(c));
  if(c.magic[0] != 'N' || c.magic[1] != 'A' || c.magic[2] != 'B' || c.magic[3] != '1' || c.format != 1) {
    c.magic[0] = 'N'; c.magic[1] = 'A'; c.magic[2] = 'B'; c.magic[3] = '1';
    c.format = 1; c.pad = 0; c.boots = 0;
    log_s("config: new page; ");
  }
  c.boots++;
  flash_write(CFG_PAGE, (__data const uint8_t *)&c, sizeof(c));
  flash_read(CFG_PAGE, 0, (__data uint8_t *)&c, sizeof(c));
  log_s("config: boot #"); log_dec(c.boots); log_nl();
}

static uint16_t leds;
static void show(uint16_t on) { if(on != leds) { leds = on; leds_set(on); } }

void main(void)
{
  uint8_t r, ear_demo = 0, last_net = 0xFF;
  uint16_t last_polls = 0;

  ADCON1 = 0x0E;                /* AN0 analog (the volume), the rest digital */
  CMCON = 0x07;                 /* comparators off */
  timer_init();
  uart_init();
  spi_init();
  ears_init();
  log_nl(); log_line("nabv1 native " FW_VERSION);
  show(LED_BOT_R);
  config_boot();

  r = prism_init();
  if(r) { log_s("card: init error "); log_dec(r); log_nl(); }
  else {
    show(LED_BOT_B);
    r = prism_join(SSID);
    if(r) { log_s("card: join error "); log_dec(r); log_nl(); }
    else log_line("card: joining " SSID);
  }
  net_init();

  for(;;) {
    clrwdt();
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
      /* Once the Terrier has answered: each ear two holes, one motor at a
       * time (two at once restarted Lazare, 2026-11-24). */
      if(ear_demo == 0) { ear_demo = 1; ear_move(0, 2); }
    }
    if(ear_demo == 1 && !ears_busy()) { ear_demo = 2; ear_move(1, 2); }
  }
}
