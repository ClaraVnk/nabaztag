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

`./rabbit` has two knobs for the entropy pool, both off by default so a
transcript stays reproducible: `--rssi-noise <seed>` wobbles every delivered
frame's RSSI by -1..+1 dB, deterministically, as the thermal noise on a real
link does; `--sram-seed <seed>` is what the retained external RAM holds at
power-up. `--eeprom-persist` lets a run write the config page back (the seed
the pool stores), which is how `tools/rng-boots.sh` makes one run the next
run's previous boot and checks that two boots on identical timers never draw
the same SAE scalar or adoption key. `make check` runs it after the scenarios.

The radio's key table is modelled, not just its presence: `rt2501_set_key()`
records the pairwise key the rabbit installs, the AP installs its own at
message 4 as hostapd does, and a protected frame in either direction is a
cipher error when the two differ. That is what shows a PTK installed at
message 2 instead of message 3 (`eapol_ptk_install`): the AP's message 3 of
a rekey, still under the old key, no longer decrypts, and the rekey never
finishes -- which is what a real access point saw and this emulator could
not. `wpa2-ptk-rekey.txt` asserts `expect-ptk-synced` since. Every frame is
also delivered in a buffer of exactly its own length, so under `make asan`
a parser that reads past the frame is a report rather than a data abort on
a rabbit (not the EAPOL MIC span: the driver copies the frame into its
static receive queue first, which is why `eapol-oversized-mic` reads the
firmware's own counter). `make asan` wants `ASAN_OPTIONS=detect_leaks=0`:
the firmware never frees the frames it sends, by design, and LeakSanitizer
would fail every run at exit. The 2026-11-08 audit's scenarios -- `eapol-forged-msg1`,
`wpa3-forged-msg1`, `eapol-oversized-mic`, `wpa2-msg3-retransmit`,
`wpa3-msg3-retransmit`, `wpa2-tkip` (refused now), `wpa3-downgrade` (refused
now, on purpose) -- each fail on a build without its step; `./run.sh --steps`
with the step left out is the control. `tools/sae-downgrade.sh` is the
power-cycle half of `wpa3-downgrade`: the "SAE required" record has to come
back from the configuration sector, and a page written for another
passphrase must not be locked by it. `make check` runs it too.

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
| `at <ms> restart [<bssid>]` | the AP restarts: one broadcast deauthentication (BIP-signed when 802.11w was negotiated), then it knows nobody |
| `at <ms> forget [<bssid>] [mute]` | the AP drops the station and says nothing; its next data frame gets a reason-7 deauthentication, unprotected -- or, with `mute`, nothing at all |
| `at <ms> drop-eapol <n>` | swallow the next `n` EAPOL frames |
| `at <ms> rekey [<bssid>]` | the AP rotates the group key |
| `at <ms> ptk-rekey [<bssid>]` | the AP restarts the four-way handshake |
| `at <ms> reauth [<bssid>]` | the AP repeats its authentication reply |
| `at <ms> arp on` / `arp off` | the gateway answers ARP, or stops |
| `at <ms> server-drop` | the warren's TCP connections close |
| `at <ms> server-close` | …after announcing `</stream:stream>`, as a server shutting down does |
| `at <ms> rfid <16 hex>` / `rfid none` | a tag (eight bytes) is on the reader until it is taken away (`./rabbit-vm`) |
| `at <ms> server-vanish` | the warren's sockets die with no FIN and no RST: the rabbit's segments on them are swallowed |
| `at <ms> expect <state>` | assert `ieee80211_state`: IDLE, SCAN, AUTH, ASSOC, EAPOL, RUN |
| `at <ms> expect-not <state>` | assert it is anything but that state |
| `at <ms> inject-plaintext [<bssid>] [eapol]` | an attacker's data frame in clear, with the AP's addresses: an ARP, or an EAPOL-Key message 1 with a high replay counter |
| `at <ms> expect-plain-dropped <n>` | assert the rabbit refused at least `n` plaintext data frames on its protected link (`ieee80211_plain_dropped`) |
| `at <ms> forged-msg1 [<bssid>]` | an EAPOL-Key message 1 the AP did not send, delivered as if the attacker were past `rx_protected`: a new ANonce, a replay counter 1000 ahead, and no message 3 ever |
| `at <ms> resend-msg3 [<bssid>]` | the AP retransmits message 3, as it does when message 4 was lost |
| `at <ms> eapol-oversized [<bssid>]` | a group key message whose `body_length` says 65535 in a 150-byte frame |
| `at <ms> expect-ptk-installs <n>` / `expect-group-installs <n>` | assert exactly `n` pairwise / group keys have been handed to the radio |
| `at <ms> expect-ptk-synced` | assert the pairwise key in the rabbit's radio is the one its AP transmits under |
| `at <ms> expect-eapol-badlen <n>` / `expect-tkip-refused <n>` / `expect-sae-refused <n>` | assert the firmware's own counters (`eapol_bad_length`, `ieee80211_tkip_refused`, `ieee80211_sae_refused`): at least `n`, and fail when the build has no such step |
| `at <ms> expect-reassoc <n>` | assert RUN has been reached at least `n` times |
| `at <ms> expect-xmpp-opens <n>` | assert at least `n` connections were opened to port 5222 (`./rabbit-vm`) |
| `realtime` | pace the loop to the wall clock |
| `fetch <ms> <event>` | any of the above, `<ms>` after the rabbit starts downloading a sound |

`security` is one of `open`, `wep`, `wpa-tkip`, `wpa2-tkip`, `wpa2-ccmp`,
`wpa2-mixed`, `wpa3-sae`, `wpa2+wpa3`.
`security <bssid> <sec>` is a `restart` with the new security: that is what
a controller does when an SSID is reprovisioned.

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
./tools/mkbytecode.sh --sources <OpenJabNab>/bootcode   --compiler <nabgcc>/mtl/mtl_compiler   --patch ../../bytecode/patch_wifi_reconnect.py --out with33.hybrid
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

**Why a rabbit stays "connected" to an access point that has forgotten it,
2026-11-08.** Two of the four needs-the-mains failures measured over JTAG
the day before were reproduced here on the first try, because both are one
line of `ieee80211.c`: in RUN the only thing that refreshes
`IEEE80211_RUN_TIMEOUT` is a beacon from the AP, and a beacon says the AP's
radio is on, not that it still knows us.

- `restart` is what hostapd does on its way down: one *broadcast*
  deauthenticate (`hostapd_deauth_all_stas`), BIP-signed when 802.11w was
  negotiated, then it knows nobody. The stock input path drops every
  deauthenticate whose addr1 is not the rabbit's own MAC, so the frame went
  by and the restarted AP's beacons kept the timeout alive -- which is what
  the UniFi WPA3->WPA2 reprovision of 2026-11-08 looked like from the JTAG
  line: W5 for ten minutes, the controller showing the rabbit gone.
- `forget` is the AP dropping the station and saying nothing. The next data
  frame from the rabbit gets a reason-7 deauthenticate, *unprotected*,
  because the AP has no keys for a stranger -- and under 802.11w that is the
  frame `ieee80211_mgmt_ok()` exists to throw away.

The `link_liveness` step (apply-mods.py) obeys the broadcast farewell,
keeps the time of the last *unicast* data frame from the AP, probes with
null-data after 60 s without one and gives up at 180 s, and believes a
refused deauthenticate only once that minute has passed. Measured here,
`./rabbit`, before and after:

```
                          without link_liveness          with
wpa2-ap-restart   RUN for ever, 1 association   IDLE at +50 ms, RUN again at +5 s
wpa3-ap-restart   RUN for ever, 1 association   IDLE at +50 ms (the signed broadcast frame), RUN at +5 s
wpa2-ap-forget    RUN for ever, 1 association   null-data at 65 s, reason 7 back, RUN again at 70 s
wpa3-ap-forget    RUN for ever, 1 association   same, through "an unprotected deauth after a minute -- believed"
wpa3-deauth-forged   passes                     passes: the forgery at 15 s of a live link is ignored
```

The virtual AP had to become more honest for this: an unsigned
deauthenticate on an association that negotiated 802.11w is now a forgery
the AP's own state ignores (before, the AP forgot the station too, which
made a forged frame indistinguishable from a real one to any liveness
check), and a null-data frame from a station it does not know gets reason
7, as hostapd gives.

