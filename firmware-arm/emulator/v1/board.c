/**
 * @file board.c
 * @brief Ports, timers, UART, ADC and the SPI bus of the Nabaztag v1.
 *
 * Pins (Newbaztag doc/components.md, checked against what the firmware does):
 *   RB0 head button       RB1 flash RDY/BUSY     RB2 OKI IRQ
 *   RB3/RB4 flash /CS (the two Newbaztag pages disagree; both are watched)
 *   RA2 OKI /CS   RA1 OKI /RST   RB5 TLC5922 XLAT   RA5 TLC5922 MODE
 *   RC3 SCK  RC4 SDI  RC5 SDO     RC6/RC7 UART1 (9600, the firmware's log)
 *   RA4/T0CKI ear 1 sensor (TMR0 counts it), RC0/T13CKI ear 2 (TMR3)
 *   RF0/RD1 and RF2/RF3 ear motors (L293D)
 *   PORTE address, PORTD data, RF7 /IORD, RG4 /IOWR, RF5 /OE, RF6 /WE: the
 *   PCMCIA Wi-Fi card, an 8-bit bus the firmware drives by hand (0x3E7C).
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"

struct board B;

uint64_t board_ms(void) { return B.pic.cycles / (CYCLES_HZ / 1000); }

void board_log(const char *fmt, ...)
{
  va_list ap;
  printf("[%8llu ms] ", (unsigned long long)board_ms());
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  putchar('\n');
  fflush(stdout);
}

/* --- ports --------------------------------------------------------------- */

static int port_of(uint16_t a, int *kind)
{
  if(a >= SFR_PORTA && a <= SFR_PORTG) { *kind = 0; return a - SFR_PORTA; }
  if(a >= SFR_LATA && a <= SFR_LATG)   { *kind = 1; return a - SFR_LATA; }
  if(a >= SFR_TRISA && a <= SFR_TRISG) { *kind = 2; return a - SFR_TRISA; }
  return -1;
}

static uint8_t pins(int p)
{
  return (uint8_t)((B.lat[p] & ~B.tris[p]) | (B.in[p] & B.tris[p]));
}

#define PIN(p, b)  ((pins(p) >> (b)) & 1)

static void pins_changed(int p, uint8_t before, uint8_t after)
{
  uint8_t fell = before & ~after, rose = ~before & after;

  if(B.trace_pins && (fell | rose))
    board_log("pins %c: %02X -> %02X  (pc %05X)", 'A' + p, before, after, B.pic.pc);

  if(p == PF && ((fell | rose) & 0x0F))
    board_log("motor pins RF3..0: %X -> %X (pc %05X)", before & 0x0F, after & 0x0F, B.pic.pc);
  if(p == PF) {
    if(fell & 0x80) B.in[PD] = prism_io_read(B.lat[PE]);       /* /IORD */
    if(fell & 0x20) B.in[PD] = prism_attr_read(B.lat[PE]);     /* /OE   */
    if(rose & 0x40) prism_attr_write(B.lat[PE], B.lat[PD]);    /* /WE   */
  }
  if(p == PG && (rose & 0x10)) prism_io_write(B.lat[PE], B.lat[PD]);  /* /IOWR */
  if(p == PB && (rose & 0x20)) spi_latch();                    /* XLAT */
  if((p == PB && ((fell | rose) & 0x18)) || (p == PA && ((fell | rose) & 0x04)))
    spi_select();
}

/* --- timers -------------------------------------------------------------- */

static void timers(unsigned cyc)
{
  uint8_t *ram = B.pic.ram;
  uint8_t t1con = ram[SFR_T1CON], t2con = ram[SFR_T2CON];

  if((t1con & 0x01) && !(t1con & 0x02)) {             /* TMR1 on, Fosc/4 */
    unsigned pre = 1u << ((t1con >> 4) & 3);
    B.t1_frac += cyc;
    while(B.t1_frac >= pre) {
      B.t1_frac -= pre;
      if(++B.tmr1 == 0) ram[SFR_PIR1] |= 0x01;
    }
  }
  if(t2con & 0x04) {                                  /* TMR2 on */
    static const unsigned pres[4] = { 1, 4, 16, 16 };
    unsigned pre = pres[t2con & 3];
    B.t2_frac += cyc;
    while(B.t2_frac >= pre) {
      B.t2_frac -= pre;
      if(B.tmr2 == ram[SFR_PR2]) { B.tmr2 = 0; ram[SFR_PIR1] |= 0x02; }
      else B.tmr2++;
    }
  }
}

