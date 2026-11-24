/*
 * board.h -- the Nabaztag v1 (2005) around its PIC18LF6525: who is on which pin.
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
#define FW_VERSION      "30"            /* the poll's v=: Violet's were 12, 14, 20 */
#define SSID            "Terrier"       /* phase 1: a constant; phase 2 reads the configuration page */

/* UART1 (J3, the serial log): 9600 8N1, BRGH=0 -> SPBRG = Fosc/64/9600 - 1 */
#define UART_SPBRG      38

/* SPI bus (MSSP): RC3 SCK, RC4 SDI, RC5 SDO */
#define FLASH_CS        LATBbits.LATB4  /* AT45DB /CS (Newbaztag); RB3 is its RST. */
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

/* --- timer.c: TMR1 free-running at Fosc/4, a tick per overflow (10.92 ms) */
void     timer_init(void);
void     timer_poll(void);
uint16_t ticks(void);
#define  TICKS_PER_S    92
#define  MS_TO_TICKS(ms) ((uint16_t)((ms) / 11))
void     delay_ms(uint16_t ms);          /* busy, feeds the watchdog */

/* --- uart.c: the log */
void uart_init(void);
void uart_putc(char c);
void log_s(const __code char *s);        /* string from flash */
void log_hex8(uint8_t v);
void log_hex16(uint16_t v);
void log_dec(uint16_t v);
void log_ip(__data const uint8_t *ip);
void log_mac(__data const uint8_t *mac);
void log_nl(void);
void log_line(const __code char *s);     /* s then newline */

/* --- spi.c: the bus, the TLC5922, the AT45DB */
void    spi_init(void);
uint8_t spi_xfer(uint8_t v);
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
uint8_t flash_init(void);                /* density code: 0x09 AT45DB081B, 0x0B AT45DB161; 0 unknown */
void    flash_read(uint16_t page, uint16_t offset, __data uint8_t *buf, uint8_t n);
void    flash_write(uint16_t page, __data const uint8_t *buf, uint8_t n);  /* n bytes at offset 0, rest erased */

/* --- ears.c */
void    ears_init(void);
void    ears_poll(void);
void    ear_move(uint8_t ear, int8_t holes);
uint8_t ears_busy(void);

/* --- prism.c: the card */
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
void     rx_seek(uint16_t offset);       /* offset in the card's buffer; 60 = first payload byte */
void     rx_bytes(__data uint8_t *dst, uint8_t n);
uint16_t rx_remaining(void);

/* --- net.c */
void net_init(void);
void net_poll(void);
void net_rx(uint16_t len);               /* the card has a frame of len bytes at offset 60 */
uint8_t net_state(void);
#define NET_DOWN      0
#define NET_DHCP      1
#define NET_DNS       2
#define NET_READY     3
extern uint16_t polls_answered;

#endif
