/**
 * @file nab_tls.h
 * @brief TLS 1.3 with an external pre-shared key, client side, for the
 *        XMPP connection between a rabbit and its warren.
 *
 * RFC 8446, the smallest client that interoperates: one cipher suite
 * (TLS_AES_128_GCM_SHA256), `psk_ke` only (4.2.9: no (EC)DHE, so no
 * forward secrecy -- a recorded session can be read later by whoever gets
 * the adoption key; what the key buys is that the connection cannot be
 * read or forged by anyone who does not have it, which today is everyone
 * on the path), one PSK identity, no certificates, no HelloRetryRequest,
 * no key update, no early data. Anything else the server does is a fatal
 * alert and the connection is dropped; the bytecode reconnects.
 *
 * The PSK is derived from the adoption key (ADOPTION-KEY.md) with its own
 * HKDF label, so the key that authenticates the bytecode and the key that
 * keys TLS are different keys with one parent: `nab_tls_psk`.
 *
 * The TCP/IP stack and the XMPP client are Metal bytecode, not C, so this
 * is not a socket layer: the bytecode hands in what it read and takes out
 * what to write, and every record, key and secret stays in C. Buffers
 * live in `.extbss` (external RAM, NOLOAD): `nab_tls_start` initialises
 * them, nothing else does.
 *
 *   nab_tls_start    -> the ClientHello record
 *   nab_tls_in       <- bytes from the socket, any framing
 *   nab_tls_next     -> length of the next decrypted application record,
 *                       handshake records consumed on the way
 *   nab_tls_take     -> that plaintext
 *   nab_tls_pending  -> handshake output and alerts to write
 *   nab_tls_out      -> application records for a plaintext
 *   nab_tls_close    -> close_notify
 *
 * The record size is bounded by asking the server for max_fragment_length
 * 2^12 (RFC 6066 4; allowed in TLS 1.3, RFC 8446 4.2) and a record longer
 * than NAB_TLS_RX is refused with record_overflow: 16 KB records would not
 * fit beside the VM heap.
 */
#ifndef NAB_TLS_H
#define NAB_TLS_H

#include <stdint.h>

#define NAB_TLS_PSK      32
/* The receive buffer holds one record -- 2^12 of plaintext
   (max_fragment_length), the content type byte, the tag, the header and
   room for the padding a server may add -- short of one byte, plus a whole
   TCP segment: the bytecode's TCP advertises an MSS of 1460 (tcp.mtl, the
   SYN option 02 04 05 b4), so what it hands over is at most that. */
#define NAB_TLS_FRAG     4096
#define NAB_TLS_MSS      1460
#define NAB_TLS_RX       (NAB_TLS_FRAG + 1 + 16 + 5 + 256 + NAB_TLS_MSS)
/* Overhead of one outgoing record: header, content type, tag. */
#define NAB_TLS_OVERHEAD (5 + 1 + 16)

enum {
  NAB_TLS_IDLE = 0,       /* nothing started, or start refused */
  NAB_TLS_HANDSHAKE = 1,  /* ClientHello sent, waiting for the server */
  NAB_TLS_READY = 2,      /* both Finished seen: application data flows */
  NAB_TLS_FAILED = 3,     /* a fatal alert, ours or theirs */
  NAB_TLS_CLOSED = 4      /* close_notify, ours or theirs */
};

/** The PSK from the adoption key: HKDF-SHA-256, its own salt and label. */
void nab_tls_psk(uint8_t psk[NAB_TLS_PSK], const uint8_t key[32]);

/**
 * @brief Begin: reset every buffer, derive the early secret, build the
 *        ClientHello record into `out`.
 *
 * `identity` is the PSK identity the server looks the key up by (the
 * rabbit's MAC as 12 hex digits), at most 32 bytes. `random` is 32 bytes
 * the caller drew. Returns the record length, 0 if `cap` is too small or
 * the identity too long (state stays IDLE).
 */
int nab_tls_start(const uint8_t psk[NAB_TLS_PSK],
                  const uint8_t *identity, uint32_t idlen,
                  const uint8_t random[32], uint8_t *out, uint32_t cap);

/**
 * @brief Bytes read from the socket, in any framing. Returns how many were
 *        taken: all of them when they fit, which a segment always does once
 *        the buffer has been drained with `nab_tls_next`; fewer otherwise,
 *        and the caller drains and feeds the rest. -1 when not in a
 *        connection.
 */
int nab_tls_in(const uint8_t *data, uint32_t len);

/**
 * @brief Decrypt and consume records at the head of the receive buffer.
 *
 * Handshake, alert and change_cipher_spec records are handled here;
 * handshake output they call for goes to the pending buffer. Returns the
 * plaintext length (0 or more) of an application record now waiting for
 * `nab_tls_take`, -1 when no complete record is buffered, -2 when the
 * connection has failed or closed (see `nab_tls_state`).
 */
int nab_tls_next(void);

/** Copy the plaintext `nab_tls_next` announced and drop the record. */
void nab_tls_take(uint8_t *dst);

/** Handshake output and alerts to write to the socket; cleared. */
int nab_tls_pending(uint8_t *out, uint32_t cap);

/** How many bytes `nab_tls_out` writes for `len` bytes of plaintext. */
uint32_t nab_tls_out_size(uint32_t len);

/**
 * @brief Application records for `len` bytes of plaintext, in records of
 *        at most NAB_TLS_FRAG bytes each. `in` and `out` may not overlap.
 *        Returns the length written, 0 when not READY or `cap` too small.
 */
int nab_tls_out(const uint8_t *in, uint32_t len, uint8_t *out, uint32_t cap);

/** A close_notify alert (RFC 8446 6.1); state becomes CLOSED. 0 if not READY. */
int nab_tls_close(uint8_t *out, uint32_t cap);

int nab_tls_state(void);

#endif /* NAB_TLS_H */
