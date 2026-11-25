/*
 * loader.c -- the permanent boot block of the v1's native firmware (phase 2).
 *
 * At every power-on, before the application: is there an image waiting in a
 * slot of the serial flash (the application downloaded it and marked it
 * "pending")? If its HMAC-SHA-256 under this rabbit's key checks, copy it
 * into the PIC's flash row by row, read it back, record it as the active
 * slot. Does the PIC still hold the active slot's image? If not (a power
 * cut mid-copy, a corrupted row), copy it again, and if that image cannot
 * be installed, fall back to the other slot. Has the new image booted
 * LS_MAX_TRIES times without reaching the Terrier? Back to the other slot.
 * Then jump to APP_BASE.
 *
 * The loader never writes below APP_BASE: nothing in it erases itself, and
 * the configuration's WRTB can protect the boot block on top of that
 * (DS39612: 0x0000-0x07FF on the 6525; DESIGN.md). Naboot's idea
 * (firmware-arm/docs/OTA-ANTIBRICK.md) on a board that, unlike the tag's,
 * has a second non-volatile memory: the window a power cut leaves is closed
 * by copying from the slot again, not by hoping.
 *
 * The key: 32 bytes in the data EEPROM at 0x000 (written at ICSP time with
 * the firmware; all 0xFF = no key, no update is ever installed). The Terrier
 * holds the same key for this rabbit and signs its images with it.
 *
 * Built with SDCC's crt0 (no data initialisation, 150 bytes less than
 * crt0iz): no static here or in common/ relies on a zero or an initial
 * value before it is written (2026-11-25).
 */
#include "board.h"
#include "sha256.h"

#define LOADER_VERSION  1

/* The configuration words (watchdog on, ~512 ms) are config.c, shared with
 * the SHA test image; every loop below clears the watchdog.
 *
 * Sizes are in 64-byte rows and the PIC's addresses in rows too (16-bit
 * arithmetic): SDCC's 32-bit code cost the phase-2 loader 12 KB, and with
 * the hash in assembly the row counts are what brought it under 8 KB
 * (2026-11-25). A row is the PIC's erase unit, an image is a multiple of
 * 64 bytes, and 0x3000..0x17FFF (the 6627) is 1 344 rows. */
#define ROW(addr)       ((uint16_t)((addr) >> 6))
#define APP_ROW         ROW(APP_BASE)
#define APP_MAX_ROWS    ROW(APP_MAX)

static uint8_t key[32], have_key;
static uint8_t hdr[HDR_SIZE];           /* the header of the slot being looked at */
static uint8_t buf[64];
static uint8_t page[528];               /* one serial flash page, for the seed */
static struct lstate st;

#define HDR     ((__data struct app_hdr *)hdr)

/* --- the PIC's own memories --------------------------------------------- */

static uint8_t ee_read(uint8_t a)
{
  EEADR = a; EEADRH = 0;
  EECON1 = 0x01;                        /* EEPGD 0, CFGS 0: the data EEPROM; RD */
  return EEDATA;
}

/* TBLPTR at the first byte of a row. */
static void pic_seek(uint16_t row)
{
  TBLPTRU = (uint8_t)(row >> 10); TBLPTRH = (uint8_t)(row >> 2); TBLPTRL = (uint8_t)(row << 6);
}

static uint8_t pic_next(void)
{
  __asm__("tblrd*+");
  return TABLAT;
}

/* The write sequence of DS39612 6.5 / DS39646 6.5, as one run of
 * instructions: 55h, AAh to EECON2, then WR. The CPU stalls until the row is
 * done (about 2 ms on the real part). */
static void unlock_and_write(void)
{
  __asm__("movlw 0x55\n\tmovwf _EECON2\n\tmovlw 0xaa\n\tmovwf _EECON2\n\tbsf _EECON1, 1\n\tnop\n\tnop");
}

/* The row TBLPTR is in. */
static void row_erase(void)
{
  EECON1 = 0x94;                        /* EEPGD, FREE, WREN */
  unlock_and_write();
}

/* WRITE_BLOCK bytes behind TBLPTR: the holding latches are filled with
 * TBLWT+* (pre-increment), so the pointer must be at block-1 on entry and
 * ends on the block's last byte, ready for the next block. */
