/*
 * board.h -- the Nabaztag v1 (2005) around its PIC18LF6525: who is on which pin,
 * and what the loader and the application share (the serial flash layout,
 * the image header, the loader's state record).
 *
 * Pins as the emulator models them (firmware-arm/emulator/v1/board.c, read
 * from Violet's own bus routines) and as Newbaztag traced them on a real
 * board (vendor/newbaztag/doc/components.md). Where the two disagree the
 * emulator's reading of Violet's firmware wins, and the disagreement is noted.
 */
#ifndef BOARD_H
#define BOARD_H

#include <pic18fregs.h>
#include <stdint.h>

#define FOSC_HZ         24000000UL
#ifndef APP_VERSION
#define APP_VERSION     30              /* the poll's v=: Violet's were 12, 14, 20 */
#endif
#define STR_(x)         #x
#define STR(x)          STR_(x)
#define FW_VERSION      STR(APP_VERSION)
#define SSID            "Terrier"       /* phase 1: a constant; phase 2 reads the configuration page */

/* --- the two boards (Makefile: DEV, BOARD_ID, FLASH_END, WRITE_BLOCK) ---- */
/* PIC18LF6525 (Lazare, 1.4b): 48 KB, 8-byte write blocks (DS39612 6.5: eight
 * holding latches), 64-byte erase rows. PIC18F6627 (2.0b): 96 KB, 64-byte
 * write blocks (DS39646 6.5). Both erase 64-byte rows. */
#ifndef BOARD_ID
#define BOARD_ID        0x25
#define FLASH_END       0xC000UL
#define WRITE_BLOCK     8
#endif
#define APP_BASE        0x3000          /* the loader owns 0x0000-0x2FFF (also an asm literal) */
#define APP_MAX         (FLASH_END - APP_BASE)
#define APP_HDR_OFF     0x40            /* the application's header, after the vectors */

/* The application's header, at APP_BASE + APP_HDR_OFF in the PIC and, as
 * the first 16 bytes of a slot's header page, in the serial flash. mkimage.py
 * fills length (the image rounded up to 64 bytes). */
struct app_hdr {
  uint8_t  magic[4];                    /* 'N' 'B' 'V' '1' */
  uint8_t  format;                      /* 1 */
  uint8_t  board;                       /* BOARD_ID */
  uint16_t version;
  uint32_t length;                      /* bytes from APP_BASE, a multiple of 64 */
  uint8_t  pending;                     /* slot header only: 1 = install me (not under the HMAC) */
  uint8_t  pad[3];
};
#define HDR_HMAC_OFF    16              /* HMAC-SHA-256 over bytes 0..11 of the header, then the image */
#define HDR_COMPLETE_OFF 48             /* 0xA5 once every image byte is in the slot */
#define HDR_COMPLETE    0xA5
#define HDR_SIZE        64

/* --- UART1 (J3, the serial log): 9600 8N1, BRGH=0 -> SPBRG = Fosc/64/9600 - 1 */
#define UART_SPBRG      38

/* SPI bus (MSSP): RC3 SCK, RC4 SDI, RC5 SDO */
#define FLASH_CS        LATBbits.LATB4  /* AT45DB /CS (measured on Violet's 1.4b; RB3 is its RST) */
#define FLASH_RST       LATBbits.LATB3
#define OKI_CS          LATAbits.LATA2
#define OKI_RST         LATAbits.LATA1
#define LED_XLAT        LATBbits.LATB5  /* TLC5922 */
#define LED_MODE        LATAbits.LATA5  /* 0: on/off register, 1: dot correction */

/* Head button: RB0, low while pressed. */
#define BUTTON_DOWN     (!PORTBbits.RB0)

/* Ear motors through the L293D, one direction per pin (the emulator's
 * reading of the firmware: RF0/RF1 and RF2/RF3, not RD1 as Newbaztag's list
 * says -- PORTD is the card's data bus). Hole sensors: ear 0 on RA4/T0CKI
 * (TMR0 counts), ear 1 on RC0/T13CKI (TMR3 counts). */
