/*
 * oki.c -- the OKI ML2870A: Violet's register sequence, measured on the
 * emulator's bus log of the 1.4b firmware (2026-11-24, tools/v1-audio-proof.sh)
 * and read in its code (v1-icsp/IMPROVEMENTS.md section 3). The chip has no
 * public datasheet: every value here is Violet's, nothing is understood
 * beyond what the names in IMPROVEMENTS.md say.
 *
 *   clock: CCP1 PWM on RC2 at Fosc/4 = 6 MHz (Timer2 prescaler 1, PR2 0,
 *          duty 2 of 4 ticks)
 *   boot:  the 22 register writes below, in Violet's order
 *   play:  45 <- 01, 35 <- rate (3: 8 kHz, 6: 16 kHz, 7: 32 kHz), 29 <- 01,
 *          33 <- 01, 10 ms; then for each burst: 29 <- 00, register 01 then
 *          up to 64 data bytes in one chip select (a burst never crosses a
 *          serial flash page: Violet's page cache did not, and the bytes
 *          and burst lengths are compared with its log), 29 <- 01; the FIFO
 *          status (register 06, bit 0) is read before each burst; at the
 *          end 33 <- 00, 29 <- 00.
 *
 * The data bytes go to the chip as they are in the file; the nibble order
 * the real ML2870A wants is not settled (IMPROVEMENTS.md #1), so a flag
 * swaps the nibbles of every byte: OKI_SWAP_NIBBLES at build time today, a
 * configuration bit when the page exists. The emulator's OKI is a register
 * log whose FIFO status is always "room": the proof is the sequence and the
 * bytes, not the pace.
 */
#include "board.h"

static const __code uint8_t boot_regs[][2] = {
  { 0x11, 0x21 }, { 0x13, 0x00 }, { 0x15, 0x01 }, { 0x19, 0x1D }, { 0x17, 0x00 }, { 0x17, 0x08 },
  { 0x57, 0x00 }, { 0x55, 0xFF }, { 0x41, 0x00 }, { 0x43, 0xFF }, { 0x45, 0x00 },
  { 0x63, 0x0F }, { 0x37, 0x00 }, { 0x39, 0x19 }, { 0x67, 0xCB }, { 0x25, 0x40 }, { 0x21, 0xC0 },
  { 0x23, 0x01 }, { 0x35, 0x03 }, { 0x45, 0x00 }, { 0x31, 0x00 }, { 0x29, 0x00 }
};

static void oki_wr(uint8_t reg, uint8_t v)
{
  OKI_CS = 0;
  spi_xfer(reg); spi_xfer(v);
  OKI_CS = 1;
}

static uint8_t oki_rd(uint8_t reg)
{
  uint8_t v;
  OKI_CS = 0;
  spi_xfer(reg); v = spi_xfer(0);
  OKI_CS = 1;
  return v;
}

void oki_init(void)
{
  uint8_t i;
  TRISCbits.TRISC2 = 0;
  PR2 = 0;
  CCPR1L = 0;
  CCP1CON = 0x2C;               /* PWM, the duty's two low bits = 2 */
  T2CON = 0x04;                 /* on, prescaler 1 */
  OKI_RST = 1;
  delay_ms(2);
  for(i = 0; i < sizeof(boot_regs) / 2; i++) oki_wr(boot_regs[i][0], boot_regs[i][1]);
}

/* What the last sound did, for the next poll's sp= (net.c; Clara,
 * 2026-11-26: "aucun des tests sons ne fonctionne sur lazare", and the
 * rabbit has no serial line to read): oki_last 1 played to the end, 2 the
 * switch on silent, 3 the download cut short, 4 refused (size); the bursts
 * sent, and how many found the FIFO never saying "room" (a time-out). */
uint8_t  oki_last;
uint16_t oki_bursts, oki_stalls;

/* An "Audi" file in the serial flash from page `page`: 16-byte header (byte
 * 8 gives the rate), the ADPCM from offset 16, `length` data bytes. */
void oki_play(uint16_t page, uint32_t length)
{
  uint8_t  buf[64], n, i, rate;
  uint16_t off = 16;
  /* The volume wheel at its lowest is the rabbit's silent mode: nothing
   * plays (Clara, 2026-11-25: "le mode silencieux de lazare (matériel) est
   * ignoré"; it was only read and reported). */
  {
    /* The three-way sound switch, as Violet's 1.4b sets it before a sound
     * (0x5FE6 -> 0x1FBC: level 0, 1, 2 -> volume 0x00, 0xC8, 0xFF, written
     * as the attenuation (0xFF - volume) >> 4 to 0x63, then 0x39 <- 5):
     * silent, normal, loud. */
    static const uint8_t att[3] = { 0x0F, 0x03, 0x00 };
    uint8_t lvl;
    /* The chip's power-up, then the switch, as Violet's 0x5FDE: 0x200A
     * (0x31 <- 0, 0x29 <- 0), 0x1FE4 (0x31 <- 1, 10 ms, 0x29 <- 2). */
    oki_wr(0x31, 0x00); oki_wr(0x29, 0x00);
    oki_wr(0x31, 0x01); delay_ms(10); oki_wr(0x29, 0x02);
    lvl = input_volume_read();
    oki_bursts = 0; oki_stalls = 0;
    if(lvl == 0) {
      oki_wr(0x29, 0x00); oki_wr(0x31, 0x00);
      log_line("oki: silent (sound switch)"); oki_last = 2; return;
    }
    oki_wr(0x63, att[lvl]); oki_wr(0x39, 0x05);
  }
  flash_read(page, 8, &rate, 1);
  rate = rate == 0x1F ? 3 : rate == 0x7D ? 7 : 6;
  log_s("oki: play "); log_dec((uint16_t)length); log_s(" B rate "); log_dec(rate); log_nl();
  oki_wr(0x45, 0x01); oki_wr(0x35, rate); oki_wr(0x29, 0x01); oki_wr(0x33, 0x01);
  delay_ms(10);
  while(length) {
    uint16_t room = flash_page_size - off;
    n = room < 64 ? (uint8_t)room : 64;
    if(length < n) n = (uint8_t)length;
    flash_read(page, off, buf, n);
    off += n; length -= n;
    if(off == flash_page_size) { off = 0; page++; }
    /* Room in the FIFO: register 0x06 bit 0 SET, Violet's 1.4b refills
     * when it is (0x07A6-0x07B0: btfsc 0,0 -> rcall 0x03C0). This waited
     * while it was set (2026-11-25): every burst after the time-out, the
     * sound in clicks and far too long, the rabbit not polling meanwhile
     * (Lazare, 2026-11-26: "un tac trac tac", "le son ne s'arrête plus"). */
    { uint16_t k; for(k = 0; k < 20000 && !(oki_rd(0x06) & 1); k++) clrwdt();
      if(k == 20000) oki_stalls++; }
    oki_bursts++;
    oki_wr(0x29, 0x00);
    OKI_CS = 0;
    spi_xfer(0x01);
    for(i = 0; i < n; i++) {
#ifdef OKI_SWAP_NIBBLES
      spi_xfer((uint8_t)((buf[i] << 4) | (buf[i] >> 4)));
#else
      spi_xfer(buf[i]);
#endif
    }
    OKI_CS = 1;
    oki_wr(0x29, 0x01);
    clrwdt();
  }
  oki_wr(0x33, 0x00); oki_wr(0x29, 0x00);
  oki_last = 1;
  log_line("oki: done");
}
