# A Nabaztag v1 that is not a Nabaztag v1

Violet's own 2005 firmware for the first Nabaztag, run on a build machine:
a PIC18 core, the board around it, and the network behind it. No rabbit, no
PICkit, no WEP access point.

The v1 is a PIC18F6627 with no JTAG and no way to update itself (its code
has no `TBLWT`, and never touches `EECON1`). Its firmware was read over ICSP
from a real rabbit and published by [Newbaztag](https://github.com/trcwm/Newbaztag)
(MIT); it is vendored in `../../vendor/newbaztag`, and `make hex` checks its
SHA-256 before using it.

```sh
make nabv1 hex
./nabv1 --hex nabaztag.hex --seconds 30 --warren 127.0.0.1 --port80 8080
```

```
[     116 ms] uart: W
[     392 ms] card: COR <- 01 (I/O mode)
[     593 ms] card: initialize
[     596 ms] card: enable, port type 1 (BSS), SSID ""
[    1080 ms] uart: .WLAN04Terrier
[    2087 ms] gw: DHCP offer — 10.0.0.42, mask 255.255.255.0, gateway and DNS 10.0.0.1
[    4141 ms] gw: DNS p.nabaztag.com -> 10.0.0.1
[    4793 ms] gw: the rabbit opened 10.0.0.42:1035 -> 127.0.0.1:80
GET /vl/FR/p3.jsp?sn=0019DB00BEEF&ex=000000000000&v=20&st=00&tc=00000000 HTTP/1.0
User-Agent: VIOLET
```

## What is emulated, and from what

| Part | File | Read from |
|---|---|---|
| PIC18 core, standard set (`CONFIG4L` 0x80: XINST off) | `pic18.c` | DS39646C ch. 26 |
| Ports, timers, UART (the firmware's log, 9600), ADC, MSSP | `board.c` | DS39646C; Newbaztag `../../vendor/newbaztag/doc/components.md` |
| AT45DB161 flash (the program frame `05` is written there) | `spi.c` | Atmel datasheet, tables 1–2 |
| OKI ML2870A (register pairs logged), TLC5922 LEDs | `spi.c` | Newbaztag `hardware.c` |
| Prism 2 PCMCIA card, I/O mode: COR, commands, RIDs, FIDs, BAPs | `prism.c` | the firmware's bus routines (`0x3E7C`–`0x3F16`) |
| Ear motors and slot sensors: 17 holes, low 190 ms, high 240 ms, a 1.4 s gap | `ears.c` | Newbaztag, measured on a scope |
| Network behind the card | `../src/gw.c` | shared with the tag's emulator |

The firmware never enables an interrupt, so the core has none. The card
does 802.11 and WEP itself, so the model has no radio: ENABLE reports the
link up, and the card's 802.3 frames (LLC/SNAP at offset 60) go to `gw.c`.
Since the native firmware's loader (2026-11-24): the `RESET` instruction
resets the core (ports inputs, PC 0, RAM kept; "reset: RESET instruction")
instead of halting it, the data EEPROM is readable through EECON1.RD and
takes its content from the hex's 0xF00000 records (the loader's key), and
the flash self-write models the 6525's 8-byte blocks under `--pic 6525`.

Read in the process, against what the sources said:

- The ear motors are RF0/RF1 and RF2/RF3, one direction per pin (not RD1:
  PORTD is the card's data bus). TMR0 counts ear 0's holes on T0CKI, TMR3
  ear 1's on T13CKI.
- The poll interval with frame `03` = 1 is about 11.8 s.
- `MOTORGET` counts holes since power-on: it never wraps at a turn and a
  hand moves it too.
- There is no ear interrupt in the VM (Violet's manual §4.4): a v1 reports
  its ears, and its long press, only if the program does it with `SEND`.

## Options

| | |
|---|---|
| `--hex FILE` | the firmware (`make hex`) |
| `--flash-chip 081` | an AT45DB081B (264-byte pages, 9-bit addresses) for the serial flash, as on Clara's 1.4b rabbit (`../../v1-icsp/dumps/violet-v1-1.4b.hex`); the default is the 2.0b board's AT45DB161. On the 161 the 1.4b firmware lost the program it had just written and never polled again |
| `--seconds N` | virtual time to run; the clock is the PIC's cycle count, so a run is reproducible |
| `--warren HOST`, `--port80 PORT` | where the rabbit's TCP connections go |
| `--mac 0019db00beef` | the card's address, which is the rabbit's `sn` |
| `--ssid NAME` | the network the card reports |
| `--hand EAR SLOTS AT` | a hand turns ear 0 or 1 by SLOTS holes at AT seconds |
| `--press AT HOLD` | the head button, pressed at AT seconds for HOLD seconds |
| `--stall EAR` | ear 0 or 1 is stalled: its motor runs, its wheel never turns (Lazare's right ear, 2026-11-24) |
| `--flash FILE` | keep the serial flash (configuration page, program) across runs |
| `--pic 6525` | Lazare's PIC18LF6525: 48 KB, 8-byte flash write blocks (DS39612); the default is the 2.0b board's PIC18F6627, 96 KB, 64-byte rows (DS39646). The row or block written is the one TBLPTR points at when WR is set, as on the part |
| `--pic-flash FILE` | keep the PIC's own flash across runs: the hex is loaded, then FILE laid over it when it exists, and the flash saved at exit. With `--seconds` cut short, a run is a power cut in the middle of whatever the loader was writing (`../tools/v1-loader-proof.sh`) |
| `--trace-flash`, `--trace-oki` | every PIC flash erase and write; the OKI's registers with every byte of a data burst |
| `--trace-bus`, `--trace-pins`, `--trace-net`, `--hot` | what the card, the pins, the gateway and the PC did |
| `--server-vanish AT`, `--server-close AT` | at AT seconds the server dies without a FIN, or closes its connections |
| `--link-down AT SECONDS` | the access point is gone from AT for SECONDS (0: for good); the card reports link status 2, then 1 when it is back |
| `--dns-mute UNTIL` | no DNS answer before UNTIL seconds (0: never) |

The last three are what `../tools/v1-study.sh` uses to see how Violet's
firmware lives through a broken network (`../../v1-icsp/IMPROVEMENTS.md`,
2026-11-24): a card re-initialisation after about 52 s on every failure
path, DNS asked once per power-on.

## Proofs

`../tools/v1-proof.sh` (from `firmware-arm/emulator`, on a machine with podman):

- alone: the rabbit joins, takes DHCP, resolves `p.nabaztag.com` and sends
  its poll;
- `--terrier <checkout>`: the Terrier's own `p3.jsp` handler answers
  (tools/v1_warren.py in the Terrier). The program is taken (`tc` becomes
  its id), the ears go to 5/10 then 2/3 and stop, a hand on the right ear is
  reported as `sd=8603`, a 3-second press as `sd=0004`, and two v1 of one
  owner in "amoureux" follow each other.

`../tools/v1-native-proof.sh`: our own firmware for the v1
(`../../v1-native`, SDCC) on Lazare's board, built and run here: joins,
DHCP, DNS, polls `../tools/v1_native_warren.py` every 10 s, moves the ears,
writes its configuration page in the serial flash; PASS/FAIL with the
image's flash and RAM use.

`../tools/v1-loader-proof.sh`: its loader (v1-native DESIGN.md section 7):
the first boot seeds slot A, an offered update is downloaded, installed and
polls, a bad signature is refused, twelve power cuts at random points of
the update each end on a polling image, a corrupt PIC row is repaired from
the slot, a corrupt slot falls back to the other, an image that never feeds
the watchdog is abandoned after five boots.

`../tools/v1-audio-proof.sh`: its OKI path against Violet's 1.4b playing
the same 16 kHz tone: the start registers, the burst lengths and every data
byte equal.

The warrens of these proofs run in a container on the host network, like
the emulator: on a Mac the podman machine's network is not the Mac's.

## The 1.4b firmware and the watchdog

Lazare's firmware (`../../v1-icsp/dumps/violet-v1-1.4b.hex`, a PIC18LF6525)
runs with `--hex` and `--flash-chip 081`. Its configuration keeps the
watchdog on (CONFIG2H 0F, ~512 ms) and the brown-out reset on (CONFIG2L 0A);
the 2.0b dump has both off. The emulator now resets the PIC when the
watchdog is on and no CLRWDT came in its period, and logs it: the 1.4b
firmware never let it expire, ears turning included. The brown-out is
electrical and not modelled: on the rabbit, two ear motors started in the
same step restarted it (2026-11-24); the Terrier's program now starts one
at a time, and the emulator shows the left ear starting 2 ms after the
right one stops:

```sh
podman run --rm --network host -v firmware-arm/v1-icsp/dumps:/d:ro,Z nabaztag-v1 \
    --warren 127.0.0.1 --port80 28080 --hex /d/violet-v1-1.4b.hex --flash-chip 081 --seconds 120
```

