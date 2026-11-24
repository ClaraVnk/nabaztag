/*
 * flash.c -- the SPI bus (MSSP) and the AT45DB081B / AT45DB161 serial flash.
 * Shared by the loader and the application.
 *
 * 264-byte pages on 9 address bits (081B, Lazare) or 528 on 10 (161, the
 * 2.0b boards): the density bits of the status register say which. The
 * layout is by page number (board.h), so a region holds twice as much on
 * the 161 and the code never changes. Writes go through buffer 1 and the
 * "write with erase" opcode (0x83): no sector erase anywhere.
 */
#include "board.h"

/* --- SPI: MSSP master, Fosc/16 = 1.5 MHz, CKE 1 (mode 0,0) --------------- */
void spi_init(void)
{
  TRISCbits.TRISC5 = 0;         /* SDO */
  TRISCbits.TRISC4 = 1;         /* SDI */
  TRISCbits.TRISC3 = 0;         /* SCK */
  FLASH_CS = 1; FLASH_RST = 1; OKI_CS = 1; OKI_RST = 0; LED_XLAT = 0; LED_MODE = 0;
  TRISBbits.TRISB3 = 0; TRISBbits.TRISB4 = 0; TRISBbits.TRISB5 = 0;
  TRISAbits.TRISA1 = 0; TRISAbits.TRISA2 = 0; TRISAbits.TRISA5 = 0;
  SSPSTAT = 0x40;               /* CKE */
  SSPCON1 = 0x21;               /* SSPEN, CKP 0, Fosc/16 */
}

uint8_t spi_xfer(uint8_t v)
{
  SSPBUF = v;
  while(!SSPSTATbits.BF) ;
  return SSPBUF;
}

/* --- AT45DB ---------------------------------------------------------------- */
uint16_t flash_page_size;
static uint8_t flash_shift;     /* 9 or 10 */

static uint8_t flash_status(void)
{
  uint8_t s;
  FLASH_CS = 0;
  spi_xfer(0xD7);
  s = spi_xfer(0xFF);
  FLASH_CS = 1;
  return s;
}

static void flash_wait(void)
{
  while(!(flash_status() & 0x80)) clrwdt();
}

/* The 24 address bits: page on the top bits, byte on the low 9 or 10. */
static void flash_addr(uint16_t page, uint16_t byte)
{
  if(flash_shift == 9) {
    spi_xfer((uint8_t)(page >> 7));
    spi_xfer((uint8_t)((page << 1) | (byte >> 8)));
  } else {
    spi_xfer((uint8_t)(page >> 6));
    spi_xfer((uint8_t)((page << 2) | (byte >> 8)));
  }
  spi_xfer((uint8_t)byte);
}

uint8_t flash_init(void)
{
  uint8_t density;
  TRISBbits.TRISB1 = 1;         /* RDY/BUSY */
  density = (flash_status() >> 2) & 0x0F;
  if(density == 0x0B) { flash_shift = 10; flash_page_size = 528; }
  else { flash_shift = 9; flash_page_size = 264; }
  return (density == 0x09 || density == 0x0B) ? density : 0;
}

void flash_read(uint16_t page, uint16_t offset, __data uint8_t *buf, uint16_t n)
{
  FLASH_CS = 0;
  spi_xfer(0x52);               /* main memory page read */
  flash_addr(page, offset);
  spi_xfer(0); spi_xfer(0); spi_xfer(0); spi_xfer(0);
  while(n--) *buf++ = spi_xfer(0xFF);
  FLASH_CS = 1;
}

void flash_write(uint16_t page, __data const uint8_t *buf, uint16_t n)
{
  uint16_t left = flash_page_size - n;
  flash_wait();
  FLASH_CS = 0;
  spi_xfer(0x84);               /* buffer 1 write, from byte 0 */
  flash_addr(0, 0);
  while(n--) spi_xfer(*buf++);
  while(left--) spi_xfer(0xFF); /* the rest of the page erased */
  FLASH_CS = 1;
  FLASH_CS = 0;
  spi_xfer(0x83);               /* buffer 1 to main memory, with erase */
  flash_addr(page, 0);
  FLASH_CS = 1;
  flash_wait();
}

/* --- an image slot as a byte stream ---------------------------------------- */
uint16_t slot_page(uint8_t slot) { return slot == SLOT_A ? PG_SLOT_A : PG_SLOT_B; }

static uint16_t cur_page, cur_byte;

void slot_seek(uint8_t slot)
{
  cur_page = slot_page(slot) + 1;       /* page 0 of the slot is its header */
  cur_byte = 0;
}

void slot_next(__data uint8_t *buf, uint8_t n)
{
  while(n) {
    uint16_t room = flash_page_size - cur_byte;
    uint8_t k = room < n ? (uint8_t)room : n;
    flash_read(cur_page, cur_byte, buf, k);
    buf += k; n -= k; cur_byte += k;
    if(cur_byte == flash_page_size) { cur_byte = 0; cur_page++; }
  }
}
