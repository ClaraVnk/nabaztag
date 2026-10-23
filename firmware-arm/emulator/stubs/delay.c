/**
 * @file delay.c
 * @brief Busy-waits, turned into virtual time with the radio still running.
 *
 * This stub is not a no-op, and it matters. rt2501_scan() walks all fourteen
 * channels in one blocking call, sending a probe request and then sitting in
 * DelayMs(350); on the hardware the USB receive interrupt delivers the
 * beacons and probe responses during exactly that wait, and the scan callback
 * fires from inside it. An empty DelayMs() means the emulator's scan hears
 * nothing at all and every network looks absent.
 *
 * So the wait advances the virtual clock and pumps the radio, which is what
 * the interrupt does. The firmware's own 100 ms timer is deliberately NOT
 * ticked here: on the hardware it is driven from the main loop, so it does
 * not run during a blocking scan either.
 */
#include <stdint.h>

#include "sim.h"
#include "vap.h"

void DelayMs(uint16_t cmpt_ms)
{
  uint16_t left = cmpt_ms;
  while(left > 0) {
    uint16_t step = left > 10 ? 10 : left;
    sim_advance(step);
    vap_poll();
    left -= step;
  }
}

void DelayUs(uint16_t cmpt_us)
{
  (void)cmpt_us;    /* below the resolution anything here cares about */
}