/* The ear sensors are the timers' external clocks; the motor model calls
 * this for each slot that passes the photo-sensor. */
void board_ear_pulse(int ear)
{
  if(ear == 0 && (B.pic.ram[SFR_T0CON] & 0xA0) == 0xA0) B.tmr0++;
  if(ear == 1 && (B.pic.ram[SFR_T3CON] & 0x03) == 0x03) B.tmr3++;
}

/* --- SFR hooks ----------------------------------------------------------- */

static uint8_t sfr_read(struct pic18 *pic, uint16_t a)
{
  int kind, p = port_of(a, &kind);

  if(p >= 0 && p < NPORTS) {
    if(kind == 0) return pins(p);
    if(kind == 1) return B.lat[p];
    return B.tris[p];
  }
  switch(a) {
    case SFR_TMR0L: B.tmr0h_buf = (uint8_t)(B.tmr0 >> 8); return (uint8_t)B.tmr0;
    case SFR_TMR0H: return B.tmr0h_buf;
    case SFR_TMR1L:
      if(pic->ram[SFR_T1CON] & 0x80) B.tmr1h_buf = (uint8_t)(B.tmr1 >> 8);
      return (uint8_t)B.tmr1;
    case SFR_TMR1H:
      return (pic->ram[SFR_T1CON] & 0x80) ? B.tmr1h_buf : (uint8_t)(B.tmr1 >> 8);
    case SFR_TMR3L:
      if(pic->ram[SFR_T3CON] & 0x80) B.tmr3h_buf = (uint8_t)(B.tmr3 >> 8);
      return (uint8_t)B.tmr3;
    case SFR_TMR3H:
      return (pic->ram[SFR_T3CON] & 0x80) ? B.tmr3h_buf : (uint8_t)(B.tmr3 >> 8);
    case SFR_TMR2: return B.tmr2;
    case SFR_PIR1: return (uint8_t)(pic->ram[a] | 0x10);         /* TXIF: always room */
    case SFR_TXSTA1: return (uint8_t)(pic->ram[a] | 0x02);       /* TRMT */
    case SFR_SSPBUF: pic->ram[SFR_SSPSTAT] &= (uint8_t)~1; return pic->ram[a];
  }
  return pic->ram[a];
}

static void uart_tx(uint8_t c)
{
  if(c == '\r') return;
  if(c == '\n' || B.line_len >= sizeof(B.line) - 1) {
    B.line[B.line_len] = 0;
    board_log("uart: %s", B.line);
    B.line_len = 0;
    if(c == '\n') return;
  }
  B.line[B.line_len++] = (char)((c >= 0x20 && c < 0x7F) ? c : '.');
}

