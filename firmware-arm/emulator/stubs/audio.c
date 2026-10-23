/**
 * @file audio.c
 * @brief The VLSI decoder, as a file you can listen to.
 *
 * nabgcc ships a host stub for this under testvm/stubs/hal, and it is not
 * usable here for two reasons, both of which cost an afternoon to find:
 *
 *   - its get_adc_value() returns 0, and sysButton3() is `255 - that`. The
 *     head button therefore reads as held down at every boot, which is how
 *     you put a Nabaztag into its configuration mode. Every emulated rabbit
 *     would come up in config mode and never touch the network.
 *   - its vlsi_feed_sdi() returns 0, meaning "I accepted none of your bytes",
 *     and the caller offers them again.
 *   - its play_check() does nothing, and play_check() is the whole player.
 *     The bytecode does not hand the decoder its sound: it writes into the
 *     FIFO in vaudio.c and play_check() drains that FIFO a byte at a time,
 *     asking the bytecode for more through SYS_CBPLAY when it runs dry. With
 *     an empty play_check() the FIFO fills once and stays full, so a rabbit
 *     downloads its sound in full, says nothing, and reports no error. This
 *     one is the real play_check(), with the SPI write replaced by a write to
 *     the capture file.
 *
 * So this one answers like a decoder that works, and keeps what it was fed.
 * Whatever the rabbit played lands in a file, byte for byte as the warren
 * served it — an MP3 stays an MP3 — so "what did it actually say?" is a
 * question with an answer you can play.
 */
#include <stdio.h>
#include <string.h>

#include "common.h"
#include "hal/audio.h"
#include "vm/vaudio.h"

#include "sim.h"
#include "hw.h"

static FILE *capture;
static uint32_t captured_bytes;
static uint32_t clips;
static uint8_t volume = 0x40;
static uint8_t amplifier;

/* play_state and valtrytofeed are hal/audio.c's own, and mean what they mean
 * there: 1 = playing, 2 = the bytecode has said end-of-file and what is left
 * in the FIFO is the last of it. rec_state is why play_start() can refuse. */
static int32_t play_state;
static int32_t valtrytofeed;
static int32_t rec_state;

/* The audio_halfduplex step changes play_start() and rec_start() to return a
 * status, and makes a sound stop a capture instead of being refused by it. It
 * defines NAB_AUDIO_STATUS in hal/audio.h so this file can follow either
 * shape -- which is the point: both behaviours have to be runnable here, or
 * the fix cannot be measured against the thing it fixes. */
#ifdef NAB_AUDIO_STATUS
#define AUDIO_STATUS   int32_t
#define AUDIO_OK       return 0
#define AUDIO_REFUSED  return 1
#else
#define AUDIO_STATUS   void
#define AUDIO_OK       return
#define AUDIO_REFUSED  return
#endif

/* Set by the harness from --audio-dir; one file per clip the rabbit plays. */
static char audio_dir[384];

void hw_audio_dir(const char *dir)
{
  snprintf(audio_dir, sizeof(audio_dir), "%s", dir ? dir : "");
}

uint32_t hw_audio_clips(void) { return clips; }
uint32_t hw_audio_bytes(void) { return captured_bytes; }

static void capture_open(void)
{
  char path[512];

  if(audio_dir[0] == 0 || capture != NULL) return;
  snprintf(path, sizeof(path), "%s/clip-%03u.bin", audio_dir, clips);
  capture = fopen(path, "wb");
  if(capture == NULL)
    sim_log("audio: cannot write %s", path);
  else
    sim_log("audio: capturing to %s", path);
}

static void capture_close(void)
{
  if(capture == NULL) return;
  fclose(capture);
  capture = NULL;
}

/* --- what vaudio.c calls ---------------------------------------------- */

void init_vlsi(void) {}
void patchwma(void) {}
void sw_reset_vlsi(void) {}
void clear_vlsi_fifo(void) {}

uint16_t vlsi_read_sci(uint8_t reg)
{
  (void)reg;
  return 0;
}

void vlsi_write_sci(uint8_t reg, uint16_t val)
{
  (void)reg; (void)val;
}

/**
 * @brief Accept a buffer of encoded audio, as the real decoder's FIFO would.
 *
 * Returning the full length matters: vaudio.c treats the return value as how
 * many bytes went in and re-offers the rest.
 */
uint32_t vlsi_feed_sdi(uint8_t *data, uint32_t len)
{
  if(len == 0) return 0;
  if(capture != NULL) fwrite(data, 1, len, capture);
  captured_bytes += len;
  return len;
}

