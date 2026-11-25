/*
 * ears.c -- the two ear motors and their hole sensors.
 *
 * A DC motor behind an L293D each, one direction per pin; a photo-sensor on
 * the gear gives 17 holes a turn (low ~190 ms, high ~240 ms, a 1.4 s gap
 * at the start of the turn: Newbaztag, measured on a scope). TMR0 counts
 * ear 0's rising edges on T0CKI, TMR3 ear 1's on T13CKI, as Violet's
 * firmware does. Non-blocking: ear_move() starts, ears_poll() stops when the
 * count is reached, or after a timeout -- Lazare's right ear draws current
 * and never turns (2026-11-24): a stalled motor is stopped and said so,
 * not waited for.
 */
#include "board.h"

#define HOLE_TIMEOUT_TICKS  (3 * TICKS_PER_S)   /* one hole takes ~430 ms */

static struct {
  int8_t   left;        /* holes still to go, signed by direction */
  uint16_t last_count;
  uint16_t started;     /* ticks at the last hole */
  uint16_t start_tick;
} ear[2];

static uint16_t count(uint8_t e)
{
  uint8_t lo, hi;
  if(e == 0) { lo = TMR0L; hi = TMR0H; }      /* reading L latches H */
  else       { lo = TMR3L; hi = TMR3H; }
  return ((uint16_t)hi << 8) | lo;
}

static void drive(uint8_t e, int8_t dir)
{
  uint8_t a = dir > 0 ? 0 : (dir < 0 ? 1 : 0);
  uint8_t b = dir > 0 ? 1 : 0;
  if(e == 0) { EAR0_A = a; EAR0_B = b; }
  else       { EAR1_A = a; EAR1_B = b; }
}

void ears_init(void)
{
  TRISAbits.TRISA4 = 1;
  TRISCbits.TRISC0 = 1;
  T0CON = 0xA8;         /* on, 16 bits, T0CKI rising, no prescaler */
  T3CON = 0x87;         /* RD16, T13CKI, no prescaler, on */
  TMR0H = 0; TMR0L = 0; TMR3H = 0; TMR3L = 0;
  drive(0, 0); drive(1, 0);
  TRISF &= 0xF0;
}

uint8_t ears_busy(void) { return ear[0].left || ear[1].left; }

void ear_move(uint8_t e, int8_t holes)
{
  if(!holes) return;
  ear[e].left = holes;
  ear[e].last_count = count(e);
  ear[e].started = ear[e].start_tick = ticks();
  log_s("ear "); log_dec(e); log_line(holes > 0 ? " fwd" : " back");
  drive(e, holes);
}

void ears_poll(void)
{
  uint8_t e;
  for(e = 0; e < 2; e++) {
    uint16_t c;
    if(!ear[e].left) continue;
    c = count(e);
    if(c != ear[e].last_count) {
      ear[e].last_count = c;
      ear[e].started = ticks();
      if(ear[e].left > 0) ear[e].left--; else ear[e].left++;
      if(ear[e].left) continue;
      drive(e, 0);
      log_s("ear "); log_dec(e); log_s(" done in ");
      log_dec((uint16_t)(ticks() - ear[e].start_tick) * 11); log_line(" ms");
    } else if((uint16_t)(ticks() - ear[e].started) > HOLE_TIMEOUT_TICKS) {
      ear[e].left = 0;
      drive(e, 0);
      log_s("ear "); log_dec(e); log_line(" stalled, stopped");
    }
  }
}
