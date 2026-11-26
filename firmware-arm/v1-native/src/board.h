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
/* The network and the server, set at build time for a real rabbit
 * (APP_DEFS='-DSSID=\"<name>\" -DSERVER_NAME=\"<host>\"'); the defaults are
 * the emulator's. Phase 2's configuration page (frame 15) will replace them. */
#ifndef SSID
#define SSID            "Terrier"
#endif
#ifndef SERVER_NAME
#define SERVER_NAME "p.nabaztag.com"   /* the build's server; frame 15 sets another */
#endif

/* --- the two boards (Makefile: DEV, BOARD_ID, FLASH_END, WRITE_BLOCK) ---- */
/* PIC18LF6525 (Lazare, 1.4b): 48 KB, 8-byte write blocks (DS39612 6.5: eight
 * holding latches), 64-byte erase rows. PIC18F6627 (2.0b): 96 KB, 64-byte
 * write blocks (DS39646 6.5). Both erase 64-byte rows. */
#ifndef BOARD_ID
#define BOARD_ID        0x25
#define FLASH_END       0xC000UL
#define WRITE_BLOCK     8
#endif
#define APP_BASE        0x2000          /* the loader owns 0x0000-0x1FFF (also an asm literal); 0x3000 until 2026-11-25 */
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
/* Pages 0-3 are never written: page 0 is where Violet's own firmware keeps
 * its configuration (network, server), and a rabbit put back on Violet's
 * firmware must find it. Our first trial on Lazare (2026-11-24) wrote its
 * boot counter there and Violet came back unconfigured (measured on the
 * emulator after: Violet 1.4b writes page 0 and, at each boot, the program
 * it downloads at pages 1936-2067). */
#define PG_CONFIG       4               /* configuration, two copies: 4 and 5 */
#define PG_STATE_A      6               /* the loader's state record, two copies */
#define PG_STATE_B      7
#define PG_LOG          8               /* ring log, pages 8-15 */
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
void log_str(__data const char *s);
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
#define slot_write_page(slot, index, buf, n) flash_write(slot_page(slot) + (index), buf, n)
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
/* As Lazare shows them (Clara, 2026-11-25, one LED at a time from the
 * Terrier): TOP the nose, MR the belly's LEFT one (the rabbit's left), MID
 * the middle, ML the belly's RIGHT one, BOT the base. The names came from
 * Newbaztag's order and are the wrong way round for the two sides: the
 * connection's progress never lit the left one (2026-11-26). */
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
void    ears_goto(uint8_t left, uint8_t right);  /* frame 11: each ear to a place, 0-16 holes */
/* EEPROM 0x7A: the ear being driven (bit 0 ear 0, bit 1 ear 1), set before
 * the motor starts and cleared when it stops; 0x7B: the ears found to have
 * restarted the rabbit (a set bit at the start), never driven again until
 * the next ICSP write erases the EEPROM. Lazare's right ear restarts it
 * (2026-11-24): with frame 11 in every answer it would have looped. */
#define EE_EAR_MOVING   0x7A
#define EE_EAR_DEAD     0x7B
/* frame 12 (main.c): the five LEDs (bit 0 R, 1 G, 2 B; top first) for
 * `seconds`, then the network's LEDs again */
void    leds_order(__data const uint8_t *five, uint8_t seconds);
/* main.c: the network and server in use (the configuration page, else the
 * build's), and frame 15's bytes (last: the frame's last byte) */
extern __data char net_ssid[33], net_server[48];
void    config_frame_byte(uint8_t c, uint8_t last);
/* frame 13 (sound.c): a sound fetched into the serial flash, then played */
extern __data uint8_t xfer_page[528];   /* update.c: an image's page, or a sound's */
void    sound_offer(uint8_t index, uint32_t length);
uint8_t sound_wanted(void);
uint8_t sound_index(void);
void    sound_begin(void);
void    sound_byte(uint8_t c);
void    sound_end(uint8_t complete);
/* input.c: the head button and the ears by hand into sd=, the volume wheel */
void     input_init(void);
void     input_poll(void);
void     input_ears(uint8_t right, uint8_t left);
uint16_t input_sd(void);
void     input_sent(uint16_t v);
uint8_t  input_volume(void);
void     input_volume_poll(void);
void     leds_dc(uint8_t base);
void     leds_rest(uint8_t c);
uint8_t  input_volume_raw(void);
#define EAR_HOLES   17
/* Ear 0 counts on T0CKI, ear 1 on T13CKI; which is the left one is still to
 * be confirmed on Lazare (its right ear's motor is dead, 2026-11-24): the
 * one that stalls is the right one. */
#define EAR_RIGHT   0
#define EAR_LEFT    1
uint8_t ears_busy(void);

/* --- app/prism.c: the card */
extern __data uint8_t our_mac[6];
uint8_t prism_init(void);                /* 0 ok */
uint8_t prism_join(__data const char *ssid);
void    prism_poll(void);                /* events: info frames, received frames */
uint8_t prism_link(void);                /* 1 associated */
void    prism_report(uint8_t join_result);  /* the card's view into the EEPROM, once */
void    ee_put(uint8_t a, uint8_t v);       /* the data EEPROM, for the reports */
uint8_t ee_get(uint8_t a);
void    ee_words(uint8_t a, __data const uint16_t *w, uint8_t n);
extern uint16_t rx_frames, rx_bad, tx_frames, tx_exc, rx_status_last, rx_events, rx_bap_fail, ev_seen, tx_lost, tx_status_last, tx_cmd_status;
void    prism_report_link(uint8_t at);  /* BSSID, port status, SSID, our MAC into the EEPROM */
extern uint8_t tx_head[16];
void    net_report(void);                   /* every network step into the EEPROM, once, at 2 min */
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