**What this emulator cannot reproduce, and why: the rabbit that is
deauthenticated and never re-authenticates (2026-11-08, WPA2, UniFi
"Reconnect", W2 for 8+ minutes).** The virtual AP answers every
authentication frame it hears, so a rabbit sitting in AUTH with no reply
is a scenario this harness cannot write. Read offline, the paths after a
deauthenticate and after a power-on differ in little: `rt2501_auth` sets
the channel, the BSSID filter, the autoresponder rates and the key before
it transmits, exactly as at boot; what a deauthenticate leaves behind is
the cipher nibble of key 0 in SEC_CSR1 (the key itself is invalidated),
the group keys at indexes 1-3, and eapol's state, none of which touches an
unencrypted management frame. Which bytecode is driving matters more than
any of that: the bootloader's `boot_loop` rotates through `wifiscans`
every `BOOT_WIFITIMEOUT` (20 s) without rescanning, which is the
"alternating between the two BSSIDs" the JTAG line showed, and a soft
reboot is how a rabbit ends up there. So the step adds the counters the
next hardware run needs and `dcc_telemetry` prints them: auth frames sent
(`a`), auth frames received to us and the last status (`r`), beacons from
the target heard while waiting (`h`), bulk-out URBs refused (`Y`), bytecode
loads (`B`, 0 = bootloader), and the ASIC's own count of frames never
acknowledged / acknowledged (`F`, STA_CSR4/5). `../jtag/listen.sh` says how
to read them.

