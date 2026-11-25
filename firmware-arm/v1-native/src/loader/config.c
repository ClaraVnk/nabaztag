/*
 * config.c -- the PIC's configuration words, linked into whatever image is
 * programmed at 0 (the loader; the SHA test image, shatest.c). Lazare's
 * configuration, byte for byte (v1-icsp/dumps/violet-v1-1.4b.hex at
 * 0x300000): HS oscillator, power-up timer, brown-out reset on (2.7 V),
 * watchdog on with the 1:128 postscaler (~512 ms), MCLR on, LVP off, no
 * code protection, no write protection. The 1.4b firmware never let the
 * watchdog expire and neither does ours: every loop clears it.
 */
#include "board.h"

__code __at(0x300000) uint8_t config_bits[14] = {
  0x00, 0x22,   /* CONFIG1L (unimplemented), CONFIG1H: OSCS off, HS */
  0x0A, 0x0F,   /* CONFIG2L: PWRT on, BOR on, BORV 2.7 V; CONFIG2H: WDT on, 1:128 */
  0x83, 0x83,   /* CONFIG3L (unimplemented), CONFIG3H: MCLR on, CCP2 on RC1 */
  0x80, 0x00,   /* CONFIG4L: DEBUG off, LVP off, STVR off; CONFIG4H */
  0x0F, 0xC0,   /* CONFIG5: no code protection */
  0x0F, 0xE0,   /* CONFIG6: no write protection (WRTB once the loader is proven on the rabbit) */
  0x0F, 0x40    /* CONFIG7: no table-read protection */
};
