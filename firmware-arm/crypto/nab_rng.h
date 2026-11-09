/**
 * @file nab_rng.h
 * @brief One entropy pool for every secret this firmware draws.
 *
 * Before this, every random number -- SAE's rand and mask, the X25519
 * adoption key, the TLS client random, the EAPOL SNonce -- came from a
 * SHA-256 of the millisecond timer, the RSSI average and an xorshift that
 * was itself seeded from the timer. Everything in that mix is a function of
 * the moment of boot, which an observer in radio range knows to within
 * seconds, so the whole state could be searched offline from one captured
 * SAE commit; and the adoption key, whose public half travels in clear, was
 * drawn from the same stream.
 *
 * The pool is a 32-byte SHA-256 state fed from what this hardware has that
 * an observer does not:
 *
 *  - every received 802.11 frame: the millisecond it arrived, the RSSI the
 *    radio reported (its low bit is thermal noise), the sequence number and
 *    the first bytes of the body -- a beacon's TSF, a neighbour's traffic;
 *  - the power-up contents of a 4 KB external-RAM buffer that nothing
 *    initialises (NOLOAD): on a cold boot a classic SRAM noise source, on a
 *    soft reset the previous run's data;
 *  - a 32-byte carry in that same retained RAM, rewritten at every draw,
 *    so a soft reset does not restart the stream where the last boot did;
 *  - a 32-byte seed in the configuration sector, written once on the first
 *    boot whose pool was ready and read on every boot after. Written once,
 *    not every boot: the ML67Q4051's flash is rated for 100 program cycles
 *    and the sector is the one that holds the Wi-Fi password.
 *
 * Credit: 2 bits per frame (one for the RSSI, one for the arrival time;
 * the frame bytes and the SRAM count for nothing) and 128 for a stored
 * seed. Secret keys (nab_rng_key_bytes) are refused until 128 bits have
 * accrued: 64 frames. On the first boot of a rabbit's life the first SAE
 * commit listens on the AP's channel until then -- six seconds of beacons
 * from a lone AP, less with neighbours -- and kxBegin says nil until then;
 * from the second boot the seed alone opens the gate at init.
 * Values that need to be fresh but not secret (the SNonce, the TLS random)
 * use nab_rng_bytes, which answers at once with whatever has been mixed.
 *
 * There is no hardware RNG on the ML67Q4051 and none in the RT2573: the
 * Linux rt2x00 driver exposes no random or noise register for this part,
 * so the radio's contribution is the RSSI and the air itself.
 *
 * Draws are one-way: each 32-byte output is SHA-256(0x01 || state) and the
 * state then becomes SHA-256(0x02 || state), so a state read later (JTAG)
 * does not give out earlier keys.
 */
#ifndef NAB_RNG_H
#define NAB_RNG_H

#include <stdint.h>

/** Where the seed lives in the 4096-byte configuration sector: the last
 *  32 bytes. The bootloader's conf.bin ends at 262; nothing else is there. */
#define NAB_RNG_SEED_OFF   4064
#define NAB_RNG_SEED_LEN   32
/** Bits of credit before a secret may be drawn. */
#define NAB_RNG_MIN_CREDIT 128
/** Credit per received frame. */
#define NAB_RNG_FRAME_CREDIT 2

/**
 * @brief Start the pool. Call once, early, before any frame is received.
 *
 * @param sram  retained RAM to hash in (its power-up contents), or NULL
 * @param len   its length
 * @param seed  the 32 bytes read from the configuration sector, or NULL.
 *              All-0x00 or all-0xFF means "no seed stored yet".
 */
void nab_rng_init(const uint8_t *sram, uint32_t len, const uint8_t *seed);

/** Mix in arbitrary bytes with a credit of `bits`. */
void nab_rng_add(const uint8_t *p, uint32_t len, uint32_t bits);

/** One received 802.11 frame: header and body, the RSSI the radio
 *  reported, and the millisecond timer at reception. */
void nab_rng_frame(const uint8_t *frame, uint32_t len, int16_t rssi,
                   uint32_t now);

/** Credit accrued so far, in bits (saturates). */
uint32_t nab_rng_credit(void);

/** 1 once NAB_RNG_MIN_CREDIT bits have been mixed in. */
int nab_rng_ready(void);

/** Fresh bytes, always: for nonces that need not be secret. */
void nab_rng_bytes(uint8_t *out, uint32_t len);

/** Fresh bytes for a secret: 0 and nothing written until nab_rng_ready(). */
int nab_rng_key_bytes(uint8_t *out, uint32_t len);

/**
 * @brief The seed to persist, if this is the moment to.
 *
 * Returns 1 and fills `out` exactly once per boot, when the pool is ready
 * and no seed was stored when it started. The caller writes it at
 * NAB_RNG_SEED_OFF. Returns 0 otherwise.
 */
int nab_rng_seed_to_store(uint8_t out[NAB_RNG_SEED_LEN]);

/** 1 when a seed was present at init. */
int nab_rng_seed_stored(void);

#endif /* NAB_RNG_H */
