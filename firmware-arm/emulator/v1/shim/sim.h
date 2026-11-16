/* What gw.c needs from the tag emulator's sim.c, supplied by v1/net.c. */
#ifndef _SIM_H_
#define _SIM_H_
void sim_note_fetch(void);
void sim_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#endif
