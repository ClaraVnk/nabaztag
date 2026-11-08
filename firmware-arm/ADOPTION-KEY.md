# The per-rabbit key

What it is for: authenticating the bytecode the warren serves (HMAC), then
keying TLS 1.3 on the XMPP connection (PSK only — certificate TLS does not
fit, see `tools/cost/results-2026-11-08.txt`). Until now everything between
a rabbit and its warren travels in clear and unauthenticated.

## The exchange

One X25519 exchange, once per rabbit, run by the **bootloader** — the code in
flash, which only a signed image can replace. Never by the runtime bytecode:
the warren serves that, unauthenticated until this key exists, so it could
show any colours it liked.

1. The rabbit boots, finds no key in its configuration sector, draws 32
   random bytes and computes its public key (TweetNaCl X25519, 2–8 s; the
   ladder feeds the watchdog, step `x25519_wdt`).
2. It sends its public key to the warren and receives the warren's.
3. Both sides run `nab_kx_derive` / `kx.derive`: HKDF-SHA-256 over the
   shared secret, salt `nabaztag adoption v1`, info `nabkx ‖ rabbit_pub ‖
   warren_pub`, 36 bytes out. The first 32 are the key. The next 15 bits are
   five colours, 3 bits each.
4. The rabbit stores the key and lights the five colours on its belly. The
   warren's page — HTTPS, behind the owner's login — shows the five colours it
   computed and asks "does the belly show these?".
5. The owner confirms. From then on the warren signs that rabbit's bytecode.

A party in the middle of step 2 ends up with one secret with the rabbit and
another with the warren: the colours differ unless it guessed 1 in 32 768 on
its single try. The spoken adoption code cannot play this role: the rabbit's
voice is audio the warren sends, which that party could replace. The spoken
code stays, to find the rabbit in the warren.

## Rabbits already adopted

No re-adoption. A rabbit without a key runs the exchange by itself on its
first boot with the new firmware; the warren shows the logged-in owner a
banner with the colours. Until the owner confirms, the bytecode is served as
today, without an HMAC. Once confirmed, the HMAC is required for that rabbit.
Later, on a date the project sets, the warren stops serving rabbits without
a key; the announcement mail explains what to do.

## Where the key lives

The configuration sector (`conf.bin`) is 208 bytes for the bootloader, and
the runtime keeps its XMPP password right after it, at 208 ("d" and 12
characters, to 220; `main.mtl`). The key goes past that: key at 224 (32
bytes), flag at 256 ('K' agreed, 'C' confirmed), colours at 257 (5). Once
the bootloader has handed over, the interpreter returns zeros for bytes 224
to 256 to `load`: the key can be used through the HMAC opcode but never read.

The first layout put the key at 208. On Nabazprout, 2026-11-08, the rabbit
then had no password, asked the warren to register, got no answer, and sat
with its belly blue; the emulator's warren speaks no XMPP and could not have
shown it. The warren now answers registration (Terrier), and the key moved.

## TLS on the XMPP connection

Once a rabbit has a confirmed key, its XMPP connection runs inside TLS 1.3
(RFC 8446) keyed from it. The smallest client that interoperates,
`crypto/nab_tls.c`:

- **One suite**, TLS_AES_128_GCM_SHA256. GCM is the bit-serial GHASH from
  `tools/cost/`, measured at 849 k instructions per KB, on cmac.c's AES block,
  which the WPA3 modes already link.
- **`psk_ke` only**: no (EC)DHE, so **no forward secrecy** -- whoever obtains
  the adoption key later can read a recorded session. The alternative,
  `psk_dhe_ke`, is one X25519 per connection: 2-8 s on this ARM7 with the
  ladder feeding the watchdog, at every reconnect. What `psk_ke` buys is that
  nobody without the key can read or forge the connection, which today is
  everyone on the path; the key itself never travels. OpenSSL refuses
  `psk_ke` unless told `SSL_OP_ALLOW_NO_DHE_KEX`, which the Terrier sets.
- **Identity** = the rabbit's MAC, twelve hex digits, what the warren keys
  `rabbit_keys` by and what every frame it sends already carries. **PSK** =
  HKDF-SHA-256 of the adoption key, salt `nabaztag tls v1`, info `nabtls psk`
  (`nab_tls_psk`, `kx.tls_psk`): the bytecode's HMAC uses the key itself, TLS
  uses this child, and neither can be turned against the other.
- **max_fragment_length 2^12** (RFC 6066) is asked for and OpenSSL honours
  it, so the receive buffer is one 4 KB record plus one 1460-byte TCP
  segment, 7 KB of external RAM (`.extbss`); nothing lands on the 16 KB
  internal SRAM. A longer record is `record_overflow`.
- Refused with a fatal alert: HelloRetryRequest, a key_share, any other
  suite or version, certificates, KeyUpdate. NewSessionTicket is skipped
  unread; close_notify is sent and understood.

### The seam