**Why no OTA ever installed, 2026-11-08.** A rabbit downloads the 215 KB
signed `.sim`, lights nose blue, nose green, one belly light blue, and
resets. `scenarios/ota/self-start.txt` runs the same path here, with the real
`fwota.mtl` and the real `naboot-stable.signed.sim`, and showed the same four
lights in the same order, then:

```
==ERROR: AddressSanitizer: negative-size-param: (size=-1728528)
    #1 nab_verify_sig ../crypto/nab_sig.c:33
    #2 interpGo /build/nabgcc/src/vm/vinterp.c:873
```

`verifySig` allocates payload+64 bytes of scratch after taking its operands'
heap indices. `getbinary_fw` has just left 107,704 two-byte strings behind, so
that allocation runs the collector, the compaction slides the payload down
over them, and the length read through the old index is garbage. On the host
that is a segfault; on the ARM7 a data abort, then the watchdog, then the
boot colours -- no red, nothing written. Two fixes, both measured here: the
bytecode calls `gc` before `verifySig` (works on the firmware already
flashed; the run then reaches white and `flashFirmware`), and the opcode
re-reads its operands after the allocation (`apply-mods.py`, `vinterp`;
the unfixed bytecode passes on a VM built with it).

What this emulator could not see, and qemu-arm could: the check itself is
247,618,040 instructions with the firmware's own flags, against a 2.09 s
watchdog fed once per opcode. The `verify_wdt` step makes the verifier feed
it 1,860 times. Until an image with that step is flashed, the bytecode fix
moves the reset from "inside a bad memcpy" to "2 s into a good verification",
and the belly cannot tell the two apart.

**Why `lean-wpa3` could not install and `lean` could, 2026-11-08.** The same
scenario with the 117,692-byte image: `#GC : sp=1567 hp=151833 used=74%`,
then "the firmware asked for a processor reset" -- the VM out of memory
(`vmem.c`, free space under `VMEM_GCTHRESHOLD` after a collection), before
`verifySig`, which is what the hardware's `nab_reset_reason = 1` said the
same afternoon. The hex path holds about six times the image at once: the
.sim is hex, `http.mtl` builds the body once for the status code and once
more for the callback and keeps the received segments, then the image and
the verifier's scratch come on top. `lean` (107,904 bytes) reached the gate
at `hp=167412 used=82%` with 5% of the heap to spare; nothing bigger fits.
The bytecode now asks for `/ota/<image>.signed.bin` -- the plain image as
bytes, the signature in an `X-Nab-Sig` header -- over `HTTP_STREAM` and
copies each segment into one buffer. At the gate, the same scenario, the
warren's own handler serving: 107,904 bytes `hp=57952 used=29%`, 117,692
`hp=60400 used=30%`, 126,976 (the flash's limit, a padded image signed with
a throwaway key, VM built with its public key) `hp=62720 used=31%`; every one
reaches white and `flashFirmware`, and the `-otafix` recovery variant
reaches yellow and the flash on the same path.

