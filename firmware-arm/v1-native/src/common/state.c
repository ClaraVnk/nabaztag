/*
 * state.c -- the loader's state record in the serial flash, two copies
 * (pages PG_STATE_A and PG_STATE_B) with a sequence number: the newer valid
 * one is the truth, a write goes to the other page, so a write cut by the
 * power (the brown-out that restarts Lazare, 2026-11-24) loses one copy and
 * never the record. Shared: the loader counts boots and installs, the
 * application confirms. Both copies in one array at constant addresses and
 * one pass per copy for the sum and the magic: SDCC's pic16 code for the
 * first version was 1.2 KB, every p[i] a fresh pointer computation
 * (2026-11-25).
 */
#include "board.h"

#define REC     sizeof(struct lstate)   /* 16 */
static uint8_t rec[2 * REC];            /* page PG_STATE_A at 0, PG_STATE_B at 16 */
#define SEQ_A   (*(__data uint16_t *)(rec + 4))
#define SEQ_B   (*(__data uint16_t *)(rec + REC + 4))
static uint8_t magic_ok;

/* The sum of the 16 bytes (0 when valid); magic_ok says whether they start with NBLS. */
static uint8_t csum(__data const uint8_t *p)
{
  const __code char *g = "NBLS";
  uint8_t i, s = 0, m = 0;
  for(i = 0; i < REC; i++) { s += *p; if(i < 4) m |= *p ^ (uint8_t)*g++; p++; }
  magic_ok = m == 0;
  return s;
}

/* The newest valid copy: 0 (A), 1 (B) or 2 for none. */
static uint8_t newest(void)
{
  uint8_t va, vb;
  flash_read(PG_STATE_A, 0, rec, REC);
  flash_read(PG_STATE_B, 0, rec + REC, REC);
  va = csum(rec) == 0 && magic_ok;
  vb = csum(rec + REC) == 0 && magic_ok;
  if(va && (!vb || (int16_t)(SEQ_A - SEQ_B) > 0)) return 0;
  return vb ? 1 : 2;
}

void state_read(__data struct lstate *st)
{
  uint8_t k = newest(), i;
  __data uint8_t *p = (__data uint8_t *)st;
  __data const uint8_t *s = k ? rec + REC : rec;
  if(k == 2) { for(i = 0; i < REC; i++) p[i] = 0; st->active = SLOT_NONE; return; }
  for(i = 0; i < REC; i++) p[i] = s[i];
}

/* The record is completed in rec's first copy (constant addresses), then
 * written and copied back, so that the caller's record carries seq and csum. */
void state_write(__data struct lstate *st)
{
  uint8_t k = newest(), i;
  __data uint8_t *p = (__data uint8_t *)st;
  uint16_t seq = (k == 0 ? SEQ_A : k == 1 ? SEQ_B : 0) + 1;
  for(i = 0; i < REC; i++) rec[i] = p[i];
  rec[0] = 'N'; rec[1] = 'B'; rec[2] = 'L'; rec[3] = 'S';
  SEQ_A = seq;
  rec[REC - 1] = 0;
  rec[REC - 1] = (uint8_t)(0 - csum(rec));
  flash_write(k == 0 ? PG_STATE_B : PG_STATE_A, rec, REC);
  for(i = 0; i < REC; i++) p[i] = rec[i];
}