#define EAR0_A          LATFbits.LATF0
#define EAR0_B          LATFbits.LATF1
#define EAR1_A          LATFbits.LATF2
#define EAR1_B          LATFbits.LATF3

/* The PCMCIA Wi-Fi card, an 8-bit bus driven by hand: PORTE address, PORTD
 * data, RF7 /IORD, RG4 /IOWR, RF5 /OE (attribute read), RF6 /WE (attribute
 * write). What acts: /IORD and /OE falling, /IOWR and /WE rising. */
#define CARD_ADDR       LATE
#define CARD_DATA_OUT   LATD
#define CARD_DATA_IN    PORTD
#define CARD_DATA_TRIS  TRISD
#define CARD_IORD       LATFbits.LATF7
#define CARD_IOWR       LATGbits.LATG4
#define CARD_OE         LATFbits.LATF5
#define CARD_WE         LATFbits.LATF6

#define clrwdt()        __asm__("clrwdt")
#define nop()           __asm__("nop")

/* --- the serial flash layout, by page number (DESIGN.md 4.3) ------------- */
#define PG_CONFIG       0               /* configuration, two copies: 0 and 1 */
#define PG_STATE_A      2               /* the loader's state record, two copies */
#define PG_STATE_B      3
#define PG_LOG          4               /* ring log, pages 4-15 */
#define PG_SLOT_A       16              /* image slot A: header page, then the image */
#define PG_SLOT_B       208             /* image slot B */
#define SLOT_PAGES      192
#define PG_SOUNDS       976             /* the directory page; the sounds follow */

/* The sound directory (page PG_SOUNDS): 'SND1', count, autoplay (the
 * application plays sound 0 at boot: the audio proof), then per sound the
 * first page (u16) and the Audi file's data length (u32); a file starts at
 * byte 0 of its first page with its 16-byte header. */
struct snd_dir { uint8_t magic[4]; uint8_t count; uint8_t autoplay; struct { uint16_t page; uint32_t length; } s[8]; };

/* The loader's state record: which slot the PIC holds, how many boots the
 * new image has had without confirming itself. 16 bytes, two pages, the
 * newer sequence number wins; a write cut by the power loses one copy. */
struct lstate {
  uint8_t  magic[4];                    /* 'N' 'B' 'L' 'S' */
  uint16_t seq;
  uint8_t  active;                      /* SLOT_A, SLOT_B or SLOT_NONE */
  uint8_t  tries;                       /* boots since the install, until confirmed */
  uint8_t  flags;
  uint16_t skip_ver;                    /* an image version refused or fallen back from: never installed again by itself */
  uint8_t  rsv[4];
  uint8_t  csum;                        /* the 16 bytes sum to 0 */
};
#define SLOT_A          0
#define SLOT_B          1
#define SLOT_NONE       0xFF
#define LS_CONFIRMED    0x01            /* the application reached the Terrier */
#define LS_MAX_TRIES    5

/* --- common/uart.c: the log */
void uart_init(void);
void uart_putc(char c);
void log_s(const __code char *s);        /* string from flash */
void log_hex8(uint8_t v);
void log_hex16(uint16_t v);
void log_nl(void);
void log_line(const __code char *s);     /* s then newline */

/* --- common/flash.c: the SPI bus and the AT45DB */
void    spi_init(void);
uint8_t spi_xfer(uint8_t v);
uint8_t flash_init(void);                /* density code: 0x09 AT45DB081B, 0x0B AT45DB161; 0 unknown */
extern uint16_t flash_page_size;         /* 264 or 528 */
void    flash_read(uint16_t page, uint16_t offset, __data uint8_t *buf, uint16_t n);
void    flash_write(uint16_t page, __data const uint8_t *buf, uint16_t n);  /* n bytes at offset 0, rest erased */
/* A slot's image as one byte stream across its pages: seek to byte 0 (page
 * slot+1), then read in order. Page 0 of a slot is its header. */
