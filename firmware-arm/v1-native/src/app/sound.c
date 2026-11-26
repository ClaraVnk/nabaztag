/*
 * sound.c -- frame 13 (DESIGN.md section 8): a sound the Terrier holds for
 * this rabbit, fetched into the serial flash, then played through the OKI.
 *
 * The answer to a poll says index and length; at the next closed
 * connection net.c asks GET /vl/v1/snd?sn=<mac>&i=<index> and hands each
 * byte of the body here. The body is an Audi file (Violet's: a 16-byte
 * header, then the ADPCM data, nabv1.audi on the Terrier), written from
 * page PG_SND_DL on, a page at a time; when it is all in, oki_play() plays
 * it from there, as sound_boot() plays sound 0 of the directory. A body
 * cut short is not played. Lazare could not be made to speak from his
 * page on our firmware (2026-11-25): this is that.
 */
#include "board.h"

#define PG_SND_DL   (PG_SOUNDS + 24)            /* page 1000: the fetched sound */
#define SND_MAX     60000UL                     /* ~15 s of ADPCM; the region holds far more */

static uint8_t  want = 0xFF;                    /* the index offered, 0xFF none */
static uint8_t  fetching;
static uint32_t length, got;
static uint16_t fill, pg;

void sound_offer(uint8_t index, uint32_t len)
{
  if(len < 32 || len > SND_MAX) { oki_last = 4; oki_bursts = 0; oki_stalls = 0; log_s("sound: refused, "); log_dec((uint16_t)(len >> 8)); log_line("00 B"); return; }
  if(fetching) return;
  want = index; length = len;
  log_s("sound: "); log_dec(index); log_s(" offered, "); log_dec((uint16_t)len); log_line(" B");
}

uint8_t sound_wanted(void) { return want != 0xFF && !fetching; }
uint8_t sound_index(void) { return want; }

void sound_begin(void)
{
  fetching = 1; got = 0; fill = 0; pg = PG_SND_DL;
}

void sound_byte(uint8_t c)
{
  if(got >= length) return;
  xfer_page[fill++] = c; got++;
  if(fill == flash_page_size || got == length) { flash_write(pg++, xfer_page, fill); fill = 0; }
}

void sound_end(uint8_t complete)
{
  fetching = 0;
  want = 0xFF;
  if(!complete || got != length) {
    log_s("sound: incomplete at "); log_dec((uint16_t)got); log_nl();
    oki_last = 3; oki_bursts = (uint16_t)(got >> 8); oki_stalls = 0;
    return;
  }
  log_line("sound: in, playing");
  oki_play(PG_SND_DL, length - 16);
}