Two things about the emulator that run found. `--realtime` on `rabbit-vm`
never paced: `sim_load` zeroes the whole `sim` struct after the arguments
were parsed, so a 60 s run finished in under a second, and any server whose
answer takes more wall clock than the rabbit's `STD_NET_TIMEOUT` takes of
it -- about 28 ms for the warren's handler -- was always too late, which is
what the "first request of every run aborts after 908 bytes" looked like.
Fixed in `vm_main.c`; an OTA scenario against a real server wants the flag.

Whole-rabbit scenarios have a loop now: `tools/vm-check.sh`, and from
2026-11-08 a second binary for the scenarios whose header says
`bytecode: bootloader`:

```sh
tools/bootbc.py ../bin/lean-wpa3/Nab.elf -o obj/boot.bc
make NABGCC=<tree> BYTECODE=obj/boot.bc vm && cp rabbit-vm rabbit-vm-boot
make NABGCC=<tree> vm
tools/vm-check.sh --boot-vm ./rabbit-vm-boot
```

`scenarios/vm/xmpp-tls.txt` is run by hand, against a real Terrier on the
host with `--realtime` (its header says how): three boots of the
`lean-wpa3-tls` bootloader, the runtime bytecode fetched from the Terrier,
plain XMPP while the key is pending, TLS 1.3 PSK once the owner confirmed,
plain again for a reflashed rabbit with no key. See `../docs/ADOPTION-KEY.md`.
`tools/xmpp-tls-proof.sh` runs the first two boots against a Terrier
container, and `--strip` puts `tools/locate_strip.py` on the path: an
attacker who edits `locate.jsp`, dropping the `xmpp_tls` line
(`--strip-port N` rewrites the port instead). That is how the TLS policy
under the HMAC was measured, control and fix, before anything was deployed:
the production runtime, locate stripped, opens the plain port and the
Terrier refuses it; the runtime with the policy opens 5223 and binds over
TLS whatever locate said. The attacker tool also takes `--host` to
rewrite `xmpp_domain`: the handshake with a host that has no key fails,
and the gate reconnects over TLS, for ever.
`--scenario scenarios/vm/xmpp-rfid.txt` runs boot 2 with a tag presented to
the reader at 120 s: the production runtime sends plain `rfid.jsp`, the one
with `bytecode/patch_rfid_xmpp.py`, served by a Terrier that flips its
`xmpp-rfid:0`, sends the tag inside the TLS session. The ping server
`locate.jsp` names has no port, so `rfid.jsp` goes to port 80; rabbit-vm's
`--port80 <N>`, which the script passes, sends those connections to the
Terrier's HTTP port.
`--scenario scenarios/vm/xmpp-res.txt --speak-at 110 --speak <text>` has the
Terrier's `/speak` API make the rabbit talk 110 s into boot 2: with
`bytecode/patch_res_in_session.py` and a Terrier that flips `xmpp-res:0`, the
sound comes down the TLS session (`res:` lines, no port 80 open); with a
Terrier that does not, it is fetched from `/res/` over plain HTTP as before.

On the same day,
with the committed `bootcode.hybrid`, `boot-wpa2` and `wpa2-deauth` failed
(`OPnetAuth ... encrypt=0 -> SKIPPED`); with the bytecode
`bytecode/build-bytecode.sh` produces from the stock tree, they pass.

**The four-way handshake after SAE ran on WPA2's key schedule, 2026-11-08.**
The whole-rabbit scenario `scenarios/vm/wpa3-sae.txt` was said to end in
IDLE. Under a VM linked with the bootloader bytecode (`tools/bootbc.py` out
of a built `Nab.elf`) it did not: the rabbit hunted and pecked, the AP
accepted, the handshake ran, RUN at 4.95 s. The IDLE came from running it
under the warren's `.hybrid`, whose `wifiAuth` has no `netSaePw` -- no
password, no SAE, open authentication, status 13. `tools/vm-check.sh` now
takes `--boot-vm` and says SKIP rather than running that scenario under the
wrong bytecode.

