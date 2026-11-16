/* What gw.c needs from the tag emulator's vap.c, supplied by v1/net.c. */
#ifndef _VAP_H_
#define _VAP_H_
#include <stdint.h>
void vap_to_station(const uint8_t *source_mac, const uint8_t *body, uint32_t length);
#endif
