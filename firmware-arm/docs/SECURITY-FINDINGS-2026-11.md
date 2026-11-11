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

8. **Pre-association out-of-bounds read in the beacon parser (HIGH).** While
   scanning, `ieee80211_input_mgt` read a 16-bit suite count out of a beacon's
   RSN and vendor-WPA elements and walked that many 4-byte suites with no
   bound against the frame — a forged beacon with count 0x7fff read ~256 KB
   past it, from radio range, before any association. Bounded against
   frame_end now. Found by the Violet audit; proven with its ASan harness
   (overflow unbounded, clean bounded). Violet-original, in the uplg and
   RedoXyde ports too.

9. **Heap write overflow copying USB descriptors (HIGH).**
   `usbh_get_descriptor_all` copied each descriptor into a fixed-size struct
   using the device's own bLength (up to 255), overrunning the heap on write.
   Needs physical access to the USB port (a tampered dongle), but the one
   write corruption the audit found. Each copy is clamped to its destination.
   Proven with the ASan harness (WRITE overflow, clean clamped).

10. **DNS replies were easy to forge and could exhaust the heap.** Query ids
    were sequential (an off-path host could predict the next and race a forged
    A/SRV record — the SRV answer is the XMPP server), and `parsemsg` walked
    nbqu/nbans (up to 65535) by recursion. Random ids now, record counts
    clamped. Resolution still works end to end in the emulator.

11. **Small robustness fixes in Violet-original C (audit).** Four at once: the
    beacon RSN case advanced `frame_current` twice, so every element after an
    RSN one was misparsed (the extra advance removed, make check still green);
    `sysCmp` compared the longer remainder, not the shorter (an over-read);
    the ADPCM step index came from the data unclamped (a step-table over-read
    on a crafted resource); and `OPstrsub`/`OPstrcpy`/`OPload` tested
    `start+len>size`, which overflows a signed int — rephrased as
    `len>size-start`. All low impact, all proven to compile and pass make check.

12. **Mixed-mode WPA2 group key refused.** `rsn_find_gtk` capped the GTK at 16
    bytes, but a TKIP group key is 32; on a mixed AP (CCMP pairwise, TKIP
    group) message 3 swallowed the failure and the rabbit went broadcast-deaf.
    The destination is the 32-byte `gtk.b` union now. A CCMP key still writes
    the same 16 bytes, so a CCMP-only network is byte for byte unchanged.

13. **A DNS reply was parsed before its id matched.** `cbnetdns` parsed every
    packet on the query port before matching the id, so an unsolicited one
    still appended to the global SRV list (which feeds the XMPP connection)
    though its callback never ran. The parse is moved inside the id match.
    Resolution still works end to end.

## Deferred (confirmed by code, left for a dedicated batch)

Two findings touch a hot or unmeasured path where a rushed change risks more
than the bug. They are left for their own batch with its own hardware
validation, rather than enlarging the pre-flash lot.

- **No CCMP replay check on receive** (`ieee80211.c`/`rt2501usb.c`; the
  RXD's Iv/Eiv, labelled "for replay attack checking", are never read).
  Moderate. An attacker can replay a captured encrypted frame. Deferred: the
  fix is in the 802.11 receive path that WPA3/EAPOL only just stabilised, and
  TLS-over-XMPP already rejects a replayed payload for a confirmed rabbit.

- **HTTP_NORMAL buffers a response without limit** (`http.mtl`). A server or
  a host on the path can exhaust the heap (a reboot). Deferred: the OTA `.bin`
  path, the one that pulls large bodies, is bounded now (fix 7); a general cap
  on `http.mtl` needs a running byte count (a struct field) and its own
  measurement of the legitimate bytecode fetch (~110 KB).

## Documented, not fixed (dead, no impact, or minor UX)

- **`sysI2cRead` passes a NULL buffer to `read_i2c`** — dead code: the
  `i2cRead` opcode is never emitted by the runtime bytecode (grep of every
  `.mtl`), so the path is unreachable.

- **`eapol.c` reads a fixed 32 bytes of key data without checking
  `key_data_length`** — low: post-auth (the MIC is verified first) and the read
  stays inside the static receive buffer, not a true out-of-bounds.

- **TLS nits, no exploit with a PSK:** a bad server Finished answers alert 50
  where 51 is specified; EncryptedExtensions is accepted zero or many times.

- **RFID tag lost during a TLS reconnect** — a tag presented in the exact
  window of a session reconnect is dropped, no retry. Minor UX.

A systematic audit of the original Violet code ran separately and drove items
8, 9 and 11; a flash-space study (dead/unused code) is under way next.
