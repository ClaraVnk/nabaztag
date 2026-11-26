/**
 * @file main.c
 * @brief nabv1: Violet's 2005 firmware for the Nabaztag v1, on a build machine.
 *
 *   nabv1 [--hex nabaztag.hex] [--flash v1.flash] [--seconds N]
 *         [--mac 0019db00beef] [--warren host] [--port80 port] [--ssid name] [--trace-pins] [--trace-bus]
 *         [--trace-net] [--hot] [--hand EAR SLOTS AT_SECONDS]...
 *         [--press AT_SECONDS HOLD_SECONDS]... [--stall EAR]
 *         [--slider 0|1|2] [--slider-at SECONDS 0|1|2]...
 *         [--server-vanish AT] [--server-close AT] [--link-down AT SECONDS]
 *         [--dns-mute UNTIL_SECONDS] [--pic 6525|6627] [--pic-flash FILE] [--trace-flash]
 *         [--trace-oki]
 *
 * --pic 6525 is Lazare's PIC18LF6525 (48 KB, 8-byte write blocks; the
 * default is the 2.0b board's PIC18F6627, 96 KB, 64-byte rows); --pic-flash
 * keeps the PIC's own flash across runs (what the v1 loader writes), the
 * hex loaded first and the file laid over it when it exists, saved at exit:
 * a run cut with --seconds at any point is a power cut mid-copy (2026-11-24).
 *
 * The last four break the network under the firmware, to see what Violet's
 * code does when the server or the access point goes away (2026-11-24):
 * --server-vanish, the server dies without a FIN; --server-close, it closes
 * cleanly; --link-down, the access point is gone for SECONDS (0: for good);
 * --dns-mute, no DNS answer until that second (0: never).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "prism.h"
#include "../src/gw.h"

extern int net_trace;

void board_init(void);
void board_reset(void);
void board_step(void);
void ears_init(int pos0, int pos1);
void ears_poll(void);
void ears_hand(int ear, int n, uint64_t at_ms);
void ears_stall(int ear);

static uint32_t hot[PIC_FLASH_SIZE / 2];

/* The head button, RB0, low while pressed. */
static struct { uint64_t at_ms, until_ms; int state; } press[8];
static int presses;

static double vanish_at = -1, close_at = -1, dns_mute_until = -1;
static int dns_muted;
static const char *pic_flash_path;

/* A reset that is not a power-on: the watchdog's or the RESET instruction's.
 * RAM keeps its content (the firmware may read why it restarted), the ports
 * are inputs again, the PC is 0. */
static void soft_reset(void)
{
  B.pic.pc = 0; B.pic.sp = 0; B.pic.wdt_clear = B.pic.cycles; B.pic.reset_req = 0;
  for(uint16_t a = SFR_TRISA; a <= SFR_TRISG; a++) B.pic.ram[a] = 0xFF;
  for(int p = 0; p < NPORTS; p++) B.tris[p] = 0xFF;
}

static void pic_flash_load(void)
{
  FILE *f;
  if(!pic_flash_path || !(f = fopen(pic_flash_path, "rb"))) return;
  size_t n = fread(B.pic.flash, 1, PIC_FLASH_SIZE, f);
  fclose(f);
  board_log("pic flash: %zu bytes from %s laid over the hex", n, pic_flash_path);
}

static void pic_flash_save(void)
{
  FILE *f;
  if(!pic_flash_path || !(f = fopen(pic_flash_path, "wb"))) return;
  fwrite(B.pic.flash, 1, PIC_FLASH_SIZE, f);
  fclose(f);
}

/* The sound switch on AN0: a three-position slider (Clara, 2026-11-26),
 * left silent, middle normal, right loud. Its divider is fed by the sound
 * side: AN0 reads 0 unless the OKI is powered (register 31, spi.c), as
 * Lazare showed (00 in every position at rest) and Violet's 1.4b implies
 * (it reads the switch only after the chip's power-up). --slider POS sets
 * the position from the start (1 by default), --slider-at SECONDS POS moves
 * it; the readings are Violet's three bands (ADRESH < 0x40, < 0xC0, else). */
static const uint16_t slider_adc[3] = { 0x10 << 2, 0x80 << 2, 0xF0 << 2 };
static int slider = 1, slider_moves;
static struct { uint64_t at; int pos; } slider_move[4];

static void sound_poll(void)
{
  uint64_t now = board_ms();
  for(int i = 0; i < slider_moves; i++)
    if(slider_move[i].at && now >= slider_move[i].at) {
      slider = slider_move[i].pos;
      board_log("sound switch: moved to %d", slider);
      slider_move[i].at = 0;
    }
  uint16_t want = B.oki_power ? slider_adc[slider] : 0;
  if(B.adc[0] != want) B.adc[0] = want;
}

