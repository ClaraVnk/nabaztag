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
 */
#include "board.h"
#include "sha256.h"

#define LOADER_VERSION  1

/* Lazare's configuration, byte for byte (v1-icsp/dumps/violet-v1-1.4b.hex at
 * 0x300000): HS oscillator, power-up timer, brown-out reset on (2.7 V),
 * watchdog on with the 1:128 postscaler (~512 ms), MCLR on, LVP off, no code
 * protection, no write protection. The 1.4b firmware never let the watchdog
 * expire and neither does this one: every loop below clears it. */
__code __at(0x300000) uint8_t config_bits[14] = {
  0x00, 0x22,   /* CONFIG1L (unimplemented), CONFIG1H: OSCS off, HS */
  0x0A, 0x0F,   /* CONFIG2L: PWRT on, BOR on, BORV 2.7 V; CONFIG2H: WDT on, 1:128 */
  0x83, 0x83,   /* CONFIG3L (unimplemented), CONFIG3H: MCLR on, CCP2 on RC1 */
  0x80, 0x00,   /* CONFIG4L: DEBUG off, LVP off, STVR off; CONFIG4H */
  0x0F, 0xC0,   /* CONFIG5: no code protection */
  0x0F, 0xE0,   /* CONFIG6: no write protection (WRTB once the loader is proven on the rabbit) */
  0x0F, 0x40    /* CONFIG7: no table-read protection */
};

/* One initialised byte, so that crt0iz finds a .cinit table: without any
 * initialised data the symbol resolves to 0 and the startup would read the
 * reset vector as a table of RAM to fill (gplink's "no section" warning). */
static uint8_t loader_version = LOADER_VERSION;
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

static void pic_seek(uint32_t a)
{
  TBLPTRU = (uint8_t)(a >> 16); TBLPTRH = (uint8_t)(a >> 8); TBLPTRL = (uint8_t)a;
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

static void row_erase(uint32_t a)
{
  pic_seek(a);
  EECON1 = 0x94;                        /* EEPGD, FREE, WREN */
  unlock_and_write();
}

/* WRITE_BLOCK bytes at a (aligned): the holding latches are filled with
 * TBLWT+* (pre-increment) from a-1, so the pointer ends inside the block. */
static void block_write(uint32_t a, __data const uint8_t *p)
{
  uint8_t i;
  pic_seek(a - 1);
  for(i = 0; i < WRITE_BLOCK; i++) { TABLAT = p[i]; __asm__("tblwt+*"); }
  EECON1 = 0x84;                        /* EEPGD, WREN */
  unlock_and_write();
  clrwdt();
}

static uint8_t app_present(void)
{
  pic_seek(APP_BASE);
  if(pic_next() != 0xFF) return 1;
  return pic_next() != 0xFF;
}

/* --- slots ---------------------------------------------------------------- */

static void log_slot(const __code char *s, uint8_t slot)
{
  log_s(s); uart_putc(slot == SLOT_NONE ? '-' : (char)('A' + slot));
}

/* The slot's header into hdr; 1 if it describes a complete image for this board. */
static uint8_t slot_header_ok(uint8_t slot)
{
  flash_read(slot_page(slot), 0, hdr, HDR_SIZE);
  return hdr[0] == 'N' && hdr[1] == 'B' && hdr[2] == 'V' && hdr[3] == '1'
      && HDR->format == 1 && HDR->board == BOARD_ID
      && HDR->length >= 64 && HDR->length <= APP_MAX && (HDR->length & 63) == 0
      && hdr[HDR_COMPLETE_OFF] == HDR_COMPLETE;
}

static uint8_t slot_hmac_ok(uint8_t slot)
{
  uint8_t mac[32], i, diff = 0;
  uint32_t left = HDR->length;
  if(!have_key) return 0;
  hmac_init(key, 32);
  hmac_update(hdr, 12);
  slot_seek(slot);
  while(left) { slot_next(buf, 64); hmac_update(buf, 64); left -= 64; }
  hmac_final(mac);
  for(i = 0; i < 32; i++) diff |= mac[i] ^ hdr[HDR_HMAC_OFF + i];
  return diff == 0;
}

/* Does the PIC hold the slot's image, byte for byte? (hdr is the slot's.)
 * TBLPTR is set again before each chunk: it is also what the compiler reads
 * its constants and strings with, so anything between two chunks (a log
 * line, the serial flash driver, the HMAC's table) moves it (2026-11-24:
 * the first seed wrote page 1 right and garbage after it). */
static uint8_t pic_matches(uint8_t slot)
{
  uint32_t left = HDR->length, a = APP_BASE;
  uint8_t i;
  slot_seek(slot);
  while(left) {
    slot_next(buf, 64);
    pic_seek(a);
    for(i = 0; i < 64; i++) if(pic_next() != buf[i]) return 0;
    a += 64; left -= 64;
    clrwdt();
  }
  return 1;
}

static void copy_slot(uint8_t slot)
{
  uint32_t a = APP_BASE, left = HDR->length;
  uint8_t i;
  slot_seek(slot);
  while(left) {
    slot_next(buf, 64);
    row_erase(a);
    for(i = 0; i < 64; i += WRITE_BLOCK) block_write(a + i, buf + i);
    a += 64; left -= 64;
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
 * back to from the first update on. */
static void seed_slot_a(void)
{
  uint32_t left, a;
  uint16_t idx = 1, n, k;
  uint8_t i;
  pic_seek(APP_BASE + APP_HDR_OFF);
  for(i = 0; i < 16; i++) hdr[i] = pic_next();
  for(i = 16; i < HDR_SIZE; i++) hdr[i] = 0xFF;
  hdr[12] = 0; hdr[13] = 0; hdr[14] = 0; hdr[15] = 0;
  if(hdr[0] != 'N' || hdr[1] != 'B' || hdr[2] != 'V' || hdr[3] != '1' || HDR->length < 64
     || HDR->length > APP_MAX || (HDR->length & 63)) { log_line("L: app has no header"); return; }
  flash_write(PG_SLOT_A, hdr, 0);       /* the header page blank until the image is in */
  hmac_init(key, 32);
  hmac_update(hdr, 12);
  left = HDR->length;
  a = APP_BASE;
  while(left) {
    n = left > flash_page_size ? flash_page_size : (uint16_t)left;
    pic_seek(a);                        /* again each page: see pic_matches */
    for(k = 0; k < n; k++) page[k] = pic_next();
    hmac_update(page, n);
    slot_write_page(SLOT_A, idx++, page, n);
    a += n; left -= n;
  }
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
  log_nl(); log_s("L: loader "); log_hex8(loader_version); log_nl();
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
    log_slot("L: pending ", slot); log_s(" v"); log_hex16(HDR->version); log_nl();
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
