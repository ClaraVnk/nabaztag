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
  uint8_t  pos;         /* holes from the top, 0-16, counted forward from the start */
  uint8_t  want;        /* where frame 11 asked it to be, 0xFF: nowhere */
  uint16_t last_count;
  uint16_t started;     /* ticks at the last hole */
  uint16_t start_tick;
} ear[2];
static uint8_t dead, moving;       /* bit per ear, as EE_EAR_DEAD and EE_EAR_MOVING */
static uint8_t  by_hand;           /* an ear turned with no motor on: report it */
static uint16_t hand_at;

static void mark_moving(uint8_t m)
{
  if(m != moving) { moving = m; ee_put(EE_EAR_MOVING, m); }
}

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
  ear[0].want = ear[1].want = 0xFF;
  /* An ear being driven when the rabbit restarted is the one that
   * restarted it: never again (EE_EAR_DEAD). */
  dead = ee_get(EE_EAR_DEAD);
  if(dead == 0xFF) dead = 0;
  moving = ee_get(EE_EAR_MOVING);
  if(moving != 0xFF && moving) {
    dead |= moving;
    ee_put(EE_EAR_DEAD, dead);
    log_s("ears: restarted while driving "); log_dec(moving); log_line(", that ear is left alone");
  }
  if(moving != 0) ee_put(EE_EAR_MOVING, 0);
}

uint8_t ears_busy(void) { return ear[0].left || ear[1].left; }

void ear_move(uint8_t e, int8_t holes)
{
  if(!holes) return;
  if(dead & (1 << e)) { log_s("ear "); log_dec(e); log_line(" left alone (it restarted the rabbit)"); return; }
  mark_moving(moving | (uint8_t)(1 << e));
  ear[e].left = holes;
  ear[e].last_count = count(e);
  ear[e].started = ear[e].start_tick = ticks();
  log_s("ear "); log_dec(e); log_line(holes > 0 ? " fwd" : " back");
  drive(e, holes);
}

/* Frame 11 (DESIGN.md section 8): each ear to a place, 0-16 holes from the
 * top, forward only, one ear after the other as Violet's program moves
 * them. The places are counted from the start, taken as the top: the gap
 * at the start of a turn is not looked for yet (a later step). The Terrier
 * sends the place it keeps with every answer; an ear already there does
 * not move. */
void ears_goto(uint8_t left, uint8_t right)
{
  if(left < EAR_HOLES) ear[EAR_LEFT].want = left;
  if(right < EAR_HOLES) ear[EAR_RIGHT].want = right;
}

static void next_move(void)
{
  uint8_t e;
  if(ears_busy()) return;
  for(e = 0; e < 2; e++) {
    if(ear[e].want == 0xFF) continue;
    if(ear[e].want != ear[e].pos && !(dead & (1 << e))) {
      ear_move(e, (int8_t)((ear[e].want + EAR_HOLES - ear[e].pos) % EAR_HOLES));
      ear[e].want = 0xFF;
      return;                       /* the other one when this one is done */
    }
    ear[e].want = 0xFF;
  }
}

void ears_poll(void)
{
  uint8_t e;
  next_move();
  /* Turned by hand: holes counted with no motor on, forward as far as the
   * sensor can tell; once both have been still for a second, sd= says
   * where they are, as Violet's v1 did (the Terrier's lovers' ears). */
  for(e = 0; e < 2; e++) {
    uint16_t c;
    if(ear[e].left) continue;
    c = count(e);
    if(c != ear[e].last_count) {
      ear[e].last_count = c;
      if(++ear[e].pos == EAR_HOLES) ear[e].pos = 0;
      by_hand = 1; hand_at = ticks();
    }
  }
  if(by_hand && !ears_busy() && (uint16_t)(ticks() - hand_at) > TICKS_PER_S) {
    by_hand = 0;
    input_ears(ear[EAR_RIGHT].pos, ear[EAR_LEFT].pos);
  }
  for(e = 0; e < 2; e++) {
    uint16_t c;
    if(!ear[e].left) continue;
    c = count(e);
    if(c != ear[e].last_count) {
      ear[e].last_count = c;
      ear[e].started = ticks();
      if(ear[e].left > 0) { ear[e].left--; if(++ear[e].pos == EAR_HOLES) ear[e].pos = 0; }
      else { ear[e].left++; ear[e].pos = ear[e].pos ? ear[e].pos - 1 : EAR_HOLES - 1; }
      if(ear[e].left) continue;
      drive(e, 0);
      mark_moving(moving & (uint8_t)~(1 << e));
      log_s("ear "); log_dec(e); log_s(" done at "); log_dec(ear[e].pos); log_s(" in ");
      log_dec((uint16_t)(ticks() - ear[e].start_tick) * 11); log_line(" ms");
    } else if((uint16_t)(ticks() - ear[e].started) > HOLE_TIMEOUT_TICKS) {
      ear[e].left = 0;
      drive(e, 0);
      mark_moving(moving & (uint8_t)~(1 << e));
      log_s("ear "); log_dec(e); log_line(" stalled, stopped");
    }
  }
}
