# A rabbit that is not a rabbit

The Nabaztag:tag's own Wi-Fi stack, compiled for a build machine and run
against a virtual access point. No hardware, no power cycles.

A Nabaztag downloads its bytecode at every boot and has no reset button worth
the name: a soft reset leaves the radio asleep, so "reboot" means pulling the
plug. Testing a Wi-Fi fix on the hardware therefore costs one unplugging per
attempt, and what you get back is a rabbit with its ears down and no way to
ask it why. This runs the same code — `ieee80211.c`, `eapol.c`, the crypto
beside them, with `firmware-arm`'s modifications applied — and prints the
state machine's every move. Open, WEP, WPA and WPA2 networks run end to end,
four-way handshake and group key included.

```
$ ./run.sh scenarios/open-deauth.txt
[      0 ms] AP 02:00:5e:10:00:01  SSID "Terrier"  channel 6  open  -52 dBm
[   1750 ms] AP 02:00:5e:10:00:01: probe response, SSID "Terrier", open
[   4900 ms] OPnetAuth check: crypt=0x00 (crypt&0xF0)=0x00 configured encrypt=0x00 -> netAuth IS called
[   4900 ms] state - -> RUN
[  20000 ms] AP 02:00:5e:10:00:01: sending deauthentication, reason 7
[  20050 ms] state RUN -> IDLE
[  20050 ms] link lost, retrying
[  25000 ms] state IDLE -> RUN
```

## Running it

Everything needed is in a container, so the only dependency is podman:

```sh
./run.sh                               # every scenario, pass or fail
./run.sh scenarios/open-deauth.txt     # one scenario, full transcript
./run.sh --steps wpa2_gtk,mdns         # choose which firmware mods to include
./run.sh --runtime docker              # if that is what you have
```

The exit status is the number of failed assertions, so `./run.sh` drops
straight into CI.

Without a container, point `make` at a prepared nabgcc checkout — see the
`Containerfile` for what "prepared" means, since it is four steps and getting
one wrong tests the wrong code:

```sh
make NABGCC=/path/to/nabgcc check
make NABGCC=/path/to/nabgcc asan      # the emulator under ASan and UBSan
```

## Scenarios

A scenario is a text file describing what the air looks like and what happens
to it. One line per access point, one per event.

```
# several APs on one SSID, as in a real house
ap 02:00:5e:10:00:01 Terrier 1  open -48
ap 02:00:5e:10:00:02 Terrier 6  open -61
psk nabaztag
tick 50              # virtual milliseconds per pass of the main loop
stop 120000

at 10000 expect RUN                        # assert, or exit non-zero
at 15000 deauth 02:00:5e:10:00:01 reason 7
at 50000 down 02:00:5e:10:00:01            # that AP stops answering
at 90000 expect RUN
```

| directive | meaning |
|---|---|
| `ap <bssid> <ssid> <channel> <security> [<rssi>] [pmf]` | one access point |
| `psk <passphrase>` | the pre-shared key |
| `tick <ms>` | virtual time per main-loop pass |
| `stop <ms>` | when the run ends |
| `at <ms> deauth [<bssid>] [reason <n>]` | deauthenticate the rabbit |
| `at <ms> disassoc [<bssid>] [reason <n>]` | disassociate it |
| `at <ms> down <bssid>` / `up <bssid>` | take an AP off the air, or back |
| `at <ms> drop-eapol <n>` | swallow the next `n` EAPOL frames |
| `at <ms> expect <state>` | assert `ieee80211_state`: IDLE, SCAN, AUTH, ASSOC, EAPOL, RUN |

`security` is one of `open`, `wep`, `wpa-tkip`, `wpa2-tkip`, `wpa2-ccmp`,
`wpa2-mixed`, `wpa3-sae`, `wpa2+wpa3`.

Time is virtual, not wall-clock. The firmware's only clock is `counter_timer`,
which the emulator writes itself, so `IEEE80211_RUN_TIMEOUT` — ten minutes on
the hardware — passes in a few milliseconds, and two runs of the same scenario
produce the same transcript on any machine.

