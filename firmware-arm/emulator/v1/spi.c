/**
 * @file spi.c
 * @brief The three chips on the v1's SPI bus.
 *
 * AT45DB161 serial flash (where frame 05, the program, is written), the OKI
 * ML2870A audio codec, and the TLC5922 LED driver, which has no chip select:
 * it shifts in everything on the bus and only XLAT decides what it shows.
 */
#include <stdio.h>
#include <string.h>

#include "board.h"

/* --- AT45DB161: 4096 pages of 528 bytes ---------------------------------- */
/* Or an AT45DB081B, 4096 pages of 264 bytes addressed on 9 bits: the flash
 * of Clara's 1.4b rabbit (Lazare, photo 2026-11-24). The 1.4b firmware wrote
 * its program on 9-bit pages; read back on the 161's 10, it found what it
 * had not written and never polled again (`--flash-chip 081`). */
static unsigned page_size = 528, page_shift = 10;
static uint8_t  status_ready = 0xAC;                   /* ready, density 1011 */
void spi_flash_chip(int db081)
{
  if(db081) { page_size = 264; page_shift = 9; status_ready = 0xA4; }  /* density 1001 */
}

#define PAGES      4096
#define PAGE_SIZE  528

static uint8_t mem[PAGES][PAGE_SIZE];
static uint8_t buf[2][PAGE_SIZE];
static const char *flash_path;
static int dirty;

struct flash_cmd {
  int      active;
  uint8_t  op;
  unsigned n;           /* bytes received, opcode included */
  uint32_t addr;        /* the 24 address bits */
  unsigned page, byte;
};
static struct flash_cmd fc;

int flash_load(const char *path)
{
  FILE *f;
  flash_path = path;
  memset(mem, 0xFF, sizeof(mem));
  if(!path || !(f = fopen(path, "rb"))) return 0;
  size_t n = fread(mem, 1, sizeof(mem), f);
  fclose(f);
  return (int)n;
}

int flash_save(void)
{
  FILE *f;
  if(!dirty || !flash_path || !(f = fopen(flash_path, "wb"))) return 0;
  fwrite(mem, 1, sizeof(mem), f);
  fclose(f);
  dirty = 0;
  return 1;
}

int flash_ready(void) { return 1; }

static unsigned addr_bytes(uint8_t op)
{
  switch(op) {
    case 0x57: case 0xD7: return 0;
    default: return 3;
  }
}

static unsigned dummy_bytes(uint8_t op)
{
  switch(op) {
    case 0x52: case 0xD2: case 0x68: case 0xE8: return 4;
    case 0x54: case 0x56: case 0xD4: case 0xD6: return 1;
    default: return 0;
  }
}

static void flash_begin(void)
{
  memset(&fc, 0, sizeof(fc));
  fc.active = 1;
}

/* CS rising: the self-timed operations happen now. */
static void flash_end(void)
{
  unsigned page = fc.page;
  if(!fc.active || fc.n == 0) { fc.active = 0; return; }
  switch(fc.op) {
    case 0x53: memcpy(buf[0], mem[page], page_size); break;
    case 0x55: memcpy(buf[1], mem[page], page_size); break;
    case 0x83: case 0x88: case 0x82: memcpy(mem[page], buf[0], page_size); dirty = 1; break;
    case 0x86: case 0x89: case 0x85: memcpy(mem[page], buf[1], page_size); dirty = 1; break;
    case 0x81: memset(mem[page], 0xFF, page_size); dirty = 1; break;
    case 0x50: for(unsigned k = 0; k < 8; k++) memset(mem[(page & ~7u) + k], 0xFF, page_size); dirty = 1; break;
    case 0x58: case 0x59: break;
  }
  if(B.trace_bus && fc.op != 0x57 && fc.op != 0xD7)
    board_log("flash: op %02X page %u byte %u, %u bytes on the wire", fc.op, page, fc.byte, fc.n);
  fc.active = 0;
}