static void block_write(__data const uint8_t *p)
{
  uint8_t i = WRITE_BLOCK;
  while(i--) { TABLAT = *p++; __asm__("tblwt+*"); }
  EECON1 = 0x84;                        /* EEPGD, WREN */
  unlock_and_write();
  clrwdt();
}

static uint8_t app_present(void)
{
  pic_seek(APP_ROW);
  if(pic_next() != 0xFF) return 1;
  return pic_next() != 0xFF;
}

/* --- slots ---------------------------------------------------------------- */

static void log_slot(const __code char *s, uint8_t slot)
{
  log_s(s); uart_putc(slot == SLOT_NONE ? '-' : (char)('A' + slot));
}

/* hdr's length in rows; 0 unless it is 64..APP_MAX and a multiple of 64. */
static uint16_t hdr_rows(void)
{
  uint16_t rows;
  if(hdr[11] || (hdr[8] & 63)) return 0;
  rows = (uint16_t)(hdr[8] >> 6) | (uint16_t)hdr[9] << 2 | (uint16_t)hdr[10] << 10;
  return rows <= APP_MAX_ROWS ? rows : 0;
}

static uint8_t hdr_magic_ok(void)
{
  return hdr[0] == 'N' && hdr[1] == 'B' && hdr[2] == 'V' && hdr[3] == '1';
}

/* The slot's header into hdr; 1 if it describes a complete image for this board. */
static uint8_t slot_header_ok(uint8_t slot)
{
  flash_read(slot_page(slot), 0, hdr, HDR_SIZE);
  return hdr_magic_ok() && HDR->format == 1 && HDR->board == BOARD_ID
      && hdr_rows() && hdr[HDR_COMPLETE_OFF] == HDR_COMPLETE;
}

static uint8_t slot_hmac_ok(uint8_t slot)
{
  uint8_t mac[32], i, diff = 0;
  uint16_t rows = hdr_rows();
  if(!have_key) return 0;
  hmac_init(key, 32);
  hmac_update(hdr, 12);
  slot_seek(slot);
  while(rows--) { slot_next(buf, 64); hmac_update(buf, 64); }
  hmac_final(mac);
  for(i = 0; i < 32; i++) diff |= mac[i] ^ hdr[HDR_HMAC_OFF + i];
  return diff == 0;
}

/* Does the PIC hold the slot's image, byte for byte? (hdr is the slot's.)
 * TBLPTR is set again before each row: it is also what the compiler reads
 * its constants and strings with, so anything between two rows (a log
 * line, the serial flash driver, the hash's table) moves it (2026-11-24:
 * the first seed wrote page 1 right and garbage after it). */
static uint8_t pic_matches(uint8_t slot)
{
  uint16_t rows = hdr_rows(), row = APP_ROW;
  uint8_t i;
  slot_seek(slot);
  while(rows--) {
    slot_next(buf, 64);
    pic_seek(row++);
    for(i = 0; i < 64; i++) if(pic_next() != buf[i]) return 0;
    clrwdt();
  }
  return 1;
}

static void copy_slot(uint8_t slot)
{
  uint16_t rows = hdr_rows(), row = APP_ROW;
  uint8_t i;
  slot_seek(slot);
  while(rows--) {
    slot_next(buf, 64);
    pic_seek(row++);
    row_erase();
    __asm__("tblrd*-");                 /* the first block's pre-increment */
    for(i = 0; i < 64; i += WRITE_BLOCK) block_write(buf + i);
  }
  EECON1 = 0;
}

/* 1 when the PIC holds the slot's image at the end. */
static uint8_t install(uint8_t slot)
{
  uint8_t k;
  if(!slot_header_ok(slot)) { log_slot("L: no image in ", slot); log_nl(); return 0; }
  if(!slot_hmac_ok(slot)) { log_slot("L: bad hmac in ", slot); log_nl(); return 0; }
  for(k = 0; k < 2; k++) {
    log_slot("L: copy ", slot); log_nl();
    copy_slot(slot);
    if(pic_matches(slot)) return 1;
    log_line("L: verify failed");
  }
  return 0;
}

/* First boot after ICSP: the application in the PIC becomes slot A, with
 * the HMAC this key gives it, so that there is a known-good image to fall
 * back to from the first update on. The PIC is read row by row, the slot
 * written page by page (264 or 528 bytes), the HMAC fed per page. */
static uint16_t fill, idx;

