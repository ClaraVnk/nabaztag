/**
 * @file pic18.c
 * @brief The PIC18 standard instruction set (DS39646C chapter 26).
 *
 * Every core register lives in ram[] at its SFR address, so a MOVFF to WREG
 * or STATUS is just a store. What the board owns (ports, timers, SPI, UART)
 * goes through pic18_board.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pic18.h"

#define W      (pic->ram[SFR_WREG])
#define STATUS (pic->ram[SFR_STATUS])
#define BSR    (pic->ram[SFR_BSR])

static uint16_t fetch(struct pic18 *pic, uint32_t at)
{
  if(at + 1 >= PIC_FLASH_SIZE) return 0xFFFF;
  return (uint16_t)(pic->flash[at] | (pic->flash[at + 1] << 8));
}

/* Registers the core implements itself; everything else in the SFR area
 * belongs to the board. */
static int core_sfr(uint16_t a)
{
  return a >= SFR_STATUS;
}

static int is_indirect(uint16_t a)
{
  return (a >= 0xFDB && a <= 0xFDF) || (a >= 0xFE3 && a <= 0xFE7) ||
         (a >= 0xFEB && a <= 0xFEF);
}

static uint16_t fsr_get(struct pic18 *pic, int n)
{
  static const uint16_t lo[3] = { SFR_FSR0L, SFR_FSR1L, SFR_FSR2L };
  return (uint16_t)((pic->ram[lo[n]] | (pic->ram[lo[n] + 1] << 8)) & 0x0FFF);
}

static void fsr_set(struct pic18 *pic, int n, uint16_t v)
{
  static const uint16_t lo[3] = { SFR_FSR0L, SFR_FSR1L, SFR_FSR2L };
  v &= 0x0FFF;
  pic->ram[lo[n]] = (uint8_t)v;
  pic->ram[lo[n] + 1] = (uint8_t)(v >> 8);
}

/* The address an access really touches, applying POSTINC & co. once. An
 * indirect register addressed through an FSR reads 0 and ignores writes:
 * that is returned as 0xFFFF. */
static uint16_t effective(struct pic18 *pic, uint16_t a)
{
  int n, op;
  uint16_t f, r;

  if(!is_indirect(a)) return a;
  if(a >= 0xFEB)      { n = 0; op = a - 0xFEB; }
  else if(a >= 0xFE3) { n = 1; op = a - 0xFE3; }
  else                { n = 2; op = a - 0xFDB; }
  /* op: 0 PLUSW, 1 PREINC, 2 POSTDEC, 3 POSTINC, 4 INDF */
  f = fsr_get(pic, n);
  switch(op) {
    case 0: r = (uint16_t)((f + (int8_t)W) & 0x0FFF); break;
    case 1: f++; fsr_set(pic, n, f); r = f & 0x0FFF; break;
    case 2: r = f; fsr_set(pic, n, (uint16_t)(f - 1)); break;
    case 3: r = f; fsr_set(pic, n, (uint16_t)(f + 1)); break;
    default: r = f; break;
  }
  return is_indirect(r) ? 0xFFFF : r;
}

uint8_t pic18_peek(struct pic18 *pic, uint16_t a)
{
  return pic->ram[a & 0x0FFF];
}

static uint8_t rd(struct pic18 *pic, uint16_t a)
{
  if(a == 0xFFFF) return 0;
  if(a < PIC_SFR_BASE) return pic->ram[a];
  switch(a) {
    case SFR_PCL:
      pic->ram[SFR_PCLATH] = (uint8_t)(pic->pc >> 8);
      pic->ram[SFR_PCLATU] = (uint8_t)(pic->pc >> 16);
      return (uint8_t)pic->pc;
    case SFR_TOSL: return (uint8_t)pic->stack[pic->sp];
    case SFR_TOSH: return (uint8_t)(pic->stack[pic->sp] >> 8);
    case SFR_TOSU: return (uint8_t)(pic->stack[pic->sp] >> 16);
    case SFR_STKPTR: return pic->ram[SFR_STKPTR] = (uint8_t)((pic->ram[SFR_STKPTR] & 0xC0) | pic->sp);
  }
  if(core_sfr(a) || !pic->board || !pic->board->sfr_read) return pic->ram[a];
  return pic->ram[a] = pic->board->sfr_read(pic, a);
}