What the pass was actually exercising was the question. The virtual AP
answered a SAE authentication with EAPOL-Key descriptor version 2 and
checked nothing about message 2 beyond its MIC -- and sent message 3 even
when that failed. hostapd does none of that: with AKM 00-0F-AC:8 the
descriptor version is 0, the PTK is KDF-SHA-256, the MIC is HMAC-SHA-256
cut to 128 bits (802.11-2020 12.7.3, Tables 12-10 and 12-11), message 2 is
discarded if its MIC does not verify, and its RSN IE must be the
association request's byte for byte or the station is disconnected with
reason 17. Made to behave that way, the same scenario stopped here:

```
EAPOL 2/4 received, PTK derived (..., descriptor version 0)
the RSN IE in 2/4 is not the one from the association request
  association: 30140100000fac040100000fac040100000fac08 8000
  message 2:   30140100000fac040100000fac040100000fac02 0000
```

The supplicant knew versions 1 and 2 only, so after a correct SAE exchange
it signed message 2 with HMAC-SHA-1 over a SHA-1-derived PTK and said PSK
and no MFPC in an IE that had said SAE and MFPC at association. A real
WPA3 access point could never have finished that handshake, whatever the
SAE exchange before it did. `wpa3_eapol_akm` in `apply-mods.py` is the
fix, keyed on `ieee80211_authmode == IEEE80211_AUTH_SAE`; 88 bytes of
flash. The same stricter AP also found `eapol_msg2_ie`: message 2 named
the pairwise cipher as the group cipher, which on a mixed-mode network is
a disconnect from any hostapd (`scenarios/wpa2-mixed-gtk.txt`). The AP now
also sends its SAE confirm only after the station's, which is hostapd's
order in an infrastructure BSS; both at once is the mesh behaviour.

`scenarios/wpa3-transition-sae.txt` boots with a passphrase on a
transition-mode AP, so SAE runs there too: a rabbit that joins a
WPA2/WPA3 SSID is not evidence that its SAE works unless it joined that
way.