## What it already answers

**WPA3 personal, SAE only: the rabbit will not even try.** Not a bug, and not
fixable in the radio. `ieee80211_input_mgt()` walks the RSN element's AKM list
looking for suite 2 (PSK) and a WPA3-only AP offers only suite 8 (SAE), so the
scan result comes back `IEEE80211_CRYPT_UNSUPPORTED` and the network is never a
candidate. SAE was standardised eleven years after this chip shipped.

**WPA2/WPA3 transition mode works.** The RSN element offers both PSK and SAE,
the AKM search finds PSK, and the rabbit treats it as an ordinary WPA2 network
(`crypt=0x4a`). That is the access-point setting that keeps a 2006 rabbit on a
2026 network — see `scenarios/wpa3-transition.txt`.

**On WPA2, the `OPnetAuth` comparison is not what blocks a reconnection.**
`vinterp.c` will only call `netAuth` when `(crypt & 0xF0) == encrypt`, where
`crypt` comes from the scan and `encrypt` from the stored configuration. The
emulator prints both numbers on every attempt, and for a CCMP network they
agree: `0x40 == 0x40`. Whatever stops a rabbit rejoining, it is not that line.

**A half-dead handshake costs a minute, then nothing happens.** Lose message
2 and the rabbit stays in `IEEE80211_S_EAPOL` until `IEEE80211_EAPOL_TIMEOUT`
expires — 64.85 s from association — then falls to `IDLE` and the driver
never tries again. Whatever retries has to live above it, and on a rabbit
that is the boot bytecode. `scenarios/wpa2-lost-msg2.txt`, and
`--no-reconnect` to watch it stop there.

**The WPA2 group key arrives and is installed.** The AP wraps a GTK with the
KEK into message 3 and the rabbit logs `GTK installed`. That is the path
`crypto/aes128.c` and the `wpa2_gtk` modification exist for, and until now it
could only be checked by flashing a rabbit and watching whether broadcast
traffic ever arrived.

## What it does not do yet

**The bytecode.** This harness drives the 802.11 layer directly, the way the
boot bytecode's `netScan` and `netAuth` pair does, rather than interpreting the
Metal bytecode a warren serves. `mkbc.py` is the start of the other half: it
turns a bytecode image into the C array the VM links against, and strips the
13-byte `amber` header that a warren prepends — feed that header to
`loaderSizeBC()` and the globals length reads as 1.7 billion.

**Everything that is not the radio.** No audio, no motors, no RFID, no TCP/IP.
A frame that is not EAPOL is counted and dropped.

## Layout

```
src/wifi_main.c   the harness: scan, choose, authenticate, watch, assert
src/vap.c         the virtual access point, in place of the USB radio
src/vapcrypto.c   AES-128 encryption and RFC 3394 key wrap, which an AP needs
                  and the rabbit does not; self-tested against the standards
src/sim.c         the virtual clock and the scenario parser
stubs/            hardware that has to exist but does nothing interesting
patches/          the simulated register window, for inc/common.h
scenarios/        the questions
```

The seam is `rt2501_tx()` and `ieee80211_input()`. Above it, everything is the
rabbit's real code. Below it, `vap.c` parses the frames the firmware transmits
and answers as an access point would.

One thing is deliberately not reimplemented: the PTK derivation calls the
firmware's own `prf()` and copies the concatenation order from `compute_ptk()`
in `eapol.c`. A handshake that only works when both ends share a mistake
proves nothing, so the key schedule is tested by vectors in `../tests` and the
emulator tests the protocol around it.

## Credit

The hardware stubs under `$(NABGCC)/testvm/stubs` and the host build they were
written for are [RedoXyde's](https://github.com/RedoXyde/nabgcc) — his
`testvm/` target is what made this possible at all, and this is an extension of
it rather than a rewrite. The AES implementation and the WPA2 group-key logic
come from [uplg/nabgcc](https://github.com/uplg/nabgcc) (MIT; text in
`../upstream/UPLG-LICENSE`). The original firmware is Violet's, 2006.