static void wr(struct pic18 *pic, uint16_t a, uint8_t v)
{
  if(a == 0xFFFF) return;
  if(a < PIC_SFR_BASE) { pic->ram[a] = v; return; }
  switch(a) {
    case SFR_PCL:
      pic->ram[SFR_PCL] = v;
      pic->pc = (((uint32_t)pic->ram[SFR_PCLATU] << 16) |
                 ((uint32_t)pic->ram[SFR_PCLATH] << 8) | v) & ~1u;
      pic->cycles++;
      return;
    case SFR_TOSL: pic->stack[pic->sp] = (pic->stack[pic->sp] & ~0xFFu) | v; return;
    case SFR_TOSH: pic->stack[pic->sp] = (pic->stack[pic->sp] & ~0xFF00u) | ((uint32_t)v << 8); return;
    case SFR_TOSU: pic->stack[pic->sp] = (pic->stack[pic->sp] & ~0x1F0000u) | ((uint32_t)(v & 0x1F) << 16); return;
    case SFR_STKPTR: pic->sp = v & 0x1F; pic->ram[a] = v; return;
    case SFR_BSR: pic->ram[a] = v & 0x0F; return;
  }
  pic->ram[a] = v;
  if(!core_sfr(a) && pic->board && pic->board->sfr_write)
    pic->board->sfr_write(pic, a, v);
}

static void push(struct pic18 *pic, uint32_t v)
{
  if(pic->sp >= PIC_STACK_DEPTH) {
    fprintf(stderr, "pic18: stack overflow at pc=%05X\n", pic->pc);
    pic->ram[SFR_STKPTR] |= 0x80;
    return;
  }
  pic->stack[++pic->sp] = v;
}

static uint32_t pop(struct pic18 *pic)
{
  if(pic->sp == 0) {
    fprintf(stderr, "pic18: stack underflow at pc=%05X\n", pic->pc);
    pic->ram[SFR_STKPTR] |= 0x40;
    return 0;
  }
  return pic->stack[pic->sp--];
}

static void nz(struct pic18 *pic, uint8_t r)
{
  STATUS &= (uint8_t)~(ST_Z | ST_N);
  if(r == 0) STATUS |= ST_Z;
  if(r & 0x80) STATUS |= ST_N;
}

/* a + b + c with every flag; subtraction is a + ~b + carry. */
static uint8_t add(struct pic18 *pic, uint8_t a, uint8_t b, unsigned c)
{
  unsigned r = (unsigned)a + b + c;
  uint8_t r8 = (uint8_t)r;

  STATUS &= (uint8_t)~(ST_C | ST_DC | ST_OV);
  if(r > 0xFF) STATUS |= ST_C;
  if(((a & 0x0F) + (b & 0x0F) + c) > 0x0F) STATUS |= ST_DC;
  if((~(a ^ b) & (a ^ r8)) & 0x80) STATUS |= ST_OV;
  nz(pic, r8);
  return r8;
}

static uint16_t faddr(struct pic18 *pic, uint16_t op)
{
  uint8_t f = (uint8_t)op;
  if(op & 0x100) return (uint16_t)(((BSR & 0x0F) << 8) | f);
  return f < PIC_ACCESS_LOW ? f : (uint16_t)(0xF00 | f);
}

static uint8_t tblread(struct pic18 *pic, uint32_t at)
{
  if(at < PIC_FLASH_SIZE) return pic->flash[at];
  if(at >= 0x300000 && at < 0x300010) return pic->config[at - 0x300000];
  if(at == 0x3FFFFE) return 0x00;      /* DEVID1/2: not read by this firmware */
  if(at == 0x3FFFFF) return 0x00;
  return 0;
}