static void button_poll(void)
{
  uint64_t now = board_ms();
  sound_poll();
  for(int i = 0; i < presses; i++) {
    if(press[i].state == 0 && now >= press[i].at_ms) {
      press[i].state = 1;
      B.in[PB] &= (uint8_t)~0x01;
      board_log("button: pressed (held %llu ms)", (unsigned long long)(press[i].until_ms - press[i].at_ms));
    } else if(press[i].state == 1 && now >= press[i].until_ms) {
      press[i].state = 2;
      B.in[PB] |= 0x01;
      board_log("button: released");
    }
  }
}

static void report_hot(void)
{
  uint32_t best[12] = { 0 }, at[12] = { 0 };
  for(uint32_t i = 0; i < PIC_FLASH_SIZE / 2; i++) {
    for(int k = 0; k < 12; k++) {
      if(hot[i] > best[k]) {
        memmove(best + k + 1, best + k, (11 - k) * sizeof(best[0]));
        memmove(at + k + 1, at + k, (11 - k) * sizeof(at[0]));
        best[k] = hot[i]; at[k] = i * 2;
        break;
      }
    }
  }
  board_log("where the time went:");
  for(int k = 0; k < 12 && best[k]; k++) printf("    %05X  %u\n", at[k], best[k]);
}

/* The gateway saw the rabbit close a connection (src/gw.c): where its PIC
 * was, and who called it, to find why a v1 with no program stopped reading
 * Violet's 33 KB answer after 506 bytes (2026-11-23). */
void sim_log(const char *fmt, ...);
void gw_rabbit_closed_hook(unsigned long down)
{
  char st[160]; int n = 0;
  for(int i = B.pic.sp; i >= 1 && n < (int)sizeof(st) - 8; i--)
    n += snprintf(st + n, sizeof(st) - n, " %05X", (unsigned)B.pic.stack[i]);
  sim_log("v1: %s after %lu bytes down at pc %05X, stack%s",
          (down & 0x80000000ul) ? "the warren closed" : "closed", down & 0x7ffffffful,
          (unsigned)B.pic.pc, n ? st : " empty");
}