static void sfr_write(struct pic18 *pic, uint16_t a, uint8_t v)
{
  int kind, p = port_of(a, &kind);

  if(p >= 0 && p < NPORTS) {
    uint8_t before = pins(p);
    if(kind == 2) B.tris[p] = v; else B.lat[p] = v;
    pic->ram[SFR_PORTA + p] = pins(p);
    pins_changed(p, before, pins(p));
    return;
  }
  switch(a) {
    case SFR_TMR0L: B.tmr0 = (uint16_t)((B.tmr0h_buf << 8) | v); return;
    case SFR_TMR0H: B.tmr0h_buf = v; return;
    case SFR_TMR1L: B.tmr1 = (uint16_t)((B.tmr1 & 0xFF00) | v); return;
    case SFR_TMR1H: B.tmr1 = (uint16_t)((B.tmr1 & 0x00FF) | (v << 8)); return;
    case SFR_TMR3L: B.tmr3 = (uint16_t)((B.tmr3 & 0xFF00) | v); return;
    case SFR_TMR3H: B.tmr3 = (uint16_t)((B.tmr3 & 0x00FF) | (v << 8)); return;
    case SFR_TMR2: B.tmr2 = v; return;
    case SFR_TXREG1: uart_tx(v); return;
    /* The data EEPROM (1 KB, DS39612 section 7), for the Wi-Fi card probe
     * (v1-icsp/wifi-probe.py, 2026-11-24): a write takes WREN, then 55h and
     * AAh on EECON2 right before WR, as the datasheet demands; it lands at
     * once (the real one takes ~4 ms) and is logged. Neither Violet
     * firmware touches it. */
    case SFR_EECON2:
      B.ee_unlock = (v == 0x55) ? 1 : (B.ee_unlock == 1 && v == 0xAA) ? 2 : 0;
      return;
    case SFR_EECON1:
      if(v & 0x02) {                        /* WR: start a write */
        if(!((v & 0x04) && B.ee_unlock == 2)) {
          board_log("nvm: write refused (WREN %d, unlock %d)", !!(v & 0x04), B.ee_unlock);
        } else if(v & 0x80) {               /* EEPGD: program (flash) memory */
          uint32_t row = pic->flash_row & ~0x3Fu;
          if(row + 64 > PIC_FLASH_SIZE) {
            board_log("flash: write past end at %06X", row);
          } else if(v & 0x10) {             /* FREE: erase the 64-byte row */
            for(int i = 0; i < 64; i++) pic->flash[row + i] = 0xFF;
            board_log("flash: erase row %05X", row);
          } else {                          /* write the 64 holding latches */
            for(int i = 0; i < 64; i++) pic->flash[row + i] = pic->flash_latch[i];
            board_log("flash: write row %05X (latch[0]=%02X)", row, pic->flash_latch[0]);
          }
        } else if(!(v & 0x40)) {            /* data EEPROM (EEPGD=0, CFGS=0) */
          unsigned at = ((pic->ram[SFR_EEADRH] & 3u) << 8) | pic->ram[SFR_EEADR];
          B.eeprom[at] = pic->ram[SFR_EEDATA];
          board_log("eeprom: [%03X] <- %02X", at, B.eeprom[at]);
        }
        pic->ram[SFR_EECON1] = v & (uint8_t)~0x02;
      }
      B.ee_unlock = 0;
      return;
    case SFR_SSPBUF:
      pic->ram[SFR_SSPBUF] = spi_transfer(v);
      pic->ram[SFR_SSPSTAT] |= 1;          /* BF */
      pic->ram[SFR_PIR1] |= 0x08;          /* SSPIF */
      pic->ram[SFR_SSPCON1] &= (uint8_t)~0x80;
      return;
    case SFR_ADCON0:
      if(v & 0x02) {                        /* GO: done at once */
        uint16_t r = B.adc[(v >> 2) & 0x0F];
        if(pic->ram[SFR_ADCON2] & 0x80) { pic->ram[SFR_ADRESH] = (uint8_t)(r >> 8); pic->ram[SFR_ADRESL] = (uint8_t)r; }
        else { pic->ram[SFR_ADRESH] = (uint8_t)(r >> 2); pic->ram[SFR_ADRESL] = (uint8_t)(r << 6); }
        pic->ram[SFR_ADCON0] = v & (uint8_t)~0x02;
        pic->ram[SFR_PIR1] |= 0x40;
      }
      return;
  }
}

static const struct pic18_board board_ops = { sfr_read, sfr_write };

void board_init(void)
{
  memset(&B, 0, sizeof(B));
  memset(B.eeprom, 0xFF, sizeof(B.eeprom));
  B.pic.board = &board_ops;
  for(int i = 0; i < 16; i++) B.adc[i] = 512;
  /* Inputs at rest: button released (pulled up), flash ready, OKI IRQ idle,
   * ear sensors high between slots. */
  B.in[PA] = 0xFF;
  B.in[PB] = 0xFF;
  B.in[PC] = 0xFF;
  B.in[PD] = 0xFF;
  B.in[PE] = 0xFF;
  B.in[PF] = 0xFF;
  B.in[PG] = 0xFF;
}

void board_reset(void)
{
  pic18_reset(&B.pic);
  for(int p = 0; p < NPORTS; p++) { B.tris[p] = 0xFF; B.lat[p] = 0; }
  prism_reset();
}

void board_step(void)
{
  unsigned cyc = pic18_step(&B.pic);
  timers(cyc);
}
