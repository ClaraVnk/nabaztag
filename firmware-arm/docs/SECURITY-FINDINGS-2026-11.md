# Security review — firmware 1.0.0.3 and runtime bytecode (2026-11)

A review of the rabbit firmware (Violet's original C and Metal, RedoXyde's
2015 port, and this project's additions) and the warren-served runtime
bytecode. Each fixed item was reproduced in the emulator before the fix and
shown fixed after; the deferred items are confirmed by code reading but left
for a dedicated batch for the reasons given.

## Fixed (proven in the emulator, committed)

1. **The adoption key was readable and overwritable by runtime bytecode.**
   After hand-over, `load` hid the key (conf 224..256) only for the filename
   `conf.bin`, but `sysLoad`/`sysSave` ignore the filename, and `save` had no
   guard at all. A warren's runtime bytecode could read the key back under
   any other name and forge the confirmed flag. Now guarded by offset and
   latch, every filename. Proof: a probe runtime, `NAB_FORCE_LATCH`.

2. **`flashFirmware` flashed anything.** It checked two public magic words,
   not a signature; a runtime bytecode that skipped `fwota.mtl` could brick
   or replace the firmware. `OPverifySig` now records the SHA-512 of each
   accepted image and `OPflashFirmware` flashes only a match. Only signed
   images flash (rollback kept). Proof: junk flash refused, signed flash via
   config-upload still reached.

3. **Probe-request SSID stack overflow (config mode).** In master mode the
   SSID element was copied into a 33-byte stack buffer with the element's own
   length (up to 255) — from any station in radio range. Bounded now. Proof:
   `inject-probe-ssid 255`, 255 bytes copied before, 32 after.

4. **DHCP reply accepted unsolicited.** `cbnetdhcp` took any reply whose
   chaddr held the rabbit's MAC, at any time, with a zero transaction id — one
   unsolicited ACK rewrote IP/gateway/DNS. Now a random id per request and a
   10 s window. Proof: `dhcp-rogue`, gateway hijacked before, dropped after.

5. **Config page handed back the Wi-Fi password.** The open config AP served
   the settings form with the password pre-filled. Rendered empty now, the
   key kept when the field is blank (as the SSID field already does).

6. **The in-session microphone had no stop.** It recorded until RT and
   resumed on its own when a dropped session reconnected. Stops after 60 s
   without a fresh RS, and the moment the session is gone.

7. **The .bin OTA buffer was the server's Content-Length, unbounded.** A huge
   size ran the VM out of memory (a reboot). Refused past the flash size now.

## Deferred (confirmed by code, left for a dedicated batch)

These are real but either touch a hot, recently-stabilised path where a rushed
change risks more than the bug, carry low practical impact, or are minor
robustness. They are grouped so a later batch can take them with their own
hardware validation, rather than enlarging the pre-flash lot.

- **No CCMP replay check on receive** (`ieee80211.c`/`rt2501usb.c`; the
  RXD's Iv/Eiv, labelled "for replay attack checking", are never read).
  Moderate. An attacker can replay a captured encrypted frame. Deferred: the
  fix is in the 802.11 receive path that WPA3/EAPOL only just stabilised, and
  TLS-over-XMPP already rejects a replayed payload for a confirmed rabbit.

- **DNS responses are unauthenticated** (`dns.mtl`: sequential 16-bit id,
  fixed source port, no check of the sender, and `parsemsg` runs before the
  id is matched). Moderate — a LAN host can forge an A record, as it could a
  DHCP reply (now fixed). Deferred: the fix (random id, sender check, parse
  only after a match) is more involved than DHCP's, over the SRV/cache/retry
  paths; the DHCP fix already protects which DNS server the rabbit trusts.

- **HTTP_NORMAL buffers a response without limit** (`http.mtl`). A server or
  a host on the path can exhaust the heap (a reboot). Deferred: the OTA `.bin`
  path, the one that pulls large bodies, is bounded now (fix 7); a general
  cap on `http.mtl` wants its own measurement.

- **Mixed-mode WPA2 group key refused** (`rsn_find_gtk` caps the GTK at 16
  bytes; a TKIP group key is 32, and message 3 swallows the failure →
  broadcast-deaf). Low — an edge config (CCMP pairwise, TKIP group) rare on
  modern APs. Deferred: the fix is in the EAPOL GTK install path.

- **Beacon RSN element double-advance** (`ieee80211.c`: the RSN case advances
  `frame_current` and the loop advances it again). Low — the elements after
  RSN are misparsed, but the SAE scenarios pass regardless, so the practical
  impact is limited to specific element orders. Deferred: removing it changes
  the scan parser and wants regression testing.

- **Minor robustness, Violet-original:** `sysCmp` clamps to the longer string
  (a small over-read in VM-bounded heap); `sysI2cRead` passes a NULL buffer to
  `read_i2c` (likely an unreachable path); ADPCM `StepIndex` is taken from the
  data unclamped (a step table over-read on a malicious resource). All low
  impact.

- **TLS nits, no exploit with a PSK:** a bad server Finished answers alert 50
  where 51 is specified; EncryptedExtensions is accepted zero or many times.

- **RFID tag lost during a TLS reconnect** — a tag presented in the exact
  window of a session reconnect is dropped, no retry. Minor.

A systematic audit of the original Violet code is under way separately and
will drive the next batch.
