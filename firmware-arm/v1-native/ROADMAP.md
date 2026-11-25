# Nabaztag v1: our firmware, the road ahead

Where the native firmware for the Nabaztag v1 (2005, PIC18LF6525 or
PIC18F6627, Prism 2.5 Wi-Fi card) stands and what comes next, in order.
Each step is proven in the v1 emulator first (`../emulator/tools/`), then
confirmed on Lazare, Clara's 1.4b, one PICkit write per proven fix.

## Done (2026-11-25)

- **Boot, loader, signed updates.** Our loader checks the application with
  HMAC-SHA-256 and a key per rabbit; frame 10 offers an update, the rabbit
  fetches `/vl/v1/fw`, writes slot A or B, falls back on a broken image.
  Violet's firmware can never be updated over the network (proven): ours
  needs the PICkit once.
- **Reaches the Terrier on a real v1.** Four causes found on Lazare through
  EEPROM reports read over ICSP (`../v1-icsp/card-report.py`): card strobes
  held one cycle instead of Violet's eight; card set-up differences; the
  DHCP discover asking for a broadcast reply, which an access point sending
  broadcasts at an OFDM rate never delivers to an 802.11b card.
- **Diagnostics without a serial cable.** The card report, the boot count
  and reset cause, a network report every 40 s (DHCP, DNS, TCP, HTTP, the
  card's events, the access point, our MAC), in the PIC's EEPROM.

## 1. The rabbit's own functions (proving now)

Frames from the Terrier, the rabbit's reports in `sd=` as Violet's v1:

| | Firmware | Terrier |
|---|---|---|
| Ears to a place (frame 11), one after the other | done | done (#499) |
| An ear that restarted the rabbit never driven again | done | — |
| LEDs (frame 12): the weather in Violet's belly colours | done | done |
| Sounds (frame 13): Faire parler, fetched then played | done | done |
| Asleep (frame 14): LEDs out for the night | done | done |
| Settings (frame 15): network and server, next boot | done | no form yet |
| Head: click, double click, long press (`sd=`) | done | click replays, double drops, long adopts |
| Ears turned by hand (`sd=8R0L`): lovers' ears | done | done |
| Volume wheel (AN0, three levels, as Violet reads it) | read, reported | — |

Then on Lazare: which ear is left (the dead right one is the one that gets
marked), the volume wheel's real effect, the sound's level and rate.

## 2. Everyday use (next)

- The ears' home: find the gap at the start of a turn at boot, as Violet's
  program does, instead of taking the start as the top.
- Sounds while asleep held back; a queue of more than one sound.
- The Terrier's page for a native v1: its own card (firmware version,
  update button, the diagnostic read back when it reaches the Terrier).
- Frame 15 from the page: change network or server without the Pi.
- A few days of Lazare on it before anyone else.

## 3. WPA2

The Prism 2.5 of Lazare runs station firmware 1.4.2: no WPA. The way:

1. **The card's firmware to 1.7.4** (Intersil's, kept off the public
   repository): written by our firmware through the card's download mode,
   proven in the emulator, tried first on Gengar's spare card, never on
   Lazare's before that.
2. **Host encryption**: the Prism 2.5 has no AES engine, but leaves the
   encryption to the host (as Linux's hostap driver did): the PIC does
   CCMP itself, about 40 ms for a poll, a few seconds for a sound.
3. **The WPA2 handshake in the PIC**: the four messages (HMAC-SHA1,
   PRF, the group key unwrapped with AES). The PMK (PBKDF2, 4096 rounds,
   about 30 s on the PIC) computed by the Terrier or the Pi and sent once
   by frame 15.
4. Proven in the emulator against hostapd (as the v2's WPA3 was), then on
   a real access point in WPA2-only mode.

## 4. Getting it to other owners

- **The case for it**, on the Terrier's v1 guide once Lazare has run it for
  a while: updates over the network for ever after one PICkit write, a
  faster start, a diagnostic instead of a silent blue belly, ears that
  cannot restart the rabbit, the Terrier's functions (weather, sounds,
  night, head), WPA2 when step 3 is done.
- **The install**: today a PICkit and the rabbit opened. A Raspberry Pi
  ICSP station (as the v2's JTAG station: a few wires, three commands)
  would take the PICkit away; its key generated with the owner, the
  rabbit's Violet firmware read and kept first so it can always go back.
- **The way back**: Violet's firmware and its configuration page are kept
  (pages 0-3 of the serial flash are never written), one ICSP write away.
