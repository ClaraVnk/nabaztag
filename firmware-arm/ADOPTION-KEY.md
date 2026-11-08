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

The configuration sector (`conf.bin`, 208 bytes today) grows by 32 bytes for
the key and one byte marking it present. The runtime bytecode can `load`
`conf.bin` too, so once the bootloader has handed over, the interpreter hides
those bytes from it: the key can be used through the HMAC opcode but never
read.

## Proven in the emulator (2026-11-08)

`emulator/tools/kx_warren.py` answers kx.jsp with OpenSSL's X25519 and HKDF
(neither the rabbit's TweetNaCl nor the Terrier's kx.py) and prints the five
colours it expects; `scenarios/vm/adoption-kx.txt` boots the bootloader of
`lean-wpa3-kx` against it, `--realtime`:

- no key: kx.jsp asked at 5 s; LEDs 0-4 show the warren's five colours from
  5.25 s to 66 s; bytecode fetched after; RUN.
- second boot, same eeprom: no kx.jsp; the stored colours for a minute again
  (not yet confirmed); RUN.
- a warren answering 404: bytecode fetched at once, no colours, no key; RUN.

The run also caught a real bug: the boot loop's "this network does not work"
test fired at 20 s during the minute and re-initialised the radio
(`kx_busy` now holds it off).

Open: in the emulator the rabbit's key pair is the same on every run -- the
random stream is timer, RSSI and MAC, all deterministic there. On hardware
they vary; how much is to be measured before shipping.

## Code

| piece | where | state |
|---|---|---|
| X25519 + HKDF + colours | `crypto/nab_kx.c`, tests `make kx` | done, against OpenSSL |
| X25519 watchdog | step `x25519_wdt` | done, 509 ticks per multiplication |
| warren side | Terrier: kx.py and its tests | done, same vectors |
| opcodes, key storage, hiding | steps `adoption_kx` | done |
| bootloader flow and belly colours | step `adoption_boot`, `boot-mods/adoption.mtl` | done, emulator |
| warren endpoint and confirmation page | Terrier | |
| bytecode HMAC | both | |
| TLS 1.3 PSK records | both | |
