/**
 * @file test_uart_fmt.c
 * @brief The console's two formatters, against the sprintf they replace.
 *
 * apply-mods.py's uart_no_sprintf step rewrites putint_uart, puthx_uart, dump
 * and dumpbin in src/hal/uart.c so that newlib's sprintf -- and the printf
 * core and nano malloc it drags in, 4.3 KB of a lean-wpa3 image -- is no
 * longer linked. The rabbit's UART must still receive the same bytes, and
 * the emulator cannot say so: it compiles its own stubs/uart.c. So this
 * compares the formatters' output with sprintf's for the four formats, over
 * every value that has an edge in it and a sweep of ordinary ones.
 *
 * The code under test is the C the step splices in, written out by the
 * Makefile from apply-mods.py itself (uart_fmt.inc), so what is tested is
 * what lands in the tree.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static char out[64];
static unsigned outn;

static void putch_uart(uint8_t c)
{
  if (outn < sizeof out - 1) out[outn++] = (char)c;
  out[outn] = 0;
}

#include "uart_fmt.inc"

static int failures;
static unsigned cases;

static void reset(void) { outn = 0; out[0] = 0; }

static void check_dec(int32_t v)
{
  char want[32];
  snprintf(want, sizeof want, "%ld", (long)v);
  reset(); put_dec_uart(v);
  cases++;
  if (strcmp(out, want) != 0) {
    printf("  FAIL \"%%ld\" of %ld: got \"%s\", sprintf says \"%s\"\n", (long)v, out, want);
    failures++;
  }
}

static void check_hex(uint32_t v, unsigned min_digits, const char *fmt)
{
  char want[32];
  snprintf(want, sizeof want, fmt, (unsigned long)v);
  reset(); put_hex_uart(v, min_digits);
  cases++;
  if (strcmp(out, want) != 0) {
    printf("  FAIL \"%s\" of 0x%lx: got \"%s\", sprintf says \"%s\"\n", fmt, (unsigned long)v, out, want);
    failures++;
  }
}

int main(void)
{
  static const int32_t dec_edges[] = {
    0, 1, -1, 9, 10, -10, 99, 100, 2147483647, (-2147483647 - 1), -2147483647,
    1000000000, -1000000000, 65535, -65536, 123456789,
  };
  static const uint32_t hex_edges[] = {
    0, 1, 0xf, 0x10, 0xff, 0x100, 0xfff, 0x1000, 0xffff, 0x10000,
    0xdeadbeef, 0xffffffffu, 0x80000000u, 0x0801a0ed,
  };
  unsigned i;
  int32_t v;

  printf("== \"%%ld\" (putint_uart)\n");
  for (i = 0; i < sizeof dec_edges / sizeof dec_edges[0]; i++) check_dec(dec_edges[i]);
  for (v = -70000; v <= 70000; v += 7) check_dec(v);
  for (v = 1; v < 700000000; v = v * 3 + 1) { check_dec(v); check_dec(-v); }

  printf("== \"0x%%lx\" (puthx_uart), \"%%04lx\" and \"%%02x\" (dump, dumpbin)\n");
  for (i = 0; i < sizeof hex_edges / sizeof hex_edges[0]; i++) {
    check_hex(hex_edges[i], 1, "%lx");       /* puthx_uart prints "0x" itself */
    check_hex(hex_edges[i], 4, "%04lx");
  }
  for (i = 0; i < 256; i++) check_hex(i, 2, "%02lx");
  for (i = 0; i < 70000; i += 3) check_hex(i, 4, "%04lx");
  { uint32_t u; for (u = 1; u != 0; u <<= 1) { check_hex(u, 1, "%lx"); check_hex(u - 1, 4, "%04lx"); } }

  printf("%u comparisons, %d failure(s)\n", cases, failures);
  return failures ? 1 : 0;
}
