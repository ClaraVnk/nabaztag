/**
 * @file uart.c
 * @brief The rabbit's serial console, on stdout.
 *
 * nabgcc ships a host stub for this under testvm/stubs/hal, but its
 * putbin_uart() prints "0x%2X" per byte — and the bytecode uses putbin_uart
 * for most of what it says, because a Metal string is a length and a pointer
 * rather than a NUL-terminated one. The rabbit's own boot log therefore came
 * out as `0x300x300x2E0x310x390x64` where it meant `00.19.db`. The hardware
 * UART sends the bytes; so does this.
 *
 * Everything the rabbit prints goes to stdout, and everything the emulator
 * says about it goes to stderr, so the two can be separated:
 *
 *   ./rabbit-vm scenarios/boot.txt 2>/dev/null     the rabbit's console alone
 *   ./rabbit-vm scenarios/boot.txt >/dev/null      the emulator's view alone
 */
#include <stdint.h>
#include <stdio.h>

#include "common.h"
#include "hal/uart.h"

void putch_uart(uint8_t c)
{
  putchar(c);
}

void putst_uart(uint8_t *str)
{
  fputs((const char *)str, stdout);
}

void putbin_uart(uint8_t *str, uint32_t len)
{
  fwrite(str, 1, len, stdout);
}

void putint_uart(int32_t v)
{
  printf("%d", (int)v);
}

void puthx_uart(uint32_t v)
{
  printf("0x%x", (unsigned)v);
}

void init_uart(void) {}
void uart0_interrupt(void) {}

uint64_t xmodem_recv(uint8_t *addr_mem)
{
  (void)addr_mem;
  return 0;     /* no serial bootloader here */
}

void xmodem_send(uint8_t *addr_mem, uint32_t nb_bytes_to_send)
{
  (void)addr_mem; (void)nb_bytes_to_send;
}

int16_t calcrc(uint8_t *ptr, uint16_t count)
{
  int16_t crc = 0;
  uint8_t i;

  while(--count) {
    crc = (int16_t)(crc ^ (int16_t)*ptr++ << 8);
    for(i = 0; i < 8; ++i)
      crc = (int16_t)((crc & 0x8000) ? (crc << 1 ^ 0x1021) : (crc << 1));
  }
  return (int16_t)(crc & 0xFFFF);
}
