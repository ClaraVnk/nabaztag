/*
 * hw.c -- the application's clock (TMR1), the LEDs (TLC5922), the address
 * loggers. The configuration bits live in the loader (loader.c), the UART
 * and the serial flash in common/.
 */
#include "board.h"

/* --- the tick: TMR1, 16 bits at Fosc/4 = 6 MHz, overflows every 10.92 ms */
static uint16_t tick_count;

void timer_init(void)
{
  T1CON = 0x81;                 /* RD16, prescaler 1:1, internal clock, on */
  TMR1H = 0; TMR1L = 0;
  PIR1bits.TMR1IF = 0;
}

void timer_poll(void)
{
  if(PIR1bits.TMR1IF) { PIR1bits.TMR1IF = 0; tick_count++; }
}

uint16_t ticks(void) { return tick_count; }

void delay_ms(uint16_t ms)
{
  uint16_t until = tick_count + MS_TO_TICKS(ms) + 1;
  while((int16_t)(until - tick_count) > 0) { timer_poll(); clrwdt(); }
}

/* Decimal by repeated subtraction: no division routine linked in (the
 * 16-bit divide and modulo cost 330 bytes of library in phase 1). */
void log_dec(uint16_t v)
{
  static const __code uint16_t pow10[4] = { 10000, 1000, 100, 10 };
  uint8_t i, d, started = 0;
  for(i = 0; i < 4; i++) {
    uint16_t p = pow10[i];
    d = 0;
    while(v >= p) { v -= p; d++; }
    if(d || started) { started = 1; uart_putc((char)('0' + d)); }
  }
  uart_putc((char)('0' + v));
}

void log_ip(__data const uint8_t *ip)
{
  uint8_t i;
  for(i = 0; i < 4; i++) { if(i) uart_putc('.'); log_dec(ip[i]); }
}

void log_mac(__data const uint8_t *mac)
{
  uint8_t i;
  for(i = 0; i < 6; i++) log_hex8(mac[i]);
}

/* --- TLC5922: 16 outputs, no chip select, XLAT latches what was shifted --- */
static void leds_latch(void) { LED_XLAT = 1; nop(); LED_XLAT = 0; }

static void leds_full_brightness(void)
{
  uint8_t i;
  LED_MODE = 1;                 /* dot correction: 16 x 7 bits, all 0x7F */
  for(i = 0; i < 14; i++) spi_xfer(0xFF);
  leds_latch();
  LED_MODE = 0;
}

/* The first bit shifted in lands on OUT15 (TLC5922 datasheet, figure 11),
 * and Newbaztag's LED order puts the top LED's red first: bit 15 = top red. */
void leds_set(uint16_t onoff)
{
  static uint8_t lit;
  if(!lit) { lit = 1; leds_full_brightness(); }
  LED_MODE = 0;
  spi_xfer((uint8_t)(onoff >> 8));
  spi_xfer((uint8_t)onoff);
  leds_latch();
}

/* A string in RAM (the network and server of the configuration page); in
 * the application only: in common/uart.c it overflowed the loader's 8 KB
 * (2026-11-25). */
void log_str(__data const char *s) { while(*s) uart_putc(*s++); }
