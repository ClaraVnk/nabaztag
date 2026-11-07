/**
 * @file prelude.h
 * @brief Declarations the firmware's sources use without declaring.
 *
 * Force-included into every translation unit by the Makefile. Each of these
 * is defined somewhere in nabgcc but declared in no header, so on the ARM
 * build they come through as implicit declarations the compiler is lenient
 * about. A host compiler is not, and a function implicitly declared as
 * returning int while it returns a pointer is exactly the kind of thing that
 * crashes an emulator half an hour after you stop suspecting the build.
 */
#ifndef _EMULATOR_PRELUDE_H_
#define _EMULATOR_PRELUDE_H_

#include <stdint.h>

/* testvm/testvm.c defines this; the HAL stubs call it. */
void consolestr(const void *s);

/* main.c defines these on the hardware. */
void dump(uint8_t *src, int32_t len);
uint8_t push_button_value(void);
void reset_uc(void);

/* ieee80211.c defines these and inc/net/ieee80211.h declares them, but
 * vinterp.c -- where the two opcodes that call them live -- includes no
 * network header at all. The ARM build gets away with it because GCC 12
 * only warns about an implicit declaration; GCC 14 rejects it, and the VM
 * target stopped building the day it was first pointed at the bootloader
 * bytecode. Declared here rather than by including the header, which drags
 * the whole 802.11 world into every translation unit. */
void ieee80211_sae_password(const uint8_t *password);
uint32_t ieee80211_sae_time_commit(const uint8_t *password);

/* eapol.c exports this and declares it nowhere. mypassword_to_pmk is
 * deliberately absent: eapol.c defines it with a uint16_t length and
 * vnet.c declares it with an int32_t one, so any declaration here conflicts
 * with one of them. Whoever needs it declares it locally. */
void prf(const uint8_t *key, uint16_t key_len,
         const uint8_t *prefix, uint16_t prefix_len,
         const uint8_t *data, uint16_t data_len,
         uint8_t *output, uint16_t len);

#endif /* _EMULATOR_PRELUDE_H_ */
