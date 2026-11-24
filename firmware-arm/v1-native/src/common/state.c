/*
 * state.c -- the loader's state record in the serial flash, two copies
 * (pages PG_STATE_A and PG_STATE_B) with a sequence number: the newer valid
 * one is the truth, a write goes to the other page, so a write cut by the
 * power (the brown-out that restarts Lazare, 2026-11-24) loses one copy and
 * never the record. Shared: the loader counts boots and installs, the
 * application confirms.
 */
#include "board.h"

static struct lstate a, b;

/* Valid: the magic and the 16 bytes summing to 0. */
static uint8_t valid(__data struct lstate *st)
{
  __data uint8_t *p = (__data uint8_t *)st;
  uint8_t i, s = 0;
  for(i = 0; i < sizeof(*st); i++) s += p[i];
  return s == 0 && p[0] == 'N' && p[1] == 'B' && p[2] == 'L' && p[3] == 'S';
}

/* The newest valid record, if any: its page, or 0; a and b hold both pages. */
static uint16_t newest(void)
{
  uint8_t va, vb;
  flash_read(PG_STATE_A, 0, (__data uint8_t *)&a, sizeof(a));
  flash_read(PG_STATE_B, 0, (__data uint8_t *)&b, sizeof(b));
  va = valid(&a); vb = valid(&b);
  if(va && (!vb || (int16_t)(a.seq - b.seq) > 0)) return PG_STATE_A;
  return vb ? PG_STATE_B : 0;
}

static void copy(__data uint8_t *d, __data const uint8_t *s)
{
  uint8_t i;
  for(i = 0; i < sizeof(struct lstate); i++) d[i] = s[i];
}

void state_read(__data struct lstate *st)
{
  uint16_t page = newest();
  __data uint8_t *p = (__data uint8_t *)st;
  if(page) { copy(p, (__data const uint8_t *)(page == PG_STATE_A ? &a : &b)); return; }
  { uint8_t i; for(i = 0; i < sizeof(*st); i++) p[i] = 0; }
  st->active = SLOT_NONE;
}

void state_write(__data struct lstate *st)
{
  uint16_t page = newest();
  __data uint8_t *p = (__data uint8_t *)st;
  uint8_t i, s = 0;
  p[0] = 'N'; p[1] = 'B'; p[2] = 'L'; p[3] = 'S';
  st->seq = page ? (page == PG_STATE_A ? a.seq : b.seq) + 1 : 1;
  st->csum = 0;
  for(i = 0; i < sizeof(*st); i++) s += p[i];
  st->csum = (uint8_t)(0 - s);
  flash_write(page == PG_STATE_A ? PG_STATE_B : PG_STATE_A, p, sizeof(*st));
}