void pic18_reset(struct pic18 *pic)
{
  memset(pic->ram, 0, sizeof(pic->ram));
  pic->pc = 0;
  pic->sp = 0;
  pic->cycles = 0;
  pic->wdt_clear = 0;
  pic->halted = 0;
  pic->halt_reason = NULL;
  /* Ports are inputs after a reset. */
  for(uint16_t a = SFR_TRISA; a <= SFR_TRISG; a++) pic->ram[a] = 0xFF;
  memset(pic->flash_latch, 0xFF, sizeof(pic->flash_latch));
  pic->flash_row = 0;
  pic->reset_req = 0;
  if(!pic->write_block) { pic->write_block = 64; pic->flash_end = PIC_FLASH_SIZE; }
}

static int hexbyte(const char *s)
{
  int v;
  return sscanf(s, "%2x", &v) == 1 ? v : -1;
}

int pic18_load_hex(struct pic18 *pic, const char *path)
{
  FILE *f = fopen(path, "r");
  char line[600];
  uint32_t base = 0;
  int n = 0;

  if(!f) return -1;
  memset(pic->flash, 0xFF, sizeof(pic->flash));
  memset(pic->config, 0xFF, sizeof(pic->config));
  memset(pic->eeprom, 0xFF, sizeof(pic->eeprom));
  while(fgets(line, sizeof(line), f)) {
    int len, type;
    uint32_t addr;
    if(line[0] != ':') continue;
    len = hexbyte(line + 1);
    addr = (uint32_t)((hexbyte(line + 3) << 8) | hexbyte(line + 5));
    type = hexbyte(line + 7);
    if(type == 4) { base = (uint32_t)((hexbyte(line + 9) << 8) | hexbyte(line + 11)) << 16; continue; }
    if(type == 1) break;
    if(type != 0) continue;
    for(int i = 0; i < len; i++) {
      uint32_t at = base + addr + (uint32_t)i;
      int b = hexbyte(line + 9 + 2 * i);
      if(at < PIC_FLASH_SIZE) { pic->flash[at] = (uint8_t)b; n++; }
      else if(at >= 0x300000 && at < 0x300010) pic->config[at - 0x300000] = (uint8_t)b;
      else if(at >= 0xF00000 && at < 0xF00400) pic->eeprom[at - 0xF00000] = (uint8_t)b;
    }
  }
  fclose(f);
  return n;
}

#define SKIP() do { pic->pc += 2; cyc = 2; } while(0)