**SAE hash-to-element, 2026-11-08.** An access point may insist on it
(hostapd's `sae_pwe=1`), and hostapd does not refuse a hunted-and-pecked
commit on such a network: `handle_auth_sae()` drops it without a reply, so
the station times out in AUTH -- which is what `wpa3-sae-h2e-only.txt`
asserted until today and what Nabazprout did on a WPA3-only UniFi whose
controller shows no `sae_pwe`. The firmware now reads bit 5 of the RSN
Extension element (ID 244) in the beacon and derives the password element
by H2E when it is set (`wpa3_h2e` in `apply-mods.py`, `sae_derive_pt` and
`sae_commit_pt` in `../crypto/sae.c`), hunting and pecking otherwise --
wpa_supplicant's `sae_pwe=2`. With it the commit carries status 126, the
association request and message 2 carry the RSNXE, and an anti-clogging
token rides in a container element after the commit element.

The virtual AP does not run the rabbit's H2E back at it. `src/vaph2e.c`
derives PT and the PWE from the standard on its own arithmetic (four-limb
integers, affine points, nothing shared but SHA-256), and both it and the
firmware's `p256_sswu` are checked against RFC 9380 J.1.1 and IEEE
802.11-2020 Annex J.10 before any scenario runs (`../tests/vectors_h2e.h`,
generated by a Python oracle that recomputes every published number). On
every H2E exchange the AP compares its PWE with the station library's. The
AP also does what hostapd does around it: `h2e-only` drops status-0
commits silently, `h2e` takes both but refuses association with status 1
when the RSNXE claims H2E and the commit did not use it (`check_assoc_ies`),
and message 2 must repeat the RSNXE as well as the RSN IE. `expect-sae
h2e|hnp` asserts which derivation the AP saw, because RUN alone would be
satisfied by either. Scenarios: `wpa3-sae-h2e-only`, `wpa3-sae-h2e`,
`wpa3-sae-h2e-token`, `wpa3-transition-h2e` (the selection picking H2E on a
WPA2/WPA3 SSID), `wpa3-sae` (no RSNXE, so hunting and pecking) and the
whole-rabbit `vm/wpa3-sae-h2e`. The firmware without the step fails the
first four exactly as expected: AUTH for ever on the H2E-only AP, hunting
and pecking on the others.

What it costs, counted under qemu-arm with the firmware's flags
(`../tools/cost`): PT 43.7 M instructions, once per (SSID, password) and
then kept in 73 bytes of `.bss`; the commit from that PT 107.6 M (two
scalar multiplications); the hunting-and-pecking commit it replaces
248.9 M. At 1.5-4 cycles per instruction on the 32.768 MHz ARM7TDMI that
is 2.0-5.3 s for PT and 4.9-13.1 s per commit, against 11.4-30.4 s before.
The longest stretch between two feeds of the watchdog is 0.61 M
instructions (0.03-0.07 s) against a 2.09 s window -- it was 5.1 M, in
the point addition at the end of PT, until `p256_add` took the tick too. H2E costs the `lean-wpa3`
image 2 464 bytes; on top of the AES-CMAC handshake fix (#82) it is
120 188 bytes, 6 788 spare, and preflight passes.

**WPA3 personal, SAE only: it associates, as of 2026-10-27 — and it did not
on real hardware until 2026-11-07, which is worth more than the pass.** The
scenario passed because this harness called `ieee80211_sae_password()` itself,
"doing what netPmk does". On a rabbit, netPmk runs when the password is typed,
not when it boots, so `ieee80211_sae_pw` was always empty and SAE never
started: against a WPA3-only UniFi the rabbit never authenticated at all, and
against a transition-mode one it associated over PSK and looked fine. A
harness that supplies what the firmware cannot had hidden that for ten days.

The harness now reads the passphrase out of the config page it boots from,
offset 92, exactly as the patched `wifiAuth` does — so the WPA3 scenarios
carry a `conf` line, and `scenarios/wpa3-sae-no-stored-password.txt` boots
without one and asserts that nothing associates. If that scenario ever
reaches `RUN`, the others have gone back to proving nothing.

`scenarios/wpa3-sae.txt` spent months asserting `IDLE` because
`ieee80211_input_mgt()` walked the RSN element's AKM list looking for suite 2
(PSK), found only suite 8 (SAE), and marked the network
`IEEE80211_CRYPT_UNSUPPORTED`. Build with `wpa3_sae` in `MOD_STEPS` (the
default now) and the whole chain runs: the scan reports `0x4a`, the rabbit
sends an authentication frame with algorithm 3 and its SAE commit, the AP
answers with its own commit and confirm, the rabbit verifies and hands the
derived PMK to `eapol.c`, which runs the ordinary four-way handshake. The
RT2573 still does CCMP for data in hardware.

Both halves of SAE are our own code, so this proves the *integration* and
not interoperability: frame layout, sequence numbers, the channel being
programmed before the commit goes out, the PMK reaching eapol, the
association completing. A real access point is the test that settles the
rest.

Three things that made it work, none of them cryptography:

- **The password never reaches the driver.** The bytecode runs PBKDF2 itself
  and hands C a 32-byte PMK. SAE needs the password, which passes through C
  exactly once, in `netPmk()`.
- **A SAE flag cannot go in `scan_result.encryption`.** That byte goes up to
  the VM, and `OPnetAuth` only calls `netAuth` when `(crypt & 0xF0)` matches
  the stored config byte. SAE-capable BSSIDs go in a driver-side cache
  instead, and the VM keeps seeing `0x4a`.
- **Returning early from `rt2501_auth` means taking over what follows it.**
  It writes the AP rates to the autoresponder and calls
  `rt2501_switch_channel()` *after* the point where SAE hooks in. Skipping
  those sent six commits while the radio was still hopping through the scan
  — all six counted under "transmitted into the void", the rabbit stuck in
  `AUTH`, and nothing in the log said why.

What it cost, measured on the ARM7TDMI: the `lean-wpa3` image is 116 996
bytes against `lean`'s 107 288, so **9 708 bytes**, with 9 980 still spare.
(114 444 of those were the first build; the remaining 2 552 are BIP, the
`netSaePw` opcode and the rest of what it took to make this work on a rabbit
rather than in here.)

Still missing, and the reason this is a separate build mode rather than part
of `lean`: BIP validation. The RSN IE advertises MFPC, which is what an AP
with MFPR set requires of us, so association works — but an unprotected
deauthenticate is still honoured, which is the attack 802.11w exists to
stop. `../crypto/cmac.c`'s `bip_cmac_128` is written and tested; what is
missing is pulling the IGTK out of the four-way handshake and checking the
MMIE. Until that lands this is WPA3's key exchange on WPA2's
management-frame security.

The original estimate of what it would cost, kept because it was close:

- **P-256 field arithmetic.** ~~Call it 6-12 KB of flash~~ — **done and
  measured, 2026-10-27**: `../crypto/p256.c`, **5 452 bytes** of ARM7TDMI flash
  (5 292 text + 160 rodata) and 132 bytes of `.bss`, against 19 688 bytes
  spare in the `lean` image. It passes 14 OpenSSL vectors and a set of
  algebraic identities that need no external data, under ASan and UBSan:
  `make -C ../tests`. Montgomery multiplication rather than the usual
  special-prime reduction — maybe half the speed, and far harder to get
  subtly wrong — and no precomputed tables, which is what keeps it this
  small. SAE-H2E rather than hunting-and-pecking still matters: it avoids up
  to forty modular exponentiations to find the password element.
  `p256_decompress()` is there for it already.
- **The watchdog.** A scalar multiplication that runs for 200 ms without
  `CLR_WDT` reboots the rabbit. Handled by construction rather than left to
  the caller: every long loop in `p256.c` takes a `tick` callback and calls
  it 272 times per scalar multiplication, which is more than once per bit of
  the scalar. The host figure is 0.7-2 ms depending on optimisation; the
  honest statement about the rabbit is that nobody has timed it there yet,
  and the arithmetic-heavy factor between a GHz out-of-order host with
  64-bit multiplies and a 48 MHz ARM7TDMI is large enough that an estimate
  would be a guess dressed up as a number. What is settled is that it cannot
  silently exceed the 2.09 s window.
- **AES-CMAC, for 802.11w.** Management frame protection is mandatory with
  WPA3, and the RT2501 does CCMP for data in hardware but nothing for
  management frames. That is BIP in software, which needs an AES *encrypt*
  side — `../crypto/aes128.c` is decrypt-only, and `src/vapcrypto.c` here
  already has the other half. Now the largest piece still missing.
- **Flash.** It was the tight one. `--gc-sections` freed 6-10 KB, dropping
  libc's `malloc` with `no_libc_malloc` freed another 8.4 KB on 2026-10-27,
  and the `lean` image now has **19 688 bytes spare** with P-256's 5 452
  already accounted for. What is left to pay for is SHA-256 and HMAC (this
  tree has SHA-1 only), AES-CMAC for 802.11w, and the SAE state machine
  itself. It is no longer the thing that decides whether this is possible.

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

**A whole rabbit fetches a sound and plays it.** The warren pushes an `ST`,
the rabbit resolves the resource, downloads it, rebinds to its `streaming`
resource and feeds the decoder; `--audio-dir` keeps the clip, and it is the
file the warren served, bit for bit. That is `hal/audio.c`'s own
`play_check()` running in the emulator, FIFO and `SYS_CBPLAY` callback
included, so "did it actually say anything?" is a question with an answer you
can play.

**The WPA2 group key arrives and is installed.** The AP wraps a GTK with the
KEK into message 3 and the rabbit logs `GTK installed`. That is the path
`../crypto/aes128.c` and the `wpa2_gtk` modification exist for, and until now it
could only be checked by flashing a rabbit and watching whether broadcast
traffic ever arrived.

## Config mode, and a phone

Hold the head button at power-on and the bootloader does not join a network:
it becomes one, open, on 192.168.0.1, and serves its configuration pages --
among them the upgrade page that takes a `.sim`. `src/phone.c` is the phone
that joins it: authentication, association, then a browser's multipart POST
of the file to `/c`, cut into the 800-byte segments the rabbit's own TCP
window allows. `scenarios/vm/config-upload.txt` runs it, and
`tools/config-upload.sh` builds the VM against a given image's bootloader
(lifted from its `Nab.elf` with `tools/bootbc.py`) and uploads a given
`.sim`:

```sh
tools/config-upload.sh ../bin/lean/Nab.elf ../bin/naboot-1.0.0.0/firmware0.0.0.13.signed.sim
```

The transcript then shows the heap at the signature gate (`verifySig
gate: ... hp=...`), the verdict, and whether `flashFirmware` was reached;
`resets asked` in the summary says whether the VM ran out of memory on the
way, as the runtime OTA's hex path does for images of 117 KB and up. The
config page does not: see the scenario's header for the figures.

## What it does not do yet

**DHCP under `./rabbit-vm`.** The rabbit ARPs for `0.0.0.0` for ever and never
gets a lease. The gateway's DHCP server is written and answers a well-formed
discovery; nothing asks it. Use `mkconf.py --ip` for now, which is the more
deterministic thing for a test anyway.

The cause is now known, and it is in the emulator rather than the firmware.
The bytecode calls `startdhcp` twice: once while it is reading its
configuration, before `gostation`, and again from `gostationW` once the radio
reports `RT2501_S_CONNECTED`. The first call hands a DISCOVER to
`rt2501_send`, which refuses it -- correctly, since `ieee80211_state` is
neither `EAPOL` nor `RUN` yet -- and the hybrid's guarded `startdhcp` then
declines every later call, so the one that would have worked never transmits:

```
[dhcp] startdhcp
[dhcp] really do startdhcp          <- before the scan, refused by rt2501_send
...
-------------dhcp                   <- associated, and now it is too late
[dhcp] startdhcp                    <- no "really do"
##timeout on configwait
```

On the hardware that first call happens while the USB radio is still being
enumerated, which takes real seconds; here the bytecode's `main()` returns at
virtual time 0, so both calls land inside the same instant. Making time pass
while the interpreter works is the fix, and it is not written yet.

One trap worth recording, since it cost an hour: the gateway used to answer
ARP for `0.0.0.0`. That told a rabbit whose gateway was still unset that the
gateway existed, and it then unicast its DHCP discovery with a destination
address of `0.0.0.0` for ever. Nobody owns `0.0.0.0`, and a gateway that is
polite about it does real damage.

**A sound and a capture no longer cancel each other in silence.** With the
`audio_halfduplex` step the speaker wins: a sound stops the capture and plays,
and both starts report what happened instead of pushing a literal 0. Drop the
step and the old behaviour comes back, which is how the before and the after
were measured:

```
without   [  58700 ms] audio: play_start refused, the microphone has the decoder
with      [  58700 ms] audio: stopping the capture, the speaker wins
          [  58700 ms] audio: end of clip 2, 61665 bytes
```

**What this emulator could not tell me, and it cost a flash.** The
`audio_ampli` step takes the amplifier out of `set_vlsi_volume()` and leaves it
to `play_start`/`play_stop`. Here that looks right, and it is measurable:

```
without   [      0 ms] audio: amplifier on        (and never off, nothing played)
with      [  15000 ms] audio: amplifier on        (a sound arrives)
          [  15000 ms] audio: amplifier off
```

On a real rabbit, 2026-10-23, that build plays a sound perfectly on the wire —
streaming resource bound, clip fetched, 5.5 s spent on it, back to idle — and
**nothing is heard**. The step is applied in no mode now; see its docstring in
`apply-mods.py`.

The emulator models `vlsi_ampli()` as a log line, so it cannot distinguish a
sound that is heard from a sound played into a dead amplifier — which is
exactly the failure that change caused. Worth stating plainly, because every
other finding on this page is one the emulator *can* settle: anything whose
only observable effect is sound pressure in a room needs a rabbit and a pair
of ears, and a green run here means nothing about it.

**The microphone records a pattern, not sound.** `rec_check()` hands the
firmware one 256-byte IMA-ADPCM block every 63 ms of sim time while it
records -- a zero header, then 0xF7 bytes, loud enough for the warren's
loudness gate -- so a capture can be counted and checked at the far end. What the capture *costs* is modelled,
and that turned out to be the part that matters: `play_start()` refuses to
play anything while a capture is running, and says so in the transcript
(`audio: play_start refused, the microphone has the decoder`), which is how
issue #23's finding 5 was measured. `xmpp-tls-proof.sh --api listen '{"seconds":3}'`
uses it to prove the microphone inside the TLS session.

**The rest of the body.** The RFID reader has a scenario-driven tag list
(`at <ms> rfid <16 hex>`, `rfid none`) and no protocol: `rfid_read` returns
zeros. The head button is `at <ms> button down` and `at <ms> button up`
(`hw_press`): held past the runtime's threshold it is a long click.

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
