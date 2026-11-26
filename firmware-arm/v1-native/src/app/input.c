/*
 * input.c -- what the rabbit has to tell the Terrier: the head button and
 * the ears turned by hand, in the poll's sd=, as Violet's v1 reported them
 * (the Terrier's nabv1.parse_sd: 0003 a click, 0001 a double click, 0004 a
 * long press, 8R0L the ears' places, a nibble each). One report waits at a
 * time; it goes with the next poll and is dropped once that poll is
 * answered. The volume wheel (AN0) is read too, in three levels as Violet's
 * firmware reads it (thresholds 0x40 and 0xC0, 0x5A4C in 1.4b); what it
 * changes is not known yet, so it is only reported.
 */
#include "board.h"

#define LONG_TICKS      MS_TO_TICKS(1500)       /* a press longer than this is long */
#define DOUBLE_TICKS    MS_TO_TICKS(400)        /* a second click within this is a double */
#define DEBOUNCE_TICKS  MS_TO_TICKS(40)

static uint16_t sd;                     /* the report waiting, 0 none */
static uint8_t  down, clicks, long_sent;
static uint16_t since, up_at;
static uint8_t  volume = 0xFF;

static void report(uint16_t v)
{
  sd = v;
  log_s("input: sd "); log_hex8((uint8_t)(v >> 8)); log_hex8((uint8_t)v); log_nl();
}

void input_init(void)
{
  TRISBbits.TRISB0 = 1;
  ADCON2 = 0x06;                        /* left justified, Fosc/64 */
}

void input_poll(void)
{
  uint8_t now = BUTTON_DOWN;
  uint16_t t = ticks();
  if(now != down && (uint16_t)(t - since) > DEBOUNCE_TICKS) {
    down = now; since = t;
    if(!down) {                         /* released */
      if(long_sent) long_sent = 0;
      else { clicks++; up_at = t; }
    }
  }
  if(down && !long_sent && (uint16_t)(t - since) > LONG_TICKS) {
    long_sent = 1; clicks = 0;
    report(0x0004);
  }
  if(!down && clicks && (uint16_t)(t - up_at) > DOUBLE_TICKS) {
    report(clicks >= 2 ? 0x0001 : 0x0003);
    clicks = 0;
  }
}

/* The ears' places after a turn by hand (ears.c calls it once both have
 * been still for a second): 8R0L, 16 sent as 15 as the Terrier reads it. */
void input_ears(uint8_t right, uint8_t left)
{
  if(right > 15) right = 15;
  if(left > 15) left = 15;
  report(0x8000 | ((uint16_t)right << 8) | left);
}

uint16_t input_sd(void) { return sd; }
/* The poll that carried `v` was answered: that report is done (a newer one
 * that came meanwhile waits for the next poll). */
void input_sent(uint16_t v) { if(sd == v) sd = 0; }

/* The sound button (AN0): a push button, not a switch. It reads 0 at rest
 * and about 0xDA while pressed (Lazare, 2026-11-26: 00 in every position,
 * DA/DC/DE once at each press; Clara: "un bouton poussoir avec 3 états").
 * Each press moves to the next state, normal -> loud -> silent -> normal,
 * kept in the EEPROM (0x79) across starts; oki.c plays at that state's
 * volume as Violet's 0x1FBC does (silent: nothing). Read every 50 ms so a
 * short press is not missed. */
#define EE_VOLUME 0x79

void input_volume_poll(void)
{
  static uint16_t at;
  static uint8_t pressed;
  uint8_t v;
  if((uint16_t)(ticks() - at) < MS_TO_TICKS(50)) return;
  at = ticks();
  ADCON0 = 0x01;                        /* AN0, on */
  { uint8_t k; for(k = 0; k < 60; k++) nop(); }   /* acquisition */
  ADCON0bits.GO = 1;
  while(ADCON0bits.GO) ;
  v = ADRESH;
  if(v >= 0x80 && !pressed) {
    pressed = 1;
    volume = input_volume() == 2 ? 0 : volume + 1;
    ee_put(EE_VOLUME, volume);
    log_s("input: sound "); log_dec(volume); log_s(" (raw "); log_hex8(v); log_line(")");
  } else if(v < 0x40) pressed = 0;
}

uint8_t input_volume(void)
{
  if(volume > 2) { volume = ee_get(EE_VOLUME); if(volume > 2) volume = 1; }
  return volume;
}

/* The sound state, sent with each poll (&vl=0N: 0 silent, 1 normal, 2 loud). */
uint8_t input_volume_raw(void) { return input_volume(); }