The TCP/IP stack and the XMPP client are Metal bytecode (`tcp.mtl`,
`main.mtl`), so TLS cannot sit under the socket. It sits where the XMPP gate
reads and writes it: step `tls_psk` adds five opcodes, numbered after the
adoption ones (158-162 in `lean-wpa3-tls`), and `firmware/patch_tls.py`
calls them at the three places `main.mtl` touches its socket -- the port in
`XmppTcpOpen`, `XmppTcpSend`, `XmppTcpRead`. Every record, key, secret and
nonce stays in C; the bytecode moves bytes.

| opcode | does |
|---|---|
| `tlsStart id` | the ClientHello record for PSK identity `id`; nil without a key |
| `tlsIn bytes` | feed what the socket read; the next decrypted application record, nil when none is complete |
| `tlsOut bytes` | records for the bytes; with nil, handshake output and alerts waiting to be written |
| `tlsState` | -1 no key, 0 idle, 1 handshake, 2 ready, 3 failed, 4 closed |
| `tlsClose` | close_notify |

### How the rabbit decides

`locate.jsp` adds `xmpp_tls <port>` for a rabbit whose owner confirmed its
key (the Terrier, port 5223 beside 5222) and for no other -- a pending key
would fail the handshake. The bytecode opens that port when the line is
there **and** `tlsState` says the bootloader handed a key over; otherwise the
plain port, as before. Stock bytecodes ignore the line. There is no fallback
from a failed handshake to plain: the gate reconnects, over TLS again.
Whoever can break the handshake can already cut the cable, and the warren
decides per rabbit what it serves; the plain port stays for every rabbit
during the transition.

### Proven

Against stacks we did not write, `firmware-arm/tests` (`make tls`):

- `openssl s_server 4.1.0 -tls1_3 -psk -allow_no_dhe_kex -rev` and Python
  3.15's `ssl` with `set_psk_server_callback` (what the Terrier runs, on
  OpenSSL 4.1.0 locally, 3.5.5 in its Rocky 10 container with Python
  3.13.16): handshake, 13 and 9,000 bytes both ways, close_notify exchanged.
- The OpenSSL session recorded (`tests/session_openssl.dump`) and replayed
  offline by `test_tls.c`: with a fixed random the ClientHello, the Finished,
  the application record and the close_notify come out byte for byte as
  OpenSSL accepted them, at 1460, 7 and 1 bytes per segment; then with a
  flipped Finished, a HelloRetryRequest, another suite, a key_share, a
  flipped application record and a 16,401-byte record, each ends in a fatal
  alert and no plaintext.

Flash: `lean-wpa3-tls` is 117,172 of 126,976 bytes, 9,804 spare
(`lean-wpa3-kx` was 111,764: TLS costs 5,408). The runtime bytecode with
`patch_tls.py` is 107,119 bytes and type-checks with `tools/mtl_comp.py --check`.

In the emulator, 2026-11-08, against a real Terrier (its container, Python
3.13.16, OpenSSL 3.5.5) on the host, `emulator/scenarios/vm/xmpp-tls.txt`
under the `lean-wpa3-tls` bootloader, the runtime fetched from the Terrier
each time, `--realtime`:

1. fresh eeprom: kx.jsp agrees a key (pending), colours for a minute,
   bytecode without HMAC, `locate.jsp` names no TLS port; the runtime opens
   port 5222 (15222 there), registers, binds, "ready for commands".
2. the owner confirms (`kx_decide`), same eeprom: bytecode with its HMAC,
   `xmpp_tls` in locate; one connection to the TLS port, the Terrier logs
   `TLS TLSv1.3 with 127.0.0.1, PSK identity 0019db00beef`, and the whole
   XMPP session -- stream, SASL, bind, session -- runs inside it; registered
   and bound.
3. fresh eeprom, same MAC: kx.jsp refuses a new exchange (409), no key;
   `locate.jsp` still says `xmpp_tls`, `tlsState` says -1, the runtime opens
   the plain port; registered and bound.

The run found two bugs on the way: the key sat on the runtime's XMPP
password slot (fixed on main the same evening, #87), and the gate's
hand-off after the handshake compared Metal constructors with `==`, which
never matches -- the handshake completed and the gate timed out two seconds
later, every three seconds.

## Code

| piece | where | state |
|---|---|---|
| X25519 + HKDF + colours | `crypto/nab_kx.c`, tests `make kx` | done, against OpenSSL |
| X25519 watchdog | step `x25519_wdt` | done, 509 ticks per multiplication |
| warren side | Terrier: kx.py and its tests | done, same vectors |
| opcodes, key storage, hiding | steps `adoption_kx` | done |
| bootloader flow and belly colours | step `adoption_boot`, `boot-mods/adoption.mtl` | done, emulator |
| warren endpoint and confirmation page | Terrier | done |
| bytecode HMAC | `kxCheck`, `adoption.mtl`; Terrier `sign_bytecode` | done, emulator |
| TLS 1.3 PSK | `crypto/nab_tls.c`, step `tls_psk`, `firmware/patch_tls.py`; Terrier xmpp.py | done, OpenSSL and Python ssl; emulator against a real Terrier |
