/**
 * @file phone.h
 * @brief The phone a rabbit in config mode is configured from.
 */
#ifndef _PHONE_H_
#define _PHONE_H_

#include <stdint.h>

struct phone_stats {
  uint32_t beacons_heard;
  uint32_t auth_done;
  uint32_t assoc_done;
  uint32_t arp_answered;
  uint32_t segments_sent;
  uint32_t segments_resent;
  uint32_t bytes_sent;
  uint32_t bytes_received;
  uint32_t uploads_started;
  uint32_t uploads_finished;    /* the rabbit answered and closed */
  int      response_status;     /* the status code of the last answer, 0 if none */
  uint32_t upload_started_ms;
  uint32_t upload_finished_ms;
};

extern struct phone_stats phone_stats;

/* The file the next upload sends to the upgrade page, and the TCP segment
 * size it is cut into. 800 is what the rabbit's own TCP advertises as its
 * window, so it is also what a real phone sends per segment. */
void phone_set_upload(const char *path);
void phone_set_segment(uint32_t bytes);

/* A frame the rabbit transmitted while it was the access point: its beacon,
 * its answers to the phone's authentication and association, and every data
 * frame it sends the phone. `frame` starts at the 802.11 header. */
void phone_from_rabbit(const uint8_t *frame, uint32_t length);

/* Once per pass of the main loop: join the rabbit's network when it appears,
 * and move the upload along one segment at a time. */
void phone_poll(void);

/* Start the upload now (a scenario's `upload` event). Returns 0 when it could
 * be started, -1 when there is no file or no network to send it on yet; the
 * phone then starts it by itself as soon as it has associated. */
int phone_start_upload(void);

#endif /* _PHONE_H_ */
