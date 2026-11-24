/*
 * uart.c -- UART1, the log: 9600 8N1 on J3 (RC6 TX, RC7 RX). Shared by the
 * loader and the application.
 */
#include "board.h"

void uart_init(void)
{
  TRISCbits.TRISC6 = 0;
  TRISCbits.TRISC7 = 1;
  SPBRG1 = UART_SPBRG;
  TXSTA1 = 0x20;                /* TXEN, BRGH 0, async, 8 bits */
  RCSTA1 = 0x90;                /* SPEN, CREN */
}

void uart_putc(char c)
{
  while(!TXSTA1bits.TRMT) ;
  TXREG1 = c;
}

void log_s(const __code char *s)
{
  while(*s) uart_putc(*s++);
}

static void hex_nibble(uint8_t v)
{
  v &= 0x0F;
  uart_putc(v < 10 ? (char)('0' + v) : (char)('a' + v - 10));
}

void log_hex8(uint8_t v) { hex_nibble(v >> 4); hex_nibble(v); }
void log_hex16(uint16_t v) { log_hex8((uint8_t)(v >> 8)); log_hex8((uint8_t)v); }

void log_nl(void) { uart_putc('\r'); uart_putc('\n'); }
void log_line(const __code char *s) { log_s(s); log_nl(); }
