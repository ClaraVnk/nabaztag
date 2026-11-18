/* OPstrcmp's comparison (vinterp.c), on the host under ASan (review S-4,
 * 2026-11-18).
 *
 * The shipped expression compares the LONGER of the two lengths:
 *
 *     memcmp(VSTARTBIN(p), VSTARTBIN(q), (pl>ql)?pl:ql)
 *
 * so memcmp reads past the shorter string -- into the next VM object on a
 * rabbit, past a heap block here, where ASan says so -- and "ab" compares
 * equal to "abc" whenever the byte after "ab" happens to be 'c'.
 * vm_strcmp_bound compares the shorter length and lets the lengths decide
 * a tie, as strcmp does. The two expressions are copied here as they stand
 * in vinterp.c before and after the step; the VM macros are reduced to the
 * pointer and the length they yield.
 *
 *     make vm_strcmp              the fixed expression: clean, and ordered
 *     BEFORE=1 make vm_strcmp     the shipped one: heap-buffer-overflow READ
 *
 * This is the shipped line compiled for the host, not the ARM binary.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static int fails;

static void check(const char *what, int ok)
{
  printf("  %s  %s\n", ok ? "ok  " : "FAIL", what);
  fails += !ok;
}

/* One VM string: a heap block of exactly its length, no terminator -- a
   Metal string carries its size, not a NUL. */
static uint8_t *vmstr(const char *s, int32_t *len)
{
  *len = (int32_t)strlen(s);
  uint8_t *b = malloc((size_t)*len);
  memcpy(b, s, (size_t)*len);
  return b;
}

static int32_t opstrcmp(const char *a, const char *b)
{
  int32_t pl, ql, r;
  uint8_t *p = vmstr(a, &pl), *q = vmstr(b, &ql);
#ifdef BEFORE
  r = memcmp(p, q, (pl>ql)?pl:ql);                                   /* vinterp.c, shipped */
#else
  r = memcmp(p, q, (pl<ql)?pl:ql);  /* vm_strcmp_bound: the shorter */
  r = r ? r : pl-ql;
#endif
  free(p); free(q);
  return r;
}

int main(void)
{
  printf("== OPstrcmp over two heap strings of exact length\n");
  check("\"abc\" vs \"abc\": equal", opstrcmp("abc", "abc") == 0);
  check("\"abd\" vs \"abc\": greater", opstrcmp("abd", "abc") > 0);
  check("\"ab\" vs \"abc\": the shorter first (reads two bytes only)", opstrcmp("ab", "abc") < 0);
  check("\"abc\" vs \"ab\": the longer last", opstrcmp("abc", "ab") > 0);
  check("\"\" vs \"a\": empty first", opstrcmp("", "a") < 0);
  check("\"Hmac\" vs \"Hmac\" (kx_trailer's compare): equal", opstrcmp("Hmac", "Hmac") == 0);
  check("\"-violet-\" vs \"-vio\" (getfirmware's limit): not equal", opstrcmp("-violet-", "-vio") != 0);
  printf("%s\n", fails ? "FAILED" : "all passed");
  return fails ? 1 : 0;
}
