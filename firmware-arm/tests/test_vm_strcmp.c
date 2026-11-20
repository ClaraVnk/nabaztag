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
 *
 * A VM string is not bare, though: vmemAllocBin zeroes the last word of
 * its block, so the byte after the string is a NUL and the old read made
 * strings that differ only by trailing NULs compare EQUAL. Violet's runtime
 * relies on that: B64toS sizes its result from the base64 length and leaves
 * one or two NULs behind the last token of a DIGEST-MD5 challenge, and
 * `strcmp sAlgo "md5-sess"` must be 0. The first cut of vm_strcmp_bound
 * (Naboot 1.2.1.0) returned the length difference there and a rabbit on a
 * 22-character domain (challenge 95 bytes, one NUL) never answered its
 * challenge; the proof's 11-character domain (84 bytes, no padding) hid it.
 * The step now compares the shorter length, then the longer string's tail
 * within its own length: NULs are the end of the string, anything else
 * sorts it last. The three expressions are copied here as they stand in
 * vinterp.c; the VM macros are reduced to the pointer and the length they
 * yield, and the heap block here is of exactly the string's length, so a
 * read past it is caught rather than fed a convenient zero.
 *
 *     make vm_strcmp              the fixed expression: clean, and ordered
 *     BEFORE=1 make vm_strcmp     the shipped one: heap-buffer-overflow READ
 *     FIRSTCUT=1 make vm_strcmp   1.2.1.0's: clean, but "md5-sess\0" != "md5-sess"
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

/* One VM string: a heap block of exactly its length (NULs inside count). */
static uint8_t *vmstr(const char *s, int32_t len)
{
  uint8_t *b = malloc((size_t)len);
  memcpy(b, s, (size_t)len);
  return b;
}

static int32_t opstrcmp_n(const char *a, int32_t pl, const char *b, int32_t ql)
{
  int32_t r;
  uint8_t *p = vmstr(a, pl), *q = vmstr(b, ql);
#if defined(BEFORE)
  r = memcmp(p, q, (pl>ql)?pl:ql);                                   /* vinterp.c, shipped */
#elif defined(FIRSTCUT)
  r = memcmp(p, q, (pl<ql)?pl:ql);  /* vm_strcmp_bound, 1.2.1.0 */
  r = r ? r : pl-ql;
#else
  int32_t n=(pl<ql)?pl:ql;
  r=memcmp(p,q,n);  /* vm_strcmp_bound: the shorter */
  if (!r && pl!=ql)
  {
    uint8_t* t=((pl>ql)?p:q)+n;
    int32_t e=((pl>ql)?pl:ql)-n;
    while (e&&!*t) { t++; e--; }
    if (e) r=(pl>ql)?1:-1;
  }
#endif
  free(p); free(q);
  return r;
}

static int32_t opstrcmp(const char *a, const char *b)
{
  return opstrcmp_n(a, (int32_t)strlen(a), b, (int32_t)strlen(b));
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
  printf("== the trailing NULs B64toS leaves behind a challenge's last token\n");
  check("\"md5-sess\\0\" vs \"md5-sess\" (sAlgo, one NUL): equal", opstrcmp_n("md5-sess\0", 9, "md5-sess", 8) == 0);
  check("\"md5-sess\" vs \"md5-sess\\0\\0\" (two NULs): equal", opstrcmp_n("md5-sess", 8, "md5-sess\0\0", 10) == 0);
  check("\"auth\\0\" vs \"auth\" (sQop): equal", opstrcmp_n("auth\0", 5, "auth", 4) == 0);
  check("\"ab\\0c\" vs \"ab\": the longer last, a NUL is not its end", opstrcmp_n("ab\0c", 4, "ab", 2) > 0);
  check("\"ab\" vs \"ab\\0c\": the shorter first", opstrcmp_n("ab", 2, "ab\0c", 4) < 0);
  check("\"\\0\" vs \"\": equal", opstrcmp_n("\0", 1, "", 0) == 0);
  printf("%s\n", fails ? "FAILED" : "all passed");
  return fails ? 1 : 0;
}
