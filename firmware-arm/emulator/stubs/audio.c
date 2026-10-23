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
 *     and the feed loop in vaudio.c offers them again.
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

#include "sim.h"
#include "hw.h"

static FILE *capture;
static uint32_t captured_bytes;
static uint32_t clips;
static int playing;
static uint8_t volume = 0x40;
static uint8_t amplifier;

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
  if(!playing) {
    playing = 1;
    capture_open();
    sim_log("audio: the rabbit started playing something");
  }
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

void play_start(int32_t trytofeed)
{
  (void)trytofeed;
}

void play_stop(void)
{
  if(!playing) return;
  playing = 0;
  clips++;
  capture_close();
  sim_log("audio: stopped after %u bytes (clip %u)", captured_bytes, clips);
}

void play_check(int32_t nocb)
{
  (void)nocb;
}

void play_eof(void)
{
  if(!playing) return;
  playing = 0;
  clips++;
  capture_close();
  sim_log("audio: end of clip %u, %u bytes", clips, captured_bytes);
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

void rec_start(uint16_t sampling_frequency, uint16_t gain)
{
  sim_log("microphone: recording at %u Hz, gain %u", sampling_frequency, gain);
}

void rec_stop(void)
{
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
