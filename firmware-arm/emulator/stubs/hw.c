/**
 * @file hw.c
 * @brief The rabbit's hardware, as something a host can watch.
 *
 * Everything above this is real: the Metal VM, the bytecode a warren serves,
 * the 802.11 stack. This is the layer the bytecode reaches for when it wants
 * to light a LED, turn an ear, read a button or remember a setting — the
 * four things that make a Nabaztag a Nabaztag rather than a very slow router.
 *
 * It is deliberately not a set of empty functions. Three of these have
 * behaviour the bytecode depends on, and an emulator whose ears never move is
 * an emulator that hangs in the ear calibration:
 *
 *   - the EEPROM is 4096 bytes backed by a file, so a configured rabbit stays
 *     configured between runs. sysLoad()/sysSave() in vlog.c land here, and
 *     the layout is the boot code's own: a server URL, DHCP or a static
 *     address, the SSID, the cipher, the PMK, and 0x47 at offset 207 to say
 *     the page means something. mkconf.py writes one.
 *   - the ear motors have an encoder that counts up while they turn, 17
 *     counts to the revolution. That number is not invented: boot.mtl tests
 *     `(i-count0)==17` to decide an ear has been all the way round.
 *   - the LEDs keep their colour and report every change, so a scenario can
 *     assert that the belly went green rather than hoping.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "hal/led.h"
#include "hal/motor.h"
#include "utils/mem.h"

#include "sim.h"
#include "hw.h"

/* --- the EEPROM ------------------------------------------------------- */

#define EEPROM_SIZE 4096

static uint8_t eeprom[EEPROM_SIZE];
static char eeprom_path[512];
static int eeprom_dirty;

/**
 * @brief Point the EEPROM at a file, reading it if it is there.
 *
 * A missing file is not an error: that is a rabbit out of the box, which
 * boots into its configuration mode, and watching it do that is a legitimate
 * thing to want.
 */
void hw_eeprom_open(const char *path)
{
  FILE *f;
  size_t n;

  snprintf(eeprom_path, sizeof(eeprom_path), "%s", path ? path : "");
  memset(eeprom, 0, sizeof(eeprom));
  if(path == NULL || path[0] == 0) {
    sim_log("eeprom: none, this rabbit is out of the box");
    return;
  }
  f = fopen(path, "rb");
  if(f == NULL) {
    sim_log("eeprom: %s does not exist yet, starting blank", path);
    return;
  }
  n = fread(eeprom, 1, sizeof(eeprom), f);
  fclose(f);
  sim_log("eeprom: %s, %zu bytes, magic 0x%02x %s", path, n, eeprom[207],
          eeprom[207] == 0x47 ? "(configured)" : "(blank — expect config mode)");
}

/** @brief Write the EEPROM back, if the rabbit changed it. */
void hw_eeprom_flush(void)
{
  FILE *f;

  if(!eeprom_dirty || eeprom_path[0] == 0) return;
  f = fopen(eeprom_path, "wb");
  if(f == NULL) {
    sim_log("eeprom: cannot write %s", eeprom_path);
    return;
  }
  fwrite(eeprom, 1, sizeof(eeprom), f);
  fclose(f);
  sim_log("eeprom: written back to %s", eeprom_path);
  eeprom_dirty = 0;
}

void read_uc_flash(uint32_t address, uint8_t *data, uint32_t nb_byte)
{
  if(address >= EEPROM_SIZE) return;
  if(address + nb_byte > EEPROM_SIZE) nb_byte = EEPROM_SIZE - address;
  memcpy(data, eeprom + address, nb_byte);
}

void write_uc_flash(uint32_t address, uint8_t *data, uint32_t nb_byte,
                    uint8_t *temp)
{
  (void)temp;
  if(address >= EEPROM_SIZE) return;
  if(address + nb_byte > EEPROM_SIZE) nb_byte = EEPROM_SIZE - address;
  if(memcmp(eeprom + address, data, nb_byte) == 0) return;
  memcpy(eeprom + address, data, nb_byte);
  eeprom_dirty = 1;
  sim_log("eeprom: the rabbit wrote %u bytes at %u", nb_byte, address);
  hw_eeprom_flush();
}

void init_uc_flash(void) {}

void flash_uc(uint8_t *data, int32_t nb_byte, uint8_t *temp)
{
  (void)data; (void)temp;
  sim_log("the rabbit asked to flash itself with a %d-byte image — ignored, "
          "there is no flash here to brick", nb_byte);
}

void setup_ext_sram_rom(void) {}
void setup_malloc(void) {}

/* --- the LEDs --------------------------------------------------------- */

/* The names the add-on and Le Terrier use, so a transcript reads the same way
 * the API does: 0 is the one in the belly. */
static const char *led_names[HW_NB_LEDS] = {
  "bottom", "left", "middle", "right", "top"
};

static uint32_t led_colour[HW_NB_LEDS];

void init_led_rgb_driver(void) {}

void set_led_rgb(uint32_t color)
{
  /* The packed form: the LED number in the top byte, as led.h's LED_RGB_n. */
  uint8_t led = (uint8_t)((color >> 24) & 0xff);
  if(led >= 1 && led <= HW_NB_LEDS) set_led((uint8_t)(led - 1), color & 0xffffff);
}

