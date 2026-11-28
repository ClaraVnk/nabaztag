/**
 * @file board.h
 * @brief The Nabaztag v1 board around the PIC: who is on which pin.
 *
 * Pin map from Newbaztag doc/components.md (traced on a real board) and from
 * the firmware's own bus routines (0x3E7C-0x3F16 for the Wi-Fi card).
 */
#ifndef _BOARD_H_
#define _BOARD_H_

#include <stdint.h>
#include "pic18.h"

#define FOSC_HZ     24000000u
#define CYCLES_HZ   (FOSC_HZ / 4)

/* port index: A=0 ... G=6 */
enum { PA, PB, PC, PD, PE, PF, PG, NPORTS };

struct board {
  struct pic18 pic;
  uint8_t lat[NPORTS], tris[NPORTS], in[NPORTS];
  int     trace_pins;
  int     trace_bus;
  /* the card's strobes: when a read (0), /WE (2), /IOWR (3) fell, and the
   * read data not yet valid */
  uint64_t strobe_at[4];
  int      read_pending;
  uint8_t  read_value;
  unsigned short_strobes;
  /* timers */
  uint16_t tmr0, tmr1, tmr3;
  uint8_t  tmr2;
  uint32_t t1_frac, t2_frac;
  uint8_t  tmr0h_buf, tmr1h_buf, tmr3h_buf;
  /* uart */
  char     line[256];
  unsigned line_len;
  uint8_t  eeprom[1024];               /* data EEPROM, erased = FF (board_init) */
  int      ee_unlock;                  /* 1 after 55h on EECON2, 2 after AAh */
  int      trace_flash;                /* log every PIC flash erase and write */
  int      trace_oki;                  /* log the OKI's registers with every byte of a burst */
  unsigned flash_erases, flash_writes; /* PIC flash rows erased, blocks written */
  /* adc: what each channel reads (10 bits) */
  uint16_t adc[16];
  int      oki_power;                  /* the OKI's register 31 bit 0: the sound side on */
};

extern struct board B;

void board_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
uint64_t board_ms(void);

/* devices */
void    prism_reset(void);
uint8_t prism_io_read(uint8_t addr);
void    prism_io_write(uint8_t addr, uint8_t value);
uint8_t prism_attr_read(uint8_t addr);
void    prism_attr_write(uint8_t addr, uint8_t value);
void    prism_tick(void);

void    spi_select(void);                 /* chip selects changed */
uint8_t spi_transfer(uint8_t out);
void    spi_flash_chip(int db081);        /* 1: AT45DB081B (264-byte pages) */
void    spi_latch(void);                  /* TLC5922 XLAT rising */
int     flash_load(const char *path);
int     flash_save(void);
int     flash_ready(void);

#endif /* _BOARD_H_ */
