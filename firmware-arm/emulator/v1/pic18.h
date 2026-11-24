/**
 * @file pic18.h
 * @brief A PIC18 core, enough of one to run the Nabaztag v1's own firmware.
 *
 * The v1 (2005) is a PIC18F6627 with no JTAG; its firmware was dumped over
 * ICSP and published by Newbaztag (firmware/original/nabaztag.hex). It never
 * enables an interrupt (INTCON GIE/PEIE cleared at 0x68F0, no vector at 0x08)
 * so this core has none: the board polls, and so does the emulator.
 *
 * Standard instruction set only: the dump's CONFIG4L is 0x80, XINST off.
 */
#ifndef _PIC18_H_
#define _PIC18_H_

#include <stdint.h>

#define PIC_FLASH_SIZE  0x18000u     /* 96 KB, PIC18F6627 */
#define PIC_RAM_SIZE    0x1000u
#define PIC_SFR_BASE    0xF60u       /* access bank: 0x00-0x5F and 0xF60-0xFFF */
#define PIC_ACCESS_LOW  0x60u
#define PIC_STACK_DEPTH 31

/* SFR addresses (DS39646C table 5-2) */
enum {
  SFR_PORTA = 0xF80, SFR_PORTB, SFR_PORTC, SFR_PORTD, SFR_PORTE, SFR_PORTF, SFR_PORTG,
  SFR_LATA = 0xF89, SFR_LATB, SFR_LATC, SFR_LATD, SFR_LATE, SFR_LATF, SFR_LATG,
  SFR_TRISA = 0xF92, SFR_TRISB, SFR_TRISC, SFR_TRISD, SFR_TRISE, SFR_TRISF, SFR_TRISG,
  SFR_PIE1 = 0xF9D, SFR_PIR1 = 0xF9E,
  SFR_EECON1 = 0xFA6, SFR_EECON2 = 0xFA7, SFR_EEDATA = 0xFA8, SFR_EEADR = 0xFA9, SFR_EEADRH = 0xFAA,
  SFR_RCSTA1 = 0xFAB, SFR_TXSTA1 = 0xFAC, SFR_TXREG1 = 0xFAD, SFR_RCREG1 = 0xFAE,
  SFR_T3CON = 0xFB1, SFR_TMR3L = 0xFB2, SFR_TMR3H = 0xFB3,
  SFR_ADCON2 = 0xFC0, SFR_ADCON1, SFR_ADCON0, SFR_ADRESL, SFR_ADRESH,
  SFR_SSPCON1 = 0xFC6, SFR_SSPSTAT = 0xFC7, SFR_SSPBUF = 0xFC9,
  SFR_T2CON = 0xFCA, SFR_PR2, SFR_TMR2,
  SFR_T1CON = 0xFCD, SFR_TMR1L, SFR_TMR1H,
  SFR_T0CON = 0xFD5, SFR_TMR0L, SFR_TMR0H, SFR_STATUS,
  SFR_FSR2L = 0xFD9, SFR_FSR2H, SFR_PLUSW2, SFR_PREINC2, SFR_POSTDEC2, SFR_POSTINC2, SFR_INDF2,
  SFR_BSR = 0xFE0, SFR_FSR1L, SFR_FSR1H, SFR_PLUSW1, SFR_PREINC1, SFR_POSTDEC1, SFR_POSTINC1, SFR_INDF1,
  SFR_WREG = 0xFE8, SFR_FSR0L, SFR_FSR0H, SFR_PLUSW0, SFR_PREINC0, SFR_POSTDEC0, SFR_POSTINC0, SFR_INDF0,
  SFR_INTCON3 = 0xFF0, SFR_INTCON2, SFR_INTCON, SFR_PRODL, SFR_PRODH, SFR_TABLAT,
  SFR_TBLPTRL = 0xFF6, SFR_TBLPTRH, SFR_TBLPTRU, SFR_PCL, SFR_PCLATH, SFR_PCLATU,
  SFR_STKPTR = 0xFFC, SFR_TOSL, SFR_TOSH, SFR_TOSU,
};

/* STATUS bits */
#define ST_C   0x01
#define ST_DC  0x02
#define ST_Z   0x04
#define ST_OV  0x08
#define ST_N   0x10

struct pic18;

/* The board sees every access to a peripheral register (0xF60-0xFFF except
 * the core's own: WREG, STATUS, BSR, FSRs, INDFs, PROD, TBLPTR, PCL, stack).
 * read returns the value; write gets the value already stored in ram[]. */
struct pic18_board {
  uint8_t (*sfr_read)(struct pic18 *pic, uint16_t addr);
  void    (*sfr_write)(struct pic18 *pic, uint16_t addr, uint8_t value);
};

struct pic18 {
  uint8_t  flash[PIC_FLASH_SIZE];
  uint8_t  ram[PIC_RAM_SIZE];
  uint8_t  config[16];                /* 0x300000-0x30000F */
  uint32_t pc;
  uint32_t stack[PIC_STACK_DEPTH + 1];
  uint8_t  sp;                       /* 0 = empty, as STKPTR */
  uint8_t  shadow_w, shadow_status, shadow_bsr;
  uint64_t cycles;                   /* instruction cycles, Fosc/4 */
  uint64_t wdt_clear;                /* cycles at the last CLRWDT or reset */
  int      halted;                   /* SLEEP, RESET or an unknown opcode */
  const char *halt_reason;
  const struct pic18_board *board;
  void    *ctx;
  /* Flash self-write (DS39646C ch. 6, DS39612C ch. 6): 64 holding latches
   * mapped to TBLPTR[5:0], written to the 64-byte row by EECON1 (board.c).
   * Violet's firmware has no TBLWT, so this is inert for it; it exists to
   * prove end to end that a loader WOULD need these (v1-icsp/VM-ESCAPE.md). */
  uint8_t  flash_latch[64];
  uint32_t flash_row;                /* 64-byte-aligned target of the latches */
  /* vm-audit (VM-ESCAPE.md): the destination of every indirect byte store
   * (MOVFF through an FSR) made from a PC inside the VM-interpreter band, to
   * measure that the downloaded program's writes stay in its arena. */
  int      audit_on;
  uint16_t audit_lo, audit_hi;       /* the VM-interpreter PC band */
  uint16_t audit_min, audit_max;     /* destination addresses seen */
  unsigned audit_stores;             /* how many */
  unsigned audit_escapes;            /* how many outside 0xA70-0xCFF */
};

void     pic18_reset(struct pic18 *pic);
int      pic18_load_hex(struct pic18 *pic, const char *path);
/* One instruction. Returns the cycles it took. */
unsigned pic18_step(struct pic18 *pic);
uint8_t  pic18_peek(struct pic18 *pic, uint16_t addr);      /* no side effects */

#endif /* _PIC18_H_ */
