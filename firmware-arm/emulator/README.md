# A rabbit that is not a rabbit

The Nabaztag:tag's own firmware, compiled for a build machine and run against
a virtual access point and a virtual network. No hardware, no power cycles.

There are two of them:

| | what it runs | what it is for |
|---|---|---|
| `./rabbit` | the 802.11 and WPA code, driven directly | Wi-Fi: association, ciphers, deauthentication, the four-way handshake |
| `./rabbit-vm` | the Metal VM and a real bytecode image | the whole rabbit: config, LEDs, ears, DNS, HTTP, its warren |

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

## A whole rabbit

`./rabbit-vm` starts where a real rabbit starts, at `main()`, and lets the
bytecode drive. That is most of the rabbit: `vnet.c` is 153 lines and does
nothing but pass Ethernet payloads up and down, so ARP, DHCP, DNS, HTTP, XMPP,
the choreographies and the configuration pages are all Metal. The emulator
supplies the hardware underneath — a radio that answers, LEDs and ears that
report what they were told, a 4096-byte configuration page that remembers, a
decoder that keeps what it was fed — and a small router behind the access
point.

```sh
python3 mkconf.py --ssid Terrier --security open   --server warren.example:8080/vl --ip 10.0.0.42 --gateway 10.0.0.1 -o open.eeprom
./run.sh --vm scenarios/vm/boot-open.txt --eeprom open.eeprom --warren <your-warren>
```

What the warren then sees, from a rabbit that does not exist:

```
GET /vl/locate.jsp?sn=0019db00beef&h=4&v=Git001
User-Agent: MTL   Host: warren.example
```

and what the emulator saw it do to get there:

```
[   4950 ms] gw: DNS warren.example -> 10.0.0.1
[   5000 ms] gw: the rabbit opened 10.0.0.42:6025 -> 127.0.0.1:8080
[  14200 ms] gw: nothing answers on port 5222, refusing the rabbit
```

That last line is the rabbit reading `xmpp_domain` out of the reply and dialling
XMPP. Point `--warren` at a real add-on or a real Terrier and the whole Violet
protocol runs. It is as useful in the other direction: a warren can be tested
against a rabbit's actual TCP stack, bugs and all, without owning a rabbit.

The configuration page is not guessed either. `mkconf.py` writes the layout
the boot code declares — a server URL, DHCP or a static address, the SSID, the
cipher, the PMK, and `0x47` at offset 207 to say the page means something. The
cipher byte matters more than it looks: it is what `OPnetAuth` compares the
scan against before it will dial at all, and the emulator prints both numbers:

```
OPnetAuth: mac=yes bssid=yes crypt=0x00 (&0xF0)=0x00 encrypt=0x00 ... -> calling netAuth
```

Other switches: `--no-eeprom` boots a rabbit straight out of the box,
`--audio-dir` keeps every clip it plays exactly as the warren served it, so an
MP3 stays an MP3 and "what did it actually say?" has an answer you can play,
and `--trace-net` logs every packet rather than only what became of it.

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
| `at <ms> rekey [<bssid>]` | the AP rotates the group key |
| `at <ms> ptk-rekey [<bssid>]` | the AP restarts the four-way handshake |
| `at <ms> reauth [<bssid>]` | the AP repeats its authentication reply |
| `at <ms> arp on` / `arp off` | the gateway answers ARP, or stops |
| `at <ms> server-drop` | the warren's TCP connections close |
| `at <ms> server-close` | …after announcing `</stream:stream>`, as a server shutting down does |
| `at <ms> expect <state>` | assert `ieee80211_state`: IDLE, SCAN, AUTH, ASSOC, EAPOL, RUN |
| `realtime` | pace the loop to the wall clock |
| `fetch <ms> <event>` | any of the above, `<ms>` after the rabbit starts downloading a sound |

`security` is one of `open`, `wep`, `wpa-tkip`, `wpa2-tkip`, `wpa2-ccmp`,
`wpa2-mixed`, `wpa3-sae`, `wpa2+wpa3`.

Time is virtual, not wall-clock. The firmware's only clock is `counter_timer`,
which the emulator writes itself, so `IEEE80211_RUN_TIMEOUT` — ten minutes on
the hardware — passes in a few milliseconds, and two runs of the same scenario
produce the same transcript on any machine.

Two directives exist because of that. `realtime` paces the loop to the wall
clock, which is needed whenever something outside the process has timers of
its own: the add-on schedules what it pushes with `threading.Timer` in wall
seconds — the connect jingle at +3.5 s — and without `realtime` a whole
two-minute run is over in five seconds, so nothing it sends ever arrives. And
`fetch <ms>` counts from the download rather than from power-on, because when
the warren decides to push a sound is the warren's business: "cut the
connection 300 ms into the download" is a thing a scenario can state, while
"cut it at 13,050 ms" is a guess that passes for the wrong reason the day the
timing moves.

## Testing a bytecode patch, not only a C one

Half of this firmware is Metal, so half the fixes are patches to `.mtl` files
and until `tools/mkbytecode.sh` existed there was no way to put one in front
of the emulator. It applies a patch, preprocesses and compiles, in a container
that can load `mtl_compiler` — which is a 32-bit x86 binary that a current
x86_64 host has no loader for, and which does not build from source with a
current gcc either.

