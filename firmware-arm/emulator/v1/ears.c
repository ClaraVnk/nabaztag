/**
 * @file ears.c
 * @brief The two ear motors and their slot sensors.
 *
 * Each ear is a DC motor behind an L293D: RF0/RF1 for one, RF2/RF3 for the
 * other (the firmware drives those, not RD1 as Newbaztag's pin list says),
 * one direction per pin. A photo-sensor on the gear reads 17 slots per turn:
 * low ~190 ms, high ~240 ms, then a long high of ~1.4 s that marks where a
 * turn starts (Newbaztag doc/components.md, measured on a scope). The sensor
 * of ear 0 is RA4/T0CKI, so TMR0 counts it; ear 1's is RC0/T13CKI, TMR3.
 */
#include "board.h"

#define SLOTS     17
#define LOW_US    190000
#define HIGH_US   240000
#define GAP_US    1400000
#define TURN_US   (SLOTS * LOW_US + (SLOTS - 1) * HIGH_US + GAP_US)

void board_ear_pulse(int ear);

struct ear {
  int64_t phase;          /* microseconds into a turn, 0 = start of slot 0's low */
  int     dir;            /* +1, -1, 0 */
  int     level;
  int64_t moved_us;
};
static struct ear ears[2];

/* A hand on an ear: from at_ms, turn it n slots (negative: backwards) at
 * the motor's speed, with the motor off. */
static struct { int ear, n; uint64_t at_ms; int done; int64_t left_us; } hand[8];
static int hands;

void ears_hand(int ear, int n, uint64_t at_ms)
{
  if(hands < 8) { hand[hands].ear = ear; hand[hands].n = n; hand[hands].at_ms = at_ms; hands++; }
}
static uint64_t last_cycles;

/* Sensor level at a phase, and which slot it is in. */
static int level_at(int64_t ph, int *slot)
{
  int64_t t = ph;
  for(int s = 0; s < SLOTS; s++) {
    if(t < LOW_US) { *slot = s; return 0; }
    t -= LOW_US;
    int64_t high = s == SLOTS - 1 ? GAP_US : HIGH_US;
    if(t < high) { *slot = s; return 1; }
    t -= high;
  }
  *slot = 0;
  return 1;
}

static void set_sensor(int e, int level)
{
  if(e == 0) B.in[PA] = (uint8_t)((B.in[PA] & ~0x10) | (level << 4));
  else       B.in[PC] = (uint8_t)((B.in[PC] & ~0x01) | level);
}

void ears_init(int pos0, int pos1)
{
  int slot;
  ears[0].phase = (int64_t)pos0 * (LOW_US + HIGH_US) + LOW_US + 1000;
  ears[1].phase = (int64_t)pos1 * (LOW_US + HIGH_US) + LOW_US + 1000;
  for(int e = 0; e < 2; e++) {
    ears[e].level = level_at(ears[e].phase, &slot);
    set_sensor(e, ears[e].level);
  }
}

void ears_poll(void)
{
  uint8_t f = (uint8_t)(B.lat[PF] & ~B.tris[PF]);
  uint64_t now = B.pic.cycles;
  int64_t dt = (int64_t)((now - last_cycles) * 1000000 / CYCLES_HZ);
  if(dt <= 0) return;
  last_cycles = now;

  for(int e = 0; e < 2; e++) {
    int a = (f >> (2 * e)) & 1, b = (f >> (2 * e + 1)) & 1;
    int dir = a == b ? 0 : (b ? 1 : -1);
    for(int h = 0; h < hands; h++) {
      if(hand[h].ear != e || hand[h].done || board_ms() < hand[h].at_ms) continue;
      if(!hand[h].left_us) {
        hand[h].left_us = (int64_t)(hand[h].n < 0 ? -hand[h].n : hand[h].n) * (LOW_US + HIGH_US);
        board_log("ear %d: a hand turns it %d slots", e, hand[h].n);
      }
      if(!dir) {
        int sl, lv;
        int hd = hand[h].n < 0 ? -1 : 1;
        int64_t step = dt < hand[h].left_us ? dt : hand[h].left_us;
        ears[e].phase = (ears[e].phase + hd * step) % TURN_US;
        if(ears[e].phase < 0) ears[e].phase += TURN_US;
        hand[h].left_us -= step;
        lv = level_at(ears[e].phase, &sl);
        if(lv != ears[e].level) { if(lv) board_ear_pulse(e); ears[e].level = lv; set_sensor(e, lv); }
        if(hand[h].left_us <= 0) { hand[h].done = 1; board_log("ear %d: the hand lets go at slot %d", e, sl); }
      }
    }
    struct ear *x = &ears[e];
    int slot, lvl;

    if(dir != x->dir) {
      level_at(x->phase, &slot);
      if(dir) board_log("ear %d: motor %s (slot %d)", e, dir > 0 ? "forward" : "backward", slot);
      else    board_log("ear %d: motor stopped, slot %d, after %lld ms of turning", e, slot,
                        (long long)(x->moved_us / 1000));
      x->dir = dir;
      if(dir) x->moved_us = 0;
    }
    if(!dir) continue;
    x->moved_us += dt;
    x->phase = (x->phase + dir * dt) % TURN_US;
    if(x->phase < 0) x->phase += TURN_US;
    lvl = level_at(x->phase, &slot);
    if(lvl != x->level) {
      if(lvl) board_ear_pulse(e);           /* T0CKI/T13CKI count rising edges */
      x->level = lvl;
      set_sensor(e, lvl);
    }
  }
}