uint8_t vlsi_fifo_ready(void)
{
  return 1;
}

void vlsi_ampli(uint8_t on)
{
  if(amplifier == on) return;
  amplifier = on;
  sim_log("audio: amplifier %s", on ? "on" : "off");
}

void set_vlsi_volume(uint8_t v)
{
  if(volume == v) return;
  volume = v;
  sim_log("audio: volume 0x%02x", v);
}

AUDIO_STATUS play_start(int32_t trytofeed)
{
  if(rec_state) {
#ifdef NAB_AUDIO_STATUS
    /* The speaker wins: the capture stops and the sound plays. */
    sim_log("audio: stopping the capture, the speaker wins");
    rec_stop();
#else
    /* Stock hal/audio.c returns here without a word when the microphone has
     * the decoder. The bytecode is told nothing, so a sound asked for while
     * the rabbit is recording is simply never played. */
    sim_log("audio: play_start refused, the microphone has the decoder");
    AUDIO_REFUSED;
#endif
  }
  clear_vlsi_fifo();
  patchwma();
  if(trytofeed <= 0) trytofeed = 1024;
  valtrytofeed = trytofeed;
  vlsi_ampli(1);
  play_state = 1;
  capture_open();
  sim_log("audio: the rabbit started playing something");
  AUDIO_OK;
}

void play_stop(void)
{
  if(!play_state) return;
  play_state = 0;
  vlsi_ampli(0);
  clear_vlsi_fifo();
  clips++;
  sim_log("audio: stopped after %u bytes (clip %u)", captured_bytes, clips);
  capture_close();
}

/**
 * @brief Drain the playback FIFO, as hal/audio.c does on the hardware.
 *
 * Byte for byte the firmware's own loop. Two things it does matter here and
 * are easy to lose: it calls back into the bytecode through audioPlayTryFeed
 * when the FIFO runs dry, which is how a streamed sound keeps arriving; and
 * it only ends the clip once the FIFO is empty *and* the bytecode has
 * declared end-of-file, so a sound is captured whole. nocb = 1 forbids the
 * callback, which is what the garbage collector passes.
 */
void play_check(int32_t nocb)
{
  int32_t empty = 0;
  int32_t nb = 0;

  if(!play_state || !vlsi_fifo_ready()) return;

  if(!nocb) nocb = 1 - audioPlayTryFeed(valtrytofeed);

  while(vlsi_fifo_ready() && !empty) {
    int32_t val = audioPlayFetchByte();
    if((val < 0) && (nb + nb >= valtrytofeed) && (!nocb)) {
      nocb = 1 - audioPlayTryFeed(valtrytofeed);
      audioPlayTryFeed(valtrytofeed);
      val = audioPlayFetchByte();
    }
    if(val >= 0) {
      uint8_t b = (uint8_t)val;
      nb++;
      vlsi_feed_sdi(&b, 1);
    } else {
      empty = 1;
      if(play_state == 2) {
        play_state = 0;
        vlsi_ampli(0);
        clips++;
        sim_log("audio: end of clip %u, %u bytes", clips, captured_bytes);
        capture_close();
      }
    }
  }
}

void play_eof(void)
{
  if(play_state == 1) play_state = 2;
}

uint32_t check_audio_file(void)
{
  return 0;
}

uint16_t check_decode_time(void)
{
  return 0;
}

/* --- the microphone --------------------------------------------------- */

void init_adpcm_encode(uint16_t sampling_frequency, uint16_t gain)
{
  sim_log("microphone: encoding at %u Hz, gain %u", sampling_frequency, gain);
}

void stop_adpcm_encode(void) {}

AUDIO_STATUS rec_start(uint16_t sampling_frequency, uint16_t gain)
{
  /* hal/audio.c refuses here while a sound plays, and upstream says nothing
   * about that either. Refusing is right -- a sound is what the server asked
   * for; what was missing was saying so. */
  if(play_state) {
    sim_log("microphone: rec_start refused, a sound has the decoder");
    AUDIO_REFUSED;
  }
  rec_state = 1;
  sim_log("microphone: recording at %u Hz, gain %u", sampling_frequency, gain);
  AUDIO_OK;
}

void rec_stop(void)
{
  rec_state = 0;
  sim_log("microphone: stopped");
}

/**
 * @brief Hand the firmware a block of captured audio.
 *
 * Silence, for now: there is no sound in a container. It returns nothing
 * rather than nothing-at-all, so the push-to-talk path can be driven later
 * by feeding a file in here.
 */
void rec_check(void) {}