```sh
./tools/mkbytecode.sh --sources <OpenJabNab>/bootcode   --compiler <nabgcc>/mtl/mtl_compiler   --patch ../../firmware/patch_wifi_reconnect.py --out with33.hybrid
make NABGCC=<tree> BYTECODE=$PWD/with33.hybrid vm
```

The first thing it proved: **PR #33 fixes issue #24 on WPA2**, which had sat
unverified for three weeks because verifying it meant a flash and a power
cycle. Deauthenticated at 60 s, back in RUN at 65.1 s, through a fresh scan, a
fresh `netAuth`, a fresh four-way handshake and a fresh group key.

Without that patch the same scenario never calls `netAuth` at all, and the
emulator says why in one line:

```
OPnetAuth: crypt=0x4a (&0xF0)=0x40 encrypt=0x00 key=NIL -> SKIPPED
OPnetAuth: crypt=0x4a (&0xF0)=0x40 encrypt=0x40 key=set -> calling netAuth
```

The stock Metal constants are 0-4 (`IEEE80211_CRYPT_WPA=3`) while the C side
compares against `crypt & 0xF0`, which is 0x00/0x10/0x20/0x40. No value of the
configuration byte can match on an encrypted network; only `0 == 0` does, which
is an open one. The check carries its author's own doubt —
`/* FIXME: 20170822: Really necessary ? */` — eleven years after the bytecode
it has to agree with.

## What it already answers

**WPA3 personal, SAE only: the rabbit does not try.** `ieee80211_input_mgt()`
walks the RSN element's AKM list looking for suite 2 (PSK); a WPA3-only AP
offers only suite 8 (SAE), so the scan result comes back
`IEEE80211_CRYPT_UNSUPPORTED` and the network is never a candidate.

Unimplemented, not impossible — the distinction matters and an earlier draft
of this file blurred it. Nothing in the silicon forbids SAE: it needs no
accelerator, only arithmetic, and this chip does arithmetic. What it would
cost, in order of difficulty:

- **P-256 field arithmetic.** WPA3 mandates SAE group 19, which is NIST
  P-256, so a scalar multiplication over a 256-bit prime field in software.
  Call it 6-12 KB of flash and a few hundred milliseconds per association on
  a 48 MHz ARM7TDMI with no multiplier worth the name. SAE-H2E rather than
  hunting-and-pecking avoids up to forty modular exponentiations to find the
  password element, and is worth having for that reason alone.
- **The watchdog.** A scalar multiplication that runs for 200 ms without
  `CLR_WDT` reboots the rabbit. The arithmetic has to be interruptible or
  the loop has to feed the dog, and this is the kind of detail that turns a
  correct implementation into a boot loop.
- **AES-CMAC, for 802.11w.** Management frame protection is mandatory with
  WPA3, and the RT2501 does CCMP for data in hardware but nothing for
  management frames. That is BIP in software, which needs an AES *encrypt*
  side — `../crypto/aes128.c` is decrypt-only, and `src/vapcrypto.c` here
  already has the other half.
- **Flash.** The tight one. `--gc-sections` freed 6-10 KB and the ROM-pages
  work exists precisely because this budget is nearly spent.

Where SAE would live is settled by the same observation that makes this
emulator work: the four-way handshake and the 802.11 management frames are C,
in `eapol.c` and `ieee80211.c`, so SAE belongs beside them and not in the
bytecode. Which means it can be written and measured here — cycle counts
included — before anything is flashed. That is the argument for building the
emulator first and the cryptography second.

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
`../crypto/aes128.c` and the `wpa2_gtk` modification exist for, and until now it
could only be checked by flashing a rabbit and watching whether broadcast
traffic ever arrived.

## What it does not do yet

**DHCP under `./rabbit-vm`.** The bytecode prints `[dhcp] startdhcp`, ARPs for
`0.0.0.0` and never transmits a BOOTP message, so it never gets a lease. The
gateway's DHCP server is written and answers a well-formed discovery; nothing
asks it. Use `mkconf.py --ip` for now, which is the more deterministic thing
for a test anyway.

One trap worth recording, since it cost an hour: the gateway used to answer
ARP for `0.0.0.0`. That told a rabbit whose gateway was still unset that the
gateway existed, and it then unicast its DHCP discovery with a destination
address of `0.0.0.0` for ever. Nobody owns `0.0.0.0`, and a gateway that is
polite about it does real damage.

**Everything that is not the radio or the network.** The microphone returns
silence, the RFID reader has a scenario-driven tag list and no protocol, and
nothing presses a button yet. The hooks are there (`hw_press`,
`hw_present_tag`); the scenario directives are not.

## Layout

```
src/wifi_main.c   ./rabbit: scan, choose, authenticate, watch, assert
src/vm_main.c     ./rabbit-vm: the VM, the bytecode, the main loop
src/vap.c         the virtual access point, in place of the USB radio
src/vapcrypto.c   AES-128 encryption and RFC 3394 key wrap, which an AP needs
                  and the rabbit does not; self-tested against the standards
src/gw.c          the network behind the AP: ARP, DHCP, DNS, ICMP, TCP, UDP
src/sim.c         the virtual clock and the scenario parser
stubs/hw.c        the EEPROM, the LEDs, the ears, the buttons, the tags
stubs/audio.c     the VLSI decoder, as a file you can listen to
patches/          the simulated register window and one interpreter trace
mkconf.py         writes the configuration page
mkbc.py           turns a bytecode image into the array the VM links
scenarios/        the Wi-Fi questions; scenarios/vm/ the whole-rabbit ones
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
