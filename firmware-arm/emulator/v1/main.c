/**
 * @file main.c
 * @brief nabv1: Violet's 2005 firmware for the Nabaztag v1, on a build machine.
 *
 *   nabv1 [--hex nabaztag.hex] [--flash v1.flash] [--seconds N]
 *         [--mac 0019db00beef] [--warren host] [--port80 port] [--ssid name] [--trace-pins] [--trace-bus]
 *         [--trace-net] [--hot] [--hand EAR SLOTS AT_SECONDS]...
 *         [--press AT_SECONDS HOLD_SECONDS]...
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

static uint32_t hot[PIC_FLASH_SIZE / 2];

/* The head button, RB0, low while pressed. */
static struct { uint64_t at_ms, until_ms; int state; } press[8];
static int presses;

static void button_poll(void)
{
  uint64_t now = board_ms();
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
    else if(!strcmp(argv[i], "--warren") && i + 1 < argc) warren = argv[++i];
    else if(!strcmp(argv[i], "--ssid") && i + 1 < argc) snprintf(prism_ssid, sizeof(prism_ssid), "%s", argv[++i]);
    else if(!strcmp(argv[i], "--port80") && i + 1 < argc) gw_set_port80((uint16_t)atoi(argv[++i]));
    else if(!strcmp(argv[i], "--press") && i + 2 < argc && presses < 8) {
      press[presses].at_ms = (uint64_t)(atof(argv[i+1]) * 1000);
      press[presses].until_ms = press[presses].at_ms + (uint64_t)(atof(argv[i+2]) * 1000);
      presses++; i += 2;
    }
    else if(!strcmp(argv[i], "--mac") && i + 1 < argc) {
      unsigned v[6];
      if(sscanf(argv[++i], "%2x%2x%2x%2x%2x%2x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6)
        for(int k = 0; k < 6; k++) prism_mac[k] = (uint8_t)v[k];
    }
    else if(!strcmp(argv[i], "--hand") && i + 3 < argc) { ears_hand(atoi(argv[i+1]), atoi(argv[i+2]), (uint64_t)(atof(argv[i+3]) * 1000)); i += 3; }
    else if(!strcmp(argv[i], "--trace-net")) { net_trace = 1; gw_set_trace(1); }
    else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
  }

  board_reset();
  int n = pic18_load_hex(&B.pic, hex);
  if(n <= 0) { fprintf(stderr, "cannot read %s\n", hex); return 2; }
  board_log("firmware: %d bytes from %s, CONFIG4L %02X", n, hex, B.pic.config[6]);
  board_log("flash: %d bytes from %s", flash_load(flash), flash ? flash : "(blank)");

  ears_init(3, 3);
  gw_init(warren);
  uint64_t end = (uint64_t)(seconds * (double)CYCLES_HZ);
  while(B.pic.cycles < end && !B.pic.halted) {
    if(want_hot) hot[B.pic.pc / 2]++;
    board_step();
    if((B.pic.cycles & 0x3FF) < 2) { prism_tick(); prism_rx_pump(); gw_poll(); ears_poll(); button_poll(); }
  }
  if(B.pic.halted) board_log("halted at %05X: %s", B.pic.pc, B.pic.halt_reason);
  if(want_hot) report_hot();
  flash_save();
  gw_close();
  return 0;
}
