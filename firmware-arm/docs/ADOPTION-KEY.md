# The per-rabbit key

What it is for: authenticating the bytecode the warren serves (HMAC), then
keying TLS 1.3 on the XMPP connection (PSK only — certificate TLS does not
fit, see `../tools/cost/results-2026-11-08.txt`). Until now everything between
a rabbit and its warren travels in clear and unauthenticated.

## The exchange

One X25519 exchange, once per rabbit, run by the **bootloader** — the code in
flash, which only a signed image can replace. Never by the runtime bytecode:
the warren serves that, unauthenticated until this key exists, so it could
show any colours it liked.

1. The rabbit boots, finds no key in its configuration sector, draws 32
   random bytes and computes its public key (TweetNaCl X25519, 2–8 s; the
   ladder feeds the watchdog, step `x25519_wdt`). The bytes come from the
   entropy pool (`crypto/nab_rng.h`, step `entropy_pool`), and only once it
   holds 128 bits of credit: `kxBegin` answers nil before that and the
   bootloader asks again on its next pass, for `KX_WAIT` seconds at most.
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
`../crypto/nab_tls.c`:

- **One suite**, TLS_AES_128_GCM_SHA256. GCM is the bit-serial GHASH from
  `../tools/cost/`, measured at 849 k instructions per KB, on cmac.c's AES block,
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
adoption ones (158-162 in `lean-wpa3-tls`), and `bytecode/patch_tls.py`
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

**The line can be removed.** `locate.jsp` is plain HTTP, and whoever owns a
hop on the path can drop `xmpp_tls` from the answer: the rabbit then opens
the plain port, where the Terrier refuses a confirmed MAC (the rabbit is
offline) -- or where the attacker answers as the warren, in the clear
(audit T6). So the runtime bytecode carries a policy the warren sets and
the HMAC covers. `patch_tls.py` compiles in the literal `xmpp-tls:0`, once;
for a rabbit whose owner confirmed the key, the Terrier flips the digit to
`1` before it signs (`mint.require_tls`, then `sign_bytecode`). With a key
and that digit, `XmppTlsSelect` opens the TLS port whatever `locate.jsp`
said: the port it named when it did, 5223 (`XMPP_TLS_DEFAULT_PORT`) when it
did not, never the plain one. The bytecode an unconfirmed rabbit gets has no
HMAC and keeps the `0`: its pending key would fail the handshake, and
anybody on the path could rewrite it anyway. One build, one file on the
warren's disk; what differs per rabbit is one byte under the signature,
which is why the decision is the warren's and not a second bytecode's: a
second file would have to be served to the right rabbits by the same
Terrier logic and rebuilt in step with the first at every change.

What is left to an attacker on the path, once the owner confirmed: the
host in `xmpp_domain` and the port in `xmpp_tls`. Either rewritten, the
rabbit does a TLS handshake with something that does not hold its key, the
handshake fails (`tlsState` 3), `XmppGateStreamClosed` reconnects, over TLS
again (`XmppTlsSelect` never picks the plain port with the digit at 1) --
for ever, nothing in the clear, which is the DoS the attacker already had
by cutting the cable. The rabbit cannot remember the TLS port across boots
without a flash write, so 5223 is a constant; a warren on another port
names it in `locate.jsp` and loses only this one fallback.

