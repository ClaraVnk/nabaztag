/**
 * @file hcdmem.c
 * @brief The USB host controller's allocator, as plain malloc.
 *
 * On the hardware this hands out two fixed arenas (COMRAM for small control
 * structures, EXTRAM for frames) and returning NULL is a real possibility the
 * firmware checks for everywhere. Here the host allocator stands in, with the
 * allocation accounted so a leak in the receive path still shows up: the run
 * summary prints what was never freed.
 */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>

static uint32_t hcd_live;
static uint32_t hcd_total;

int8_t hcd_malloc_init(int32_t a, uint32_t b, uint32_t c, uint8_t d)
{
  (void)a; (void)b; (void)c; (void)d;
  return 0;
}

void *hcd_malloc(uint32_t size, int8_t bank, int32_t tag)
{
  void *p;
  (void)bank; (void)tag;
  p = calloc(1, size);
  if(p != NULL) { hcd_live++; hcd_total++; }
  return p;
}

int8_t hcd_free(void *address)
{
  if(address == NULL) return -1;
  free(address);
  hcd_live--;
  return 0;
}

int8_t hcd_malloc_check(void *address)
{
  (void)address;
  return 0;
}

void hcd_malloc_info(int32_t bank)
{
  (void)bank;
}

int32_t hcd_malloc_rest(uint8_t bank)
{
  (void)bank;
  return 0x7fff;   /* never out of memory on a host */
}

uint32_t hcd_malloc_live(void)   { return hcd_live; }
uint32_t hcd_malloc_total(void)  { return hcd_total; }