static void seed_flush(void)
{
  hmac_update(page, fill);
  slot_write_page(SLOT_A, idx++, page, fill);
  fill = 0;
}

static void seed_slot_a(void)
{
  uint16_t rows, row = APP_ROW;
  uint8_t i;
  pic_seek(APP_ROW + 1);                /* APP_HDR_OFF: the second row */
  for(i = 0; i < 12; i++) hdr[i] = pic_next();
  for(i = 12; i < HDR_SIZE; i++) hdr[i] = i < 16 ? 0 : 0xFF;   /* pending 0, the rest blank */
  rows = hdr_rows();
  if(!hdr_magic_ok() || !rows) { log_line("L: app has no header"); return; }
  flash_write(PG_SLOT_A, hdr, 0);       /* the header page blank until the image is in */
  hmac_init(key, 32);
  hmac_update(hdr, 12);
  fill = 0; idx = 1;
  while(rows--) {
    pic_seek(row++);                    /* again each row: see pic_matches */
    for(i = 0; i < 64; i++) buf[i] = pic_next();
    for(i = 0; i < 64; i++) { page[fill++] = buf[i]; if(fill == flash_page_size) seed_flush(); }
  }
  if(fill) seed_flush();
  hmac_final(hdr + HDR_HMAC_OFF);
  hdr[HDR_COMPLETE_OFF] = HDR_COMPLETE;
  flash_write(PG_SLOT_A, hdr, HDR_SIZE);
  log_line("L: seeded A");
}

static void fallback(void)
{
  uint8_t other = st.active == SLOT_A ? SLOT_B : SLOT_A;
  log_slot("L: fall back to ", other); log_nl();
  if(install(other)) st.active = other;
  else log_line("L: no good slot");
  st.tries = 0;
  st.flags = LS_CONFIRMED;              /* stop counting either way */
  state_write(&st);
}

static void run_app(void)
{
  if(!app_present()) { log_line("L: no app"); for(;;) clrwdt(); }
  log_line("L: run");
  STKPTR = 0;
  __asm__("goto " STR(APP_BASE));
}

void main(void)
{
  uint8_t i, slot;
  uart_init();
  spi_init();
  log_nl(); log_s("L: loader "); log_hex8(LOADER_VERSION); log_nl();
  if(!flash_init()) { log_line("L: no flash"); run_app(); }
  have_key = 0;
  for(i = 0; i < 32; i++) { key[i] = ee_read(i); if(key[i] != 0xFF) have_key = 1; }
  state_read(&st);
  log_slot("L: active ", st.active); log_s(" tries "); log_hex8(st.tries); log_s(" flags "); log_hex8(st.flags); log_nl();

  /* 1. an image waiting in the other slot */
  for(slot = SLOT_A; slot <= SLOT_B; slot++) {
    if(slot == st.active) continue;
    flash_read(slot_page(slot), 0, hdr, HDR_SIZE);
    if(HDR->pending != 1 || hdr[HDR_COMPLETE_OFF] != HDR_COMPLETE) continue;
    if(HDR->version == st.skip_ver) continue;
    log_slot("L: pending ", slot); log_s(" v"); log_hex8(hdr[7]); log_hex8(hdr[6]); log_nl();
    if(install(slot)) { st.active = slot; st.tries = 0; st.flags = 0; log_line("L: installed"); }
    else { st.skip_ver = HDR->version; log_line("L: refused"); }
    state_write(&st);
  }

  /* 2. the PIC holds the active slot's image, or will */
  if(st.active == SLOT_NONE) {
    if(have_key && app_present()) {
      seed_slot_a();
      st.active = SLOT_A; st.tries = 0; st.flags = LS_CONFIRMED;
      state_write(&st);
    }
  } else if(!slot_header_ok(st.active) || !pic_matches(st.active)) {
    log_slot("L: PIC differs from ", st.active); log_nl();
    if(!install(st.active)) fallback();
  }

  /* 3. a new image gets LS_MAX_TRIES boots to confirm itself */
  if(st.active != SLOT_NONE && !(st.flags & LS_CONFIRMED)) {
    flash_read(slot_page(st.active), 0, hdr, HDR_SIZE);
    if(++st.tries > LS_MAX_TRIES) { log_line("L: never confirmed"); st.skip_ver = HDR->version; fallback(); }
    else state_write(&st);
  }
  for(i = 0; i < 32; i++) key[i] = 0;
  run_app();
}