void set_led(uint8_t led, uint32_t color)
{
  if(led >= HW_NB_LEDS) return;
  color &= 0xffffff;
  if(led_colour[led] == color) return;
  led_colour[led] = color;
  sim_log("led %s = #%02x%02x%02x", led_names[led], (color >> 16) & 0xff,
          (color >> 8) & 0xff, color & 0xff);
}

uint32_t hw_led(int led)
{
  return (led >= 0 && led < HW_NB_LEDS) ? led_colour[led] : 0;
}

const char *hw_led_name(int led)
{
  return (led >= 0 && led < HW_NB_LEDS) ? led_names[led] : "?";
}

/* --- the ears --------------------------------------------------------- */

/* One revolution is 17 encoder counts: boot.mtl decides an ear has been all
 * the way round when the count has advanced by exactly 17. A real ear takes
 * roughly a second to turn, so a count is about 60 ms. */
#define HW_EAR_COUNTS_PER_TURN 17
#define HW_MS_PER_COUNT        60

struct hw_ear {
  int      running;      /* 0 stopped, +1 or -1 for the direction */
  uint16_t position;     /* the encoder count, which only ever goes up */
  uint32_t last_tick_ms;
};

static struct hw_ear ears[HW_NB_EARS];

void init_pwm(void) {}

void run_motor(uint8_t number, uint8_t speed, uint8_t rotation)
{
  struct hw_ear *e;
  int dir;

  if(number < 1 || number > HW_NB_EARS) return;
  e = &ears[number - 1];
  dir = (rotation == REVERSE) ? -1 : 1;
  if(e->running == dir) return;
  e->running = dir;
  e->last_tick_ms = sim_now();
  sim_log("ear %d turning %s (speed %u)", number - 1,
          dir > 0 ? "forwards" : "backwards", speed);
}

void stop_motor(uint8_t number)
{
  struct hw_ear *e;

  if(number < 1 || number > HW_NB_EARS) return;
  e = &ears[number - 1];
  if(!e->running) return;
  e->running = 0;
  sim_log("ear %d stopped at count %u (%u of a turn)", number - 1,
          e->position, e->position % HW_EAR_COUNTS_PER_TURN);
}

uint16_t get_motor_position(uint8_t number)
{
  if(number < 1 || number > HW_NB_EARS) return 0;
  return ears[number - 1].position;
}

/**
 * @brief Advance the encoders by however much virtual time has passed.
 *
 * Called once per pass of the main loop. The count rises whichever way the
 * ear is turning, because that is what an encoder on one wheel does: it
 * counts notches, not directions.
 */
void hw_tick(void)
{
  uint32_t now = sim_now();

  for(int i = 0; i < HW_NB_EARS; i++) {
    struct hw_ear *e = &ears[i];
    if(!e->running) continue;
    while(now - e->last_tick_ms >= HW_MS_PER_COUNT) {
      e->last_tick_ms += HW_MS_PER_COUNT;
      e->position++;
    }
  }
}

uint16_t hw_ear_position(int ear)
{
  return (ear >= 0 && ear < HW_NB_EARS) ? ears[ear].position : 0;
}

/* --- the buttons ------------------------------------------------------ */

/* The head button, which the bytecode reads through an ADC: sysButton3()
 * returns 255 - get_adc_value(), so a released button is a high reading. */
static uint8_t adc_value = 255;
static uint8_t button_down;

void hw_press(int which, int down)
{
  if(which == 2) {
    button_down = down ? 1 : 0;
    sim_log("button 2 %s", down ? "pressed" : "released");
  } else {
    adc_value = down ? 0 : 255;
    sim_log("the head button %s", down ? "pressed" : "released");
  }
}

uint8_t get_adc_value(void)
{
  return adc_value;
}

uint8_t push_button_value(void)
{
  return button_down;
}

/* --- RFID ------------------------------------------------------------- */

/* Up to a few tags presented to the rabbit; a scenario puts them there. */
#define HW_MAX_TAGS 4

static uint8_t tags[HW_MAX_TAGS][8];
static int nb_tags;

void hw_present_tag(const uint8_t *id)
{
  if(nb_tags >= HW_MAX_TAGS) return;
  memcpy(tags[nb_tags], id, 8);
  nb_tags++;
  sim_log("rfid: a tag was presented (%02x%02x%02x%02x%02x%02x%02x%02x)",
          id[0],id[1],id[2],id[3],id[4],id[5],id[6],id[7]);
}

void hw_clear_tags(void)
{
  nb_tags = 0;
}

void init_rfid(void) {}
void rfid_events(void) {}

uint8_t *get_rfid_first_device(void)
{
  return nb_tags > 0 ? tags[0] : NULL;
}

int32_t check_rfid_n(void)
{
  return nb_tags;
}

uint8_t *get_nth_rfid(int32_t i)
{
  return (i >= 0 && i < nb_tags) ? tags[i] : NULL;
}

int32_t rfid_read(uint8_t *id, int32_t bloc, uint8_t *data)
{
  (void)id; (void)bloc;
  memset(data, 0, 4);
  return 0;
}

int32_t rfid_write(uint8_t *id, int32_t bloc, uint8_t *data)
{
  (void)id; (void)data;
  sim_log("rfid: block %d written", bloc);
  return 0;
}