static uint8_t flash_byte(uint8_t in)
{
  uint8_t out = 0xFF;
  unsigned na = addr_bytes(fc.op), nd;

  if(fc.n == 0) {
    fc.op = in;
    fc.n = 1;
    return 0xFF;
  }
  na = addr_bytes(fc.op);
  nd = dummy_bytes(fc.op);
  if(fc.n <= na) {
    fc.addr = (fc.addr << 8) | in;
    fc.n++;
    if(fc.n == na + 1) { fc.page = (fc.addr >> page_shift) & 0xFFF; fc.byte = fc.addr & ((1u << page_shift) - 1); }
    return 0xFF;
  }
  if(fc.n <= na + nd) { fc.n++; return 0xFF; }
  fc.n++;
  switch(fc.op) {
    case 0x57: case 0xD7: out = status_ready; break;
    case 0x52: case 0xD2:
      out = mem[fc.page][fc.byte];
      fc.byte = (fc.byte + 1) % page_size;
      break;
    case 0x68: case 0xE8:                               /* continuous array read */
      out = mem[fc.page][fc.byte];
      if(++fc.byte == page_size) { fc.byte = 0; fc.page = (fc.page + 1) % PAGES; }
      break;
    case 0x54: case 0xD4: out = buf[0][fc.byte]; fc.byte = (fc.byte + 1) % page_size; break;
    case 0x56: case 0xD6: out = buf[1][fc.byte]; fc.byte = (fc.byte + 1) % page_size; break;
    case 0x84: buf[0][fc.byte] = in; fc.byte = (fc.byte + 1) % page_size; break;
    case 0x87: buf[1][fc.byte] = in; fc.byte = (fc.byte + 1) % page_size; break;
    case 0x82: buf[0][fc.byte] = in; fc.byte = (fc.byte + 1) % page_size; break;
    case 0x85: buf[1][fc.byte] = in; fc.byte = (fc.byte + 1) % page_size; break;
    default:
      if(fc.n == na + nd + 2) board_log("flash: opcode %02X is not modelled", fc.op);
      break;
  }
  return out;
}

/* --- OKI ML2870A: register pairs, and a FIFO of ADPCM -------------------- */

static int oki_on, oki_n;
static uint8_t oki_reg;
static unsigned long oki_bytes;

static uint8_t oki_byte(uint8_t in)
{
  if(oki_n++ % 2 == 0) { oki_reg = in; return 0; }
  if(B.trace_bus) board_log("oki: reg %02X <- %02X", oki_reg, in);
  oki_bytes++;
  return 0;
}

/* --- TLC5922: 16 outputs, a shift register, XLAT ------------------------- */

static uint8_t tlc_shift[14];          /* 112 bits: enough for dot correction */
static uint16_t tlc_onoff = 0xFFFF;

static void tlc_byte(uint8_t in)
{
  memmove(tlc_shift, tlc_shift + 1, sizeof(tlc_shift) - 1);
  tlc_shift[sizeof(tlc_shift) - 1] = in;
}

void spi_latch(void)
{
  int dc_mode = ((B.lat[PA] & ~B.tris[PA]) >> 5) & 1;   /* MODE: RA5 */
  uint16_t v = (uint16_t)((tlc_shift[12] << 8) | tlc_shift[13]);
  if(dc_mode) return;
  if(v != tlc_onoff) {
    char s[17];
    for(int i = 0; i < 16; i++) s[i] = (v >> (15 - i)) & 1 ? '#' : '.';
    s[16] = 0;
    board_log("leds: %s", s);
  }
  tlc_onoff = v;
}

/* --- the bus ------------------------------------------------------------- */

static int sel_flash, sel_oki;

/* A chip select is asserted when its pin is an output driven low. */
static int asserted(int p, int bit)
{
  return !((B.tris[p] >> bit) & 1) && !((B.lat[p] >> bit) & 1);
}

void spi_select(void)
{
  int f = asserted(PB, 3) || asserted(PB, 4);
  int o = asserted(PA, 2);

  if(f && !sel_flash) flash_begin();
  if(!f && sel_flash) flash_end();
  if(o && !sel_oki) oki_n = 0;
  if(o != sel_oki && B.trace_bus) board_log("oki: %s", o ? "selected" : "released");
  sel_flash = f;
  sel_oki = o;
  oki_on = o;
}

uint8_t spi_transfer(uint8_t out)
{
  uint8_t in = 0xFF;
  tlc_byte(out);
  if(sel_flash) in = flash_byte(out);
  if(sel_oki) in = oki_byte(out);
  return in;
}