**A rabbit whose key the warren forgets.** The Terrier refuses to replace
a confirmed key (`kx_offer`); an operator who deletes the row by hand, or
points the rabbit at a warren that never knew it, gets a rabbit whose
bootloader (`conf` flag `C`) refuses every bytecode without a valid HMAC
-- it does not get as far as the TLS policy. That was true before this
change and this change adds nothing to it: a confirmed rabbit depends on
its warren holding the key. The way back is the configuration mode's reset
page: head button held at power-up, `http://192.168.0.1/reset` (the
bootloader's `confReset`, any URI starting with `r`), which rewrites the
configuration sector from its defaults -- Wi-Fi, server, key and flag all
gone; the next boot runs a fresh exchange, the owner confirms the colours
again. Saving the Wi-Fi alone (`updateconf`) edits the sector in place and
keeps the key, so a rabbit moved to another network stays adopted. A JTAG
flash of the configuration sector does the same as the reset page. On the
Terrier, `kx_offer` takes a new exchange for a MAC whose key is absent or
rejected and refuses one while a key is pending or confirmed, so a rabbit
reset this way needs its old row deleted or rejected first -- which is the
operator's deliberate act, as it should be: the row is what stops a
stranger from re-adopting someone else's rabbit.

### Proven

Against stacks we did not write, `firmware-arm/tests` (`make tls`):

- `openssl s_server 4.1.0 -tls1_3 -psk -allow_no_dhe_kex -rev` and Python
  3.15's `ssl` with `set_psk_server_callback` (what the Terrier runs, on
  OpenSSL 4.1.0 locally, 3.5.5 in its Rocky 10 container with Python
  3.13.16): handshake, 13 and 9,000 bytes both ways, close_notify exchanged.
- The OpenSSL session recorded (`../tests/session_openssl.dump`) and replayed
  offline by `test_tls.c`: with a fixed random the ClientHello, the Finished,
  the application record and the close_notify come out byte for byte as
  OpenSSL accepted them, at 1460, 7 and 1 bytes per segment; then with a
  flipped Finished, a HelloRetryRequest, another suite, a key_share, a
  flipped application record and a 16,401-byte record, each ends in a fatal
  alert and no plaintext.

Flash: `lean-wpa3-tls` is 117,172 of 126,976 bytes, 9,804 spare
(`lean-wpa3-kx` was 111,764: TLS costs 5,408). The runtime bytecode with
`patch_tls.py` is 107,119 bytes and type-checks with `../tools/mtl_comp.py --check`.

In the emulator, 2026-11-08, against a real Terrier (its container, Python
3.13.16, OpenSSL 3.5.5) on the host, `../emulator/scenarios/vm/xmpp-tls.txt`
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

The TLS policy, 2026-11-09, same bootloader, `../emulator/tools/xmpp-tls-proof.sh`
with `../emulator/tools/locate_strip.py` dropping the `xmpp_tls` line between boot 1
(exchange, pending, plain) and boot 2 (owner confirmed), real Terrier
containers:

- control, Terrier `main` and the production runtime (rebuilt from source,
  byte for byte the deployed `baseline-tls`): boot 2 opens the plain port,
  three times in the run; the Terrier answers `XMPP auth refused ...
  confirmed adoption key: TLS port only` each time. Offline.
- fix, the Terrier of the same date and the runtime with the policy
  (107,199 bytes, the literal once): boot 2 opens 5223 with no line to tell
  it to, `TLS TLSv1.3 with ..., PSK identity 0019db00beef`, bound.
- fix without the attacker: the port `locate.jsp` named, TLS, bound.
- fix with the attacker rewriting `xmpp_tls` to the plain port: four
  ClientHellos to the plain port, no XMPP, no fallback.

## What else the rabbit tells the warren, and how

Once the XMPP session runs TLS, what travels inside it is authenticated by
the key; what the runtime still sends over plain HTTP is not. The runtime
(`main.mtl`, built by `bytecode/build-bytecode.sh`) sends these requests of
its own:

| request | when | side effect on the warren | now |
|---|---|---|---|
| `locate.jsp` | boot, before XMPP | none (read; the TLS policy under the HMAC makes it harmless, above) | HTTP |
| `rfid.jsp` | a tag on the reader | **yes**: the owner's mapped action | **inside the session** |
| `record.jsp` (POST, a WAV) | a long press of the head button (`_controlrecord`) | would be | HTTP, not served by the Terrier (a POST: 501) |
| `itmode.jsp` (start, btn, ear, rfid, reco, done) | interactive mode, entered on a server command the Terrier never sends | would be | HTTP, not served by the Terrier |
| `sendMailXMPP.jsp` | XMPP register/login failure reports | none | HTTP, not served by the Terrier |
| `violet:nabaztag:button`, `violet:nabaztag:ears` | head, ears | yes | XMPP already, so inside TLS since `patch_tls.py` |
| sounds, choreographies, OTA images | commands | none (downloads) | HTTP, out of scope |
| any URL (`CU` command) | a command from the warren | whatever that URL does; the warren chose it | HTTP |
| BOSH (`/http-bind`, POST) | the gate's fallback after three failed TCP opens | the session itself | HTTP, not served by the Terrier |

