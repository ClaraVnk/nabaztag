/*
 * hw.c -- configuration bits, the clock, the UART log, the SPI bus, the LEDs,
 * the serial flash.
 */
#include "board.h"

/* Lazare's configuration, byte for byte (v1-icsp/dumps/violet-v1-1.4b.hex at
 * 0x300000): HS oscillator, power-up timer, brown-out reset on (2.7 V),
 * watchdog on with the 1:128 postscaler (~512 ms), MCLR on, LVP off, no code
 * protection, no write protection. The 1.4b firmware never let the watchdog
 * expire and neither does this one: the main loop clears it at every pass,
 * and no path blocks longer than its period. */
__code __at(0x300000) uint8_t config_bits[14] = {
  0x00, 0x22,   /* CONFIG1L (unimplemented), CONFIG1H: OSCS off, HS */
  0x0A, 0x0F,   /* CONFIG2L: PWRT on, BOR on, BORV 2.7 V; CONFIG2H: WDT on, 1:128 */
  0x83, 0x83,   /* CONFIG3L (unimplemented), CONFIG3H: MCLR on, CCP2 on RC1 */
  0x80, 0x00,   /* CONFIG4L: DEBUG off, LVP off, STVR off; CONFIG4H */
  0x0F, 0xC0,   /* CONFIG5: no code protection */
  0x0F, 0xE0,   /* CONFIG6: no write protection */
  0x0F, 0x40    /* CONFIG7: no table-read protection */
};

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

/* --- UART1: the log, 9600 8N1 on J3 (RC6 TX, RC7 RX) */
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

void log_dec(uint16_t v)
{
  char buf[5];
  uint8_t n = 0;
  do { buf[n++] = (char)('0' + v % 10); v /= 10; } while(v);
  while(n) uart_putc(buf[--n]);
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

void log_nl(void) { uart_putc('\r'); uart_putc('\n'); }
void log_line(const __code char *s) { log_s(s); log_nl(); }

/* --- SPI: MSSP master, Fosc/16 = 1.5 MHz, CKE 1 (mode 0,0) --------------- */
void spi_init(void)
{
  TRISCbits.TRISC5 = 0;         /* SDO */
  TRISCbits.TRISC4 = 1;         /* SDI */
  TRISCbits.TRISC3 = 0;         /* SCK */
  FLASH_CS = 1; FLASH_RST = 1; OKI_CS = 1; OKI_RST = 0; LED_XLAT = 0; LED_MODE = 0;
  TRISBbits.TRISB3 = 0; TRISBbits.TRISB4 = 0; TRISBbits.TRISB5 = 0;
  TRISAbits.TRISA1 = 0; TRISAbits.TRISA2 = 0; TRISAbits.TRISA5 = 0;
  SSPSTAT = 0x40;               /* CKE */
  SSPCON1 = 0x21;               /* SSPEN, CKP 0, Fosc/16 */
}

uint8_t spi_xfer(uint8_t v)
{
  SSPBUF = v;
  while(!SSPSTATbits.BF) ;
  return SSPBUF;
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

/* --- AT45DB081B / AT45DB161: the serial flash --------------------------- */
/* 264-byte pages on 9 address bits (081B, Lazare) or 528 on 10 (161, the
 * 2.0b boards): the density bits of the status register say which. */
static uint8_t flash_shift;     /* 9 or 10 */

static uint8_t flash_status(void)
{
  uint8_t s;
  FLASH_CS = 0;
  spi_xfer(0xD7);
  s = spi_xfer(0xFF);
  FLASH_CS = 1;
  return s;
}

static void flash_wait(void)
{
  while(!(flash_status() & 0x80)) clrwdt();
}

static void flash_addr(uint16_t page, uint16_t byte)
{
  uint32_t a = ((uint32_t)page << flash_shift) | byte;
  spi_xfer((uint8_t)(a >> 16));
  spi_xfer((uint8_t)(a >> 8));
  spi_xfer((uint8_t)a);
}

uint8_t flash_init(void)
{
  uint8_t density;
  TRISBbits.TRISB1 = 1;         /* RDY/BUSY */
  density = (flash_status() >> 2) & 0x0F;
  flash_shift = density == 0x0B ? 10 : 9;
  return (density == 0x09 || density == 0x0B) ? density : 0;
}

void flash_read(uint16_t page, uint16_t offset, __data uint8_t *buf, uint8_t n)
{
  FLASH_CS = 0;
  spi_xfer(0x52);               /* main memory page read */
  flash_addr(page, offset);
  spi_xfer(0); spi_xfer(0); spi_xfer(0); spi_xfer(0);
  while(n--) *buf++ = spi_xfer(0xFF);
  FLASH_CS = 1;
}

void flash_write(uint16_t page, __data const uint8_t *buf, uint8_t n)
{
  uint16_t left = (flash_shift == 10 ? 528 : 264) - n;
  flash_wait();
  FLASH_CS = 0;
  spi_xfer(0x84);               /* buffer 1 write, from byte 0 */
  flash_addr(0, 0);
  while(n--) spi_xfer(*buf++);
  while(left--) spi_xfer(0xFF); /* the rest of the page erased */
  FLASH_CS = 1;
  FLASH_CS = 0;
  spi_xfer(0x83);               /* buffer 1 to main memory, with erase */
  flash_addr(page, 0);
  FLASH_CS = 1;
  flash_wait();
}
