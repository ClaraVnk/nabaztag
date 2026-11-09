/**
 * @file hw.h
 * @brief The emulated hardware's own entry points.
 */
#ifndef _EMULATOR_HW_H_
#define _EMULATOR_HW_H_

#include <stdint.h>

#define HW_NB_LEDS 5
#define HW_NB_EARS 2

void hw_eeprom_open(const char *path);
void hw_eeprom_flush(void);
void hw_eeprom_volatile(int on);
/* The config page itself, for the one caller that has to read it the way
 * the bootloader's confGet* functions do. NULL until one is opened. */
const uint8_t *hw_eeprom_page(void);

void hw_tick(void);

uint32_t    hw_led(int led);
const char *hw_led_name(int led);
uint16_t    hw_ear_position(int ear);
uint32_t    hw_ear_commands(int ear);

void hw_audio_dir(const char *dir);
uint32_t hw_audio_clips(void);
uint32_t hw_audio_bytes(void);

void hw_press(int which, int down);
/* How often the firmware reached flashFirmware, and with what. The flash is
 * never written here; the call is the proof that everything before it --
 * the upload, the hex decoding, the signature -- was accepted. */
uint32_t hw_flash_writes(void);
int32_t  hw_flash_last_size(void);
void hw_present_tag(const uint8_t *id);
void hw_clear_tags(void);

#endif /* _EMULATOR_HW_H_ */