int main(int argc, char **argv)
{
  const char *hex = "nabaztag.hex", *flash = NULL, *warren = NULL;
  double seconds = 10;
  int want_hot = 0;

  board_init();
  for(int i = 1; i < argc; i++) {
    if(!strcmp(argv[i], "--hex") && i + 1 < argc) hex = argv[++i];
    else if(!strcmp(argv[i], "--flash") && i + 1 < argc) flash = argv[++i];
    else if(!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
    else if(!strcmp(argv[i], "--trace-pins")) B.trace_pins = 1;
    else if(!strcmp(argv[i], "--trace-bus")) B.trace_bus = 1;
    else if(!strcmp(argv[i], "--hot")) want_hot = 1;
    else if(!strcmp(argv[i], "--flash-chip") && i + 1 < argc) spi_flash_chip(!strcmp(argv[++i], "081"));
    else if(!strcmp(argv[i], "--warren") && i + 1 < argc) warren = argv[++i];
    else if(!strcmp(argv[i], "--ssid") && i + 1 < argc) snprintf(prism_ssid, sizeof(prism_ssid), "%s", argv[++i]);
    else if(!strcmp(argv[i], "--port80") && i + 1 < argc) gw_set_port80((uint16_t)atoi(argv[++i]));
    else if(!strcmp(argv[i], "--bcast-deaf")) gw_set_bcast_deaf(1);
    else if(!strcmp(argv[i], "--press") && i + 2 < argc && presses < 8) {
      press[presses].at_ms = (uint64_t)(atof(argv[i+1]) * 1000);
      press[presses].until_ms = press[presses].at_ms + (uint64_t)(atof(argv[i+2]) * 1000);
      presses++; i += 2;
    }
    else if(!strcmp(argv[i], "--slider") && i + 1 < argc) slider = atoi(argv[++i]) % 3;
    else if(!strcmp(argv[i], "--slider-at") && i + 2 < argc && slider_moves < 4) {
      slider_move[slider_moves].at = (uint64_t)(atof(argv[i+1]) * 1000);
      slider_move[slider_moves++].pos = atoi(argv[i+2]) % 3;
      i += 2;
    }
    else if(!strcmp(argv[i], "--stall") && i + 1 < argc) ears_stall(atoi(argv[++i]));
    else if(!strcmp(argv[i], "--mac") && i + 1 < argc) {
      unsigned v[6];
      if(sscanf(argv[++i], "%2x%2x%2x%2x%2x%2x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6)
        for(int k = 0; k < 6; k++) prism_mac[k] = (uint8_t)v[k];
    }
    else if(!strcmp(argv[i], "--hand") && i + 3 < argc) { ears_hand(atoi(argv[i+1]), atoi(argv[i+2]), (uint64_t)(atof(argv[i+3]) * 1000)); i += 3; }
    else if(!strcmp(argv[i], "--trace-net")) { net_trace = 1; gw_set_trace(1); }
    else if(!strcmp(argv[i], "--trace-flash")) B.trace_flash = 1;
    else if(!strcmp(argv[i], "--trace-oki")) B.trace_oki = 1;
    else if(!strcmp(argv[i], "--pic-flash") && i + 1 < argc) pic_flash_path = argv[++i];
    else if(!strcmp(argv[i], "--pic") && i + 1 < argc) {
      const char *d = argv[++i];
      if(!strcmp(d, "6525")) { B.pic.write_block = 8; B.pic.flash_end = 0xC000; }
      else if(!strcmp(d, "6627")) { B.pic.write_block = 64; B.pic.flash_end = PIC_FLASH_SIZE; }
      else { fprintf(stderr, "--pic takes 6525 or 6627\n"); return 2; }
    }
    else if(!strcmp(argv[i], "--server-vanish") && i + 1 < argc) vanish_at = atof(argv[++i]);
    else if(!strcmp(argv[i], "--server-close") && i + 1 < argc) close_at = atof(argv[++i]);
    else if(!strcmp(argv[i], "--link-down") && i + 2 < argc) { prism_link_down((uint64_t)(atof(argv[i+1]) * 1000), (uint64_t)atof(argv[i+2])); i += 2; }
    else if(!strcmp(argv[i], "--dns-mute") && i + 1 < argc) { dns_mute_until = atof(argv[++i]); dns_muted = 1; gw_set_dns_mute(1); }
    else if(!strcmp(argv[i], "--vm-audit") && i + 2 < argc) {
      B.pic.audit_on = 1;
      B.pic.audit_lo = (uint16_t)strtol(argv[i+1], NULL, 0);
      B.pic.audit_hi = (uint16_t)strtol(argv[i+2], NULL, 0);
      i += 2;
    }
    else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
  }

  board_reset();
  int n = pic18_load_hex(&B.pic, hex);
  if(n <= 0) { fprintf(stderr, "cannot read %s\n", hex); return 2; }
  board_log("firmware: %d bytes from %s, CONFIG4L %02X", n, hex, B.pic.config[6]);
  memcpy(B.eeprom, B.pic.eeprom, sizeof(B.eeprom));
  pic_flash_load();
  board_log("flash: %d bytes from %s", flash_load(flash), flash ? flash : "(blank)");

  ears_init(3, 3);
  gw_init(warren);
  uint64_t end = (uint64_t)(seconds * (double)CYCLES_HZ);
  /* The watchdog (DS39612: a 4 ms nominal period times the CONFIG2H
   * postscaler). The 1.4b firmware of Clara's rabbit has it on in its
   * configuration (CONFIG2H 0F: 1:128, ~512 ms), the 2.0b dump off (1E):
   * Lazare restarted at every ear order (2026-11-24), the ears turn for
   * seconds. Without it the emulator could not show that. */
  int wdt_cfg = B.pic.config[3] & 1;
  uint64_t wdt_period = (uint64_t)(CYCLES_HZ / 1000) * 4u << ((B.pic.config[3] >> 1) & 0xF);
  unsigned wdt_resets = 0;
  while(B.pic.cycles < end && !B.pic.halted) {
    if(want_hot) hot[B.pic.pc / 2]++;
    board_step();
    if((wdt_cfg || (B.pic.ram[0xFD1] & 1)) && B.pic.cycles - B.pic.wdt_clear > wdt_period) {
      board_log("watchdog: no CLRWDT for %llu ms, at pc %05X: reset #%u",
                (unsigned long long)(wdt_period * 1000 / CYCLES_HZ), B.pic.pc, ++wdt_resets);
      soft_reset();
    }
    if(B.pic.reset_req) {
      board_log("reset: RESET instruction at pc %05X: reset #%u", B.pic.pc - 2, ++B.pic.resets);
      soft_reset();
    }
    if((B.pic.cycles & 0x3FF) < 2) {
      prism_tick(); prism_rx_pump(); gw_poll(); ears_poll(); button_poll();
      double now = (double)B.pic.cycles / CYCLES_HZ;
      if(vanish_at >= 0 && now >= vanish_at) { vanish_at = -1; gw_server_vanish(); }
      if(close_at >= 0 && now >= close_at) { close_at = -1; gw_server_close(0); }
      if(dns_muted && dns_mute_until > 0 && now >= dns_mute_until) { dns_muted = 0; gw_set_dns_mute(0); board_log("gw: DNS answers again"); }
    }
  }
  if(B.pic.halted) board_log("halted at %05X: %s", B.pic.pc, B.pic.halt_reason);
  if(B.pic.audit_on)
    board_log("vm-audit: %u indirect stores from the VM band [%05X,%05X]; destinations %03X..%03X; outside the arena 0xA70-0xCFF: %u",
              B.pic.audit_stores, B.pic.audit_lo, B.pic.audit_hi,
              B.pic.audit_stores ? B.pic.audit_min : 0, B.pic.audit_stores ? B.pic.audit_max : 0,
              B.pic.audit_escapes);
  if(want_hot) report_hot();
  if(B.flash_erases || B.flash_writes)
    board_log("pic flash: %u rows erased, %u blocks of %u written", B.flash_erases, B.flash_writes, B.pic.write_block);
  pic_flash_save();
  flash_save();
  gw_close();
  return 0;
}
