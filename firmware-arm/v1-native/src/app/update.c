/*
 * update.c -- a new image from the Terrier into the slot the loader is not
 * running from, then a reset into the loader (loader/loader.c), which checks
 * the HMAC and copies it into the PIC. The application never sees the key.
 *
 * The poll's answer carries our frame 10 (two bytes: the version the Terrier
 * holds for this board); if it is newer than ours, the image is fetched on
 * the next connection (GET /vl/v1/fw, net.c) and streamed into the slot a
 * page at a time: 64 bytes of header first (board.h struct app_hdr, then
 * the HMAC at 16), then the image. The slot's header page is blanked before
 * the first image byte and written last, with "pending" set and the
 * "complete" mark, so that a download cut anywhere leaves a slot the loader
 * ignores. Once the Terrier has answered a poll of this image, the loader's
 * state is marked confirmed and its boot counter stops.
 */
#include "board.h"

static struct lstate st;
static uint8_t  target;                 /* the slot to write */
static uint16_t offered;                /* the version the Terrier has; 0 none */
static uint8_t  fetching, phase;        /* phase: 0 header, 1 image, 2 all in, 3 given up */
static uint8_t  hdr[HDR_SIZE];
/* the page being received: an image here, a sound in sound.c (never both
 * at once; one buffer, the RAM is 4 KB) */
__data uint8_t xfer_page[528];
#define page xfer_page
static uint16_t fill, idx;
static uint32_t got;
static uint8_t  confirmed;

#define HDR ((__data struct app_hdr *)hdr)

void update_init(void)
{
  state_read(&st);
  target = st.active == SLOT_A ? SLOT_B : SLOT_A;
}

void update_offer(uint16_t version)
{
  if(version <= APP_VERSION || version == st.skip_ver || offered) return;
  offered = version;
  log_s("update: v"); log_dec(version); log_line(" offered");
}

uint8_t update_wanted(void) { return offered && !fetching; }

void update_begin(void)
{
  fetching = 1; phase = 0; got = 0; fill = 0; idx = 1;
}

void update_byte(uint8_t c)
{
  if(phase == 0) {
    hdr[got++] = c;
    if(got < HDR_SIZE) return;
    got = 0;
    if(hdr[0] != 'N' || hdr[1] != 'B' || hdr[2] != 'V' || hdr[3] != '1' || HDR->format != 1
       || HDR->board != BOARD_ID || HDR->length < 64 || HDR->length > APP_MAX || (HDR->length & 63)) {
      log_line("update: bad header"); phase = 3; return;
    }
    flash_write(slot_page(target), hdr, 0);         /* the header page blank while the image comes */
    log_s("update: image "); log_dec((uint16_t)HDR->length); log_s(" B into "); uart_putc((char)('A' + target)); log_nl();
    phase = 1;
    return;
  }
  if(phase != 1) return;
  page[fill++] = c; got++;
  if(fill == flash_page_size || got == HDR->length) { slot_write_page(target, idx++, page, fill); fill = 0; }
  if(got == HDR->length) phase = 2;
}

void update_end(uint8_t complete)
{
  fetching = 0;
  if(phase == 2 && complete) {
    HDR->pending = 1;
    hdr[HDR_COMPLETE_OFF] = HDR_COMPLETE;
    flash_write(slot_page(target), hdr, HDR_SIZE);
    log_line("update: written, rebooting into the loader");
    __asm__("reset");
  }
  log_s("update: incomplete at "); log_dec((uint16_t)got); log_nl();
  offered = 0;                          /* the next poll may offer it again */
}

void update_confirm(void)
{
  if(confirmed) return;
  confirmed = 1;
  if(st.flags & LS_CONFIRMED) return;
  st.flags |= LS_CONFIRMED; st.tries = 0;
  state_write(&st);
  log_line("update: image confirmed");
}