Stage A, `bytecode/patch_rfid_xmpp.py` (2026-11-09): the one live side
effect moves. `controlrfid` sends the tag as a `<message>` stanza,
`violet:nabaztag:rfid`, the button's shape, through `xmpps_sendStanza` --
when the socket runs TLS (`XmppTlsOn`) and the warren asked for it: the
literal `xmpp-rfid:0`, which the Terrier flips to 1 together with
`xmpp-tls:N`, for a confirmed rabbit, under the HMAC. The Terrier fires the
action for the rabbit whose key opened the session (`tls_mac`), never for a
MAC read out of the stanza, and records at the boot that it told this rabbit
so (`bc_rfid_session`): a plain `rfid.jsp` in that rabbit's name is then
ignored, from any address. Everywhere else `rfid.jsp` goes out as before: no
key, a pending key, a warren without a TLS listener, and a Terrier from
before this change, which never flips the literal and so never receives a
stanza it would drop. A confirmed rabbit on the runtime from before keeps the
address check of the Terrier's `vouch`.

Measured in the emulator, `tools/xmpp-tls-proof.sh --scenario
scenarios/vm/xmpp-rfid.txt` (boot 2, the owner having confirmed; a tag on
the reader from 120 s to 124 s; the directive `at <ms> rfid <16 hex>` and
rabbit-vm's `--port80`, which sends the ping server's connections to the
Terrier's HTTP port, are new), four runs side by side:

- control, the production runtime (`2d1eebe3`, rebuilt byte for byte) and
  the Terrier's `main`: TLS session up, the tag goes out as four
  `GET /vl/rfid.jsp?sn=...&t=d0021a0b0c0d0e0f` (a held tag repeats every
  second) on new plain connections, vouched for by address, action fired.
- fix, the runtime with the patch (`4d711512`, 107,429 bytes, each literal
  once) and the Terrier of this change: four `<message
  from='<mac>@warren.test/idle' to='rfid@xmpp.objects.violet.net/rfid'
  id='16'><rfid xmlns="violet:nabaztag:rfid"><tag>d0021a0b0c0d0e0f</tag>`
  inside the TLS session, `rfid over the session` and the action fired for
  the session's key, no HTTP connection opened for the tag.
- the Terrier of this change with the production runtime: as the control.
- the Terrier's `main` with the new runtime: nothing flips `xmpp-rfid:0`,
  so `rfid.jsp`, vouched for, as the control.

Not yet: `record.jsp` and the interactive mode, which the Terrier does not
serve; an HTTP request an attacker forges in a rabbit's name for them is
answered 404 or 501 today. Stage B would carry them the same way if the
Terrier ever serves them.

## Code

| piece | where | state |
|---|---|---|
| X25519 + HKDF + colours | `../crypto/nab_kx.c`, tests `make kx` | done, against OpenSSL |
| X25519 watchdog | step `x25519_wdt` | done, 509 ticks per multiplication |
| warren side | Terrier: kx.py and its tests | done, same vectors |
| opcodes, key storage, hiding | steps `adoption_kx` | done |
| bootloader flow and belly colours | step `adoption_boot`, `../boot-mods/adoption.mtl` | done, emulator |
| warren endpoint and confirmation page | Terrier | done |
| bytecode HMAC | `kxCheck`, `adoption.mtl`; Terrier `sign_bytecode` | done, emulator |
| TLS 1.3 PSK | `../crypto/nab_tls.c`, step `tls_psk`, `bytecode/patch_tls.py`; Terrier xmpp.py | done, OpenSSL and Python ssl; emulator against a real Terrier |
| RFID report inside the session | `bytecode/patch_rfid_xmpp.py`; Terrier xmpp.py, protocol.py | done, emulator against a real Terrier |