unsigned pic18_step(struct pic18 *pic)
{
  uint32_t at = pic->pc;
  uint16_t op, op2;
  unsigned cyc = 1;
  uint16_t a;
  uint8_t v, r;
  int d, b;

  if(pic->halted) return 1;
  op = fetch(pic, at);
  pic->pc = at + 2;
  d = (op >> 9) & 1;            /* destination: 1 = f, 0 = W */

  switch(op >> 12) {
  case 0x0:
    if(op == 0x0000) break;                                   /* NOP */
    if(op == 0x0003) { pic->halted = 1; pic->halt_reason = "SLEEP"; break; }
    if(op == 0x0004) { pic->wdt_clear = pic->cycles; break; } /* CLRWDT */
    if(op == 0x0005) { push(pic, pic->pc); break; }           /* PUSH */
    if(op == 0x0006) { pop(pic); break; }                     /* POP */
    if(op == 0x0007) {                                        /* DAW */
      unsigned w = W;
      if((w & 0x0F) > 9 || (STATUS & ST_DC)) w += 6;
      if(((w >> 4) & 0x1F) > 9 || (STATUS & ST_C)) w += 0x60;
      if(w > 0xFF) STATUS |= ST_C; else STATUS &= (uint8_t)~ST_C;
      W = (uint8_t)w;
      break;
    }
    if(op >= 0x0008 && op <= 0x000F) {                       /* TBLRD / TBLWT */
      uint32_t p = (uint32_t)(pic->ram[SFR_TBLPTRL] | (pic->ram[SFR_TBLPTRH] << 8) |
                              ((pic->ram[SFR_TBLPTRU] & 0x3F) << 16));
      int mode = op & 3;
      if(mode == 3) p++;
      if(op < 0x000C) pic->ram[SFR_TABLAT] = tblread(pic, p);
      else { pic->flash_latch[p & (pic->write_block - 1)] = pic->ram[SFR_TABLAT]; pic->flash_row = p & ~(pic->write_block - 1); }
      if(mode == 1) p++;
      if(mode == 2) p--;
      pic->ram[SFR_TBLPTRL] = (uint8_t)p;
      pic->ram[SFR_TBLPTRH] = (uint8_t)(p >> 8);
      pic->ram[SFR_TBLPTRU] = (uint8_t)((p >> 16) & 0x3F);
      cyc = 2;
      break;
    }
    if((op & 0xFFFE) == 0x0010) {                             /* RETFIE */
      pic->pc = pop(pic);
      if(op & 1) { W = pic->shadow_w; STATUS = pic->shadow_status; BSR = pic->shadow_bsr; }
      pic->ram[SFR_INTCON] |= 0x80;
      cyc = 2;
      break;
    }
    if((op & 0xFFFE) == 0x0012) {                             /* RETURN */
      pic->pc = pop(pic);
      if(op & 1) { W = pic->shadow_w; STATUS = pic->shadow_status; BSR = pic->shadow_bsr; }
      cyc = 2;
      break;
    }
    if(op == 0x00FF) { pic->reset_req = 1; break; }         /* RESET: the board does it */
    if((op & 0xFFF0) == 0x0100) { BSR = op & 0x0F; break; }   /* MOVLB */
    if((op & 0xFE00) == 0x0200) {                             /* MULWF */
      a = effective(pic, faddr(pic, op));
      unsigned p = (unsigned)W * rd(pic, a);
      pic->ram[SFR_PRODL] = (uint8_t)p; pic->ram[SFR_PRODH] = (uint8_t)(p >> 8);
      break;
    }
    if((op & 0xFC00) == 0x0400) {                             /* DECF */
      a = effective(pic, faddr(pic, op));
      r = add(pic, rd(pic, a), 0xFE, 1);
      if(d) wr(pic, a, r); else W = r;
      break;
    }
    if((op & 0xFF00) == 0x0800) { W = add(pic, (uint8_t)op, (uint8_t)~W, 1); break; }  /* SUBLW */
    if((op & 0xFF00) == 0x0900) { W |= (uint8_t)op; nz(pic, W); break; }              /* IORLW */
    if((op & 0xFF00) == 0x0A00) { W ^= (uint8_t)op; nz(pic, W); break; }              /* XORLW */
    if((op & 0xFF00) == 0x0B00) { W &= (uint8_t)op; nz(pic, W); break; }              /* ANDLW */
    if((op & 0xFF00) == 0x0C00) { W = (uint8_t)op; pic->pc = pop(pic); cyc = 2; break; } /* RETLW */
    if((op & 0xFF00) == 0x0D00) {                                                      /* MULLW */
      unsigned p = (unsigned)W * (uint8_t)op;
      pic->ram[SFR_PRODL] = (uint8_t)p; pic->ram[SFR_PRODH] = (uint8_t)(p >> 8);
      break;
    }
    if((op & 0xFF00) == 0x0E00) { W = (uint8_t)op; break; }                            /* MOVLW */
    if((op & 0xFF00) == 0x0F00) { W = add(pic, W, (uint8_t)op, 0); break; }            /* ADDLW */
    goto unknown;

  case 0x1: case 0x2: case 0x3: case 0x4: case 0x5: {
    unsigned sub = (op >> 10) & 0x3F;            /* top 6 bits */
    a = effective(pic, faddr(pic, op));
    v = rd(pic, a);
    switch(sub) {
      case 0x04: r = v | W; nz(pic, r); break;                       /* IORWF */
      case 0x05: r = v & W; nz(pic, r); break;                       /* ANDWF */
      case 0x06: r = v ^ W; nz(pic, r); break;                       /* XORWF */
      case 0x07: r = (uint8_t)~v; nz(pic, r); break;                 /* COMF */
      case 0x08: r = add(pic, v, W, (STATUS & ST_C) ? 1 : 0); break; /* ADDWFC */
      case 0x09: r = add(pic, v, W, 0); break;                       /* ADDWF */
      case 0x0A: r = add(pic, v, 1, 0); break;                       /* INCF */
      case 0x0B: r = (uint8_t)(v - 1); if(r == 0) SKIP(); break;     /* DECFSZ */
      case 0x0C: {                                                   /* RRCF */
        unsigned c = STATUS & ST_C;
        r = (uint8_t)((v >> 1) | (c ? 0x80 : 0));
        if(v & 1) STATUS |= ST_C; else STATUS &= (uint8_t)~ST_C;
        nz(pic, r); break;
      }
      case 0x0D: {                                                   /* RLCF */
        unsigned c = STATUS & ST_C;
        r = (uint8_t)((v << 1) | (c ? 1 : 0));
        if(v & 0x80) STATUS |= ST_C; else STATUS &= (uint8_t)~ST_C;
        nz(pic, r); break;
      }
      case 0x0E: r = (uint8_t)((v << 4) | (v >> 4)); break;          /* SWAPF */
      case 0x0F: r = (uint8_t)(v + 1); if(r == 0) SKIP(); break;     /* INCFSZ */
      case 0x10: r = (uint8_t)((v >> 1) | (v << 7)); nz(pic, r); break; /* RRNCF */
      case 0x11: r = (uint8_t)((v << 1) | (v >> 7)); nz(pic, r); break; /* RLNCF */
      case 0x12: r = (uint8_t)(v + 1); if(r != 0) SKIP(); break;     /* INFSNZ */
      case 0x13: r = (uint8_t)(v - 1); if(r != 0) SKIP(); break;     /* DCFSNZ */
      case 0x14: r = v; nz(pic, r); break;                           /* MOVF */
      case 0x15: r = add(pic, W, (uint8_t)~v, (STATUS & ST_C) ? 1 : 0); break; /* SUBFWB */
      case 0x16: r = add(pic, v, (uint8_t)~W, (STATUS & ST_C) ? 1 : 0); break; /* SUBWFB */
      case 0x17: r = add(pic, v, (uint8_t)~W, 1); break;             /* SUBWF */
      default: goto unknown;
    }
    if(d) wr(pic, a, r); else W = r;
    break;
  }

  case 0x6: {
    unsigned sub = (op >> 9) & 7;
    a = effective(pic, faddr(pic, op));
    switch(sub) {
      case 0: if(rd(pic, a) < W) SKIP(); break;                      /* CPFSLT */
      case 1: if(rd(pic, a) == W) SKIP(); break;                     /* CPFSEQ */
      case 2: if(rd(pic, a) > W) SKIP(); break;                      /* CPFSGT */
      case 3: if(rd(pic, a) == 0) SKIP(); break;                     /* TSTFSZ */
      case 4: wr(pic, a, 0xFF); break;                               /* SETF */
      case 5: wr(pic, a, 0); STATUS |= ST_Z; break;                  /* CLRF */
      case 6: wr(pic, a, add(pic, 0, (uint8_t)~rd(pic, a), 1)); break; /* NEGF */
      case 7: wr(pic, a, W); break;                                  /* MOVWF */
    }
    break;
  }

  case 0x7: case 0x8: case 0x9: case 0xA: case 0xB:
    b = (op >> 9) & 7;
    a = effective(pic, faddr(pic, op));
    switch(op >> 12) {
      case 0x7: wr(pic, a, (uint8_t)(rd(pic, a) ^ (1 << b))); break;    /* BTG */
      case 0x8: wr(pic, a, (uint8_t)(rd(pic, a) | (1 << b))); break;    /* BSF */
      case 0x9: wr(pic, a, (uint8_t)(rd(pic, a) & ~(1 << b))); break;   /* BCF */
      case 0xA: if(rd(pic, a) & (1 << b)) SKIP(); break;                /* BTFSS */
      case 0xB: if(!(rd(pic, a) & (1 << b))) SKIP(); break;             /* BTFSC */
    }
    break;

  case 0xC: {                                                         /* MOVFF */
    uint16_t src = op & 0x0FFF, dst, da;
    op2 = fetch(pic, pic->pc);
    pic->pc += 2;
    dst = op2 & 0x0FFF;
    v = rd(pic, effective(pic, src));
    da = effective(pic, dst);
    /* vm-audit: a store the VM program steers lands here (every arena write
     * is a MOVFF through an FSR). Record where, to measure the bound. */
    if(pic->audit_on && is_indirect(dst) && da != 0xFFFF &&
       at >= pic->audit_lo && at <= pic->audit_hi) {
      if(!pic->audit_stores || da < pic->audit_min) pic->audit_min = da;
      if(!pic->audit_stores || da > pic->audit_max) pic->audit_max = da;
      pic->audit_stores++;
      if(da < 0x0A70u || da > 0x0CFFu) {
        pic->audit_escapes++;
        if(pic->audit_escapes <= 20)
          fprintf(stderr, "vm-audit: ESCAPE store pc=%05X dst=%03X v=%02X\n", at, da, v);
      }
    }
    wr(pic, da, v);
    cyc = 2;
    break;
  }

  case 0xD: {                                                         /* BRA / RCALL */
    int n = op & 0x7FF;
    if(n & 0x400) n -= 0x800;
    if(op & 0x800) push(pic, pic->pc);
    pic->pc = (uint32_t)((int32_t)pic->pc + 2 * n);
    cyc = 2;
    break;
  }

  case 0xE: {
    unsigned sub = (op >> 8) & 0xF;
    if(sub <= 7) {                                                    /* conditional branches */
      int n = (int8_t)(op & 0xFF), take = 0;
      switch(sub) {
        case 0: take = STATUS & ST_Z; break;          /* BZ */
        case 1: take = !(STATUS & ST_Z); break;       /* BNZ */
        case 2: take = STATUS & ST_C; break;          /* BC */
        case 3: take = !(STATUS & ST_C); break;       /* BNC */
        case 4: take = STATUS & ST_OV; break;         /* BOV */
        case 5: take = !(STATUS & ST_OV); break;      /* BNOV */
        case 6: take = STATUS & ST_N; break;          /* BN */
        case 7: take = !(STATUS & ST_N); break;       /* BNN */
      }
      if(take) { pic->pc = (uint32_t)((int32_t)pic->pc + 2 * n); cyc = 2; }
      break;
    }
    if(sub == 0xC || sub == 0xD || sub == 0xF) {                      /* CALL / GOTO */
      uint32_t target;
      op2 = fetch(pic, pic->pc);
      pic->pc += 2;
      target = ((uint32_t)(op2 & 0x0FFF) << 8 | (op & 0xFF)) << 1;
      if(sub != 0xF) {
        if(op & 0x100) { pic->shadow_w = W; pic->shadow_status = STATUS; pic->shadow_bsr = BSR; }
        push(pic, pic->pc);
      }
      pic->pc = target;
      cyc = 2;
      break;
    }
    if(sub == 0xE && (op & 0xC0) == 0) {                              /* LFSR */
      int n = (op >> 4) & 3;
      op2 = fetch(pic, pic->pc);
      pic->pc += 2;
      if(n < 3) fsr_set(pic, n, (uint16_t)(((op & 0x0F) << 8) | (op2 & 0xFF)));
      cyc = 2;
      break;
    }
    goto unknown;
  }

  case 0xF:                                                           /* NOP (2nd word) */
    break;
  }
  pic->cycles += cyc;
  return cyc;

unknown:
  pic->halted = 1;
  pic->halt_reason = "unknown opcode";
  fprintf(stderr, "pic18: unknown opcode %04X at %05X\n", op, at);
  pic->cycles += cyc;
  return cyc;
}