void    slot_seek(uint8_t slot);
void    slot_next(__data uint8_t *buf, uint8_t n);
void    slot_write_page(uint8_t slot, uint16_t index, __data const uint8_t *buf, uint16_t n);
uint16_t slot_page(uint8_t slot);

/* --- common/state.c */
void    state_read(__data struct lstate *st);         /* the newest valid copy, or a blank record */
void    state_write(__data struct lstate *st);        /* seq+1 into the older page */

#ifndef LOADER
/* --- app/hw.c: TMR1 free-running at Fosc/4, a tick per overflow (10.92 ms); the LEDs */
void     timer_init(void);
void     timer_poll(void);
uint16_t ticks(void);
#define  TICKS_PER_S    92
#define  MS_TO_TICKS(ms) ((uint16_t)((ms) / 11))
void     delay_ms(uint16_t ms);          /* busy, feeds the watchdog */
void     log_dec(uint16_t v);
void     log_ip(__data const uint8_t *ip);
void     log_mac(__data const uint8_t *mac);
void    leds_set(uint16_t onoff);        /* bit 15 top R, 14 top B, 13 top G ... 1 bottom G */
#define LED_TOP_R   0x8000
#define LED_TOP_B   0x4000
#define LED_TOP_G   0x2000
#define LED_MR_R    0x1000
#define LED_MR_B    0x0800
#define LED_MR_G    0x0400
#define LED_MID_R   0x0200
#define LED_MID_B   0x0100
#define LED_MID_G   0x0080
#define LED_ML_R    0x0040
#define LED_ML_B    0x0020
#define LED_ML_G    0x0010
#define LED_BOT_R   0x0008
#define LED_BOT_B   0x0004
#define LED_BOT_G   0x0002

/* --- app/ears.c */
void    ears_init(void);
void    ears_poll(void);
void    ear_move(uint8_t ear, int8_t holes);
uint8_t ears_busy(void);

/* --- app/prism.c: the card */
extern __data uint8_t our_mac[6];
uint8_t prism_init(void);                /* 0 ok */
uint8_t prism_join(const __code char *ssid);
void    prism_poll(void);                /* events: info frames, received frames */
uint8_t prism_link(void);                /* 1 associated */
void    prism_tx(__data const uint8_t *frame, uint16_t len);   /* an 802.3 frame: dst, src, len, LLC... */
/* reading the frame being received, word by word from the card */
uint8_t  rx_byte(void);
uint16_t rx_be16(void);
void     rx_skip(uint16_t n);
void     rx_bytes(__data uint8_t *dst, uint8_t n);

/* --- app/net.c */
void net_init(void);
void net_poll(void);
void net_rx(uint16_t len);               /* the card has a frame of len bytes at offset 60 */
uint8_t net_state(void);
#define NET_DOWN      0
#define NET_DHCP      1
#define NET_DNS       2
#define NET_READY     3
extern uint16_t polls_answered;

/* --- app/oki.c: the ML2870A, Violet's register sequence */
void    oki_init(void);
void    oki_play(uint16_t page, uint32_t length);   /* an Audi file from the serial flash */

/* --- app/update.c: a new image into the other slot, then the loader */
void    update_init(void);
void    update_offer(uint16_t version);  /* the poll's frame 10: what the Terrier has */
uint8_t update_wanted(void);             /* 1: fetch the image when the poll's connection is closed */
void    update_begin(void);              /* the image's connection is open */
void    update_byte(uint8_t c);          /* one byte of the image's body */
void    update_end(uint8_t complete);    /* the image's connection closed; complete: with a FIN */
void    update_confirm(void);            /* the Terrier answered: this image is good */
#endif

#endif
