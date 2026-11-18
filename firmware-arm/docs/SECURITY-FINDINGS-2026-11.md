# Security review — firmware 1.0.0.3 and runtime bytecode (2026-11)

A review of the rabbit firmware (Violet's original C and Metal, RedoXyde's
2015 port, and this project's additions) and the warren-served runtime
bytecode. Each fixed item was reproduced in the emulator before the fix and
shown fixed after. One finding is instrumented rather than fixed in this
version, for the reason given under its heading.

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

14. **HTTP_NORMAL buffered an answer without limit, in both copies of the
    HTTP client.** The bootloader's (`boot.0.0.0.13.mtl`, bc.jsp and kx.jsp)
    and the runtime's (`http.mtl`, locate.jsp and the other `.jsp`) kept
    every segment until the connection closed. A server, or a host on the
    path of these plain-HTTP requests, ran the VM out of memory -- a reset,
    after which the radio stays asleep. Reproduced with the gateway's
    `http-big`: a megabyte resets the bootloader at 768 KB received and the
    runtime at 666 KB; a complete answer does it from 220,000 bytes
    (bootloader) and 200,000 (runtime, answering locate.jsp), while 180,000
    and 100,000 do not. Each request now counts what it receives and is
    dropped past a ceiling: connection closed, callback handed the nil it
    gets for any failed request, so the caller retries as it already did.
    Bootloader: 163,840 bytes (`http_cap`, apply-mods.py) against a largest
    real answer of 112,349 (bc.jsp, a 112,232-byte runtime) -- 46% of room
    to grow, under the 180,000 the heap was measured to hold. Runtime:
    16,384 bytes (`bytecode/patch_http_cap.py`) against a largest real
    answer of 178 (locate.jsp); the hex `.sim` OTA, the one large
    HTTP_NORMAL download, carries its own ceiling of 218,018 (the largest
    `.sim` that path has installed is 215,970, 216,175 with headers).
    Proof: `../emulator/scenarios/vm/http-big-boot.txt` and
    `../emulator/scenarios/http/big-runtime.txt` -- the rabbit hangs up at 165,259 and 18,259 bytes,
    six and thirty-eight times in two minutes, no reset; a whole boot
    (kx.jsp, bc.jsp, locate.jsp, XMPP dialled) and the hex OTA to the
    signature gate are unchanged. Cost: 128 bytes of flash (111,716 to
    111,844), 181 of runtime bytecode. Left as it was: the two TLS fetches,
    whose peer holds the adoption key.

15. **The header block of a streamed answer was accumulated without limit.**
    HTTP_STREAM (sounds fetched by URL, the `.bin` OTA) keeps nothing after
    the headers, but it kept every byte until their blank line, a strcat per
    segment. A response whose headers never end is the same reset as item
    14. Reproduced with the gateway's `http-bighead` on the request for a
    sound (the adoption announcement, fetched over plain HTTP): reset twelve
    seconds in. The block now stops at 8,192 bytes -- connection closed, the
    stream's callback handed the nil that ends it. Runtime only
    (`bytecode/patch_http_cap.py`); the bootloader streams nothing on its
    own.

## Measured in 1.1.0.0, refusal to be turned on after

- **No CCMP replay check on receive.** A captured encrypted frame sent
  again was decrypted again and handed up again
  (`../emulator/scenarios/ccmp-replay.txt` on a build without the step:
  one frame, two deliveries). The check itself is short -- per key, the last packet
  number taken; refuse what is not above it -- but it has to read the
  packet number from the RXD's Iv and Eiv words, and what the RT2571W
  writes there on this board has never been read. Linux's rt73usb says:
  the stripped CCMP header, PN = Eiv<<16 | Iv&0xffff, key id = Iv>>30. If
  that is wrong, a refusal in the receive path drops every frame after
  the first, and a deaf rabbit is worse than this finding. So 1.1.0.0
  **counts and refuses nothing** (`ccmp_replay_count`): frames examined,
  frames whose number did not increase (unicast and group apart), the AP's
  own retransmissions apart again (Retry bit), and the two raw words of
  the last frame; the last number is forgotten whenever a key is installed
  (PTK, GTK, rekeys -- not a retransmitted message 3, which installs
  nothing). They leave on the telemetry line as `N.. p../../.. V../..`
  (`../jtag/listen.sh`, `../jtag/ccmp-pn.py`). In the emulator, whose AP fills
  the words as rt73usb describes: ordinary traffic 0, a replay counted,
  a retransmission counted apart, no false positive across two group
  rekeys, a pairwise rekey and a retransmitted message 3, every frame
  still delivered; `make check` green before and after. Cost: 384 bytes
  of flash with the telemetry fields (111,844 to 112,228; 14,748 spare).

  The refusal is written and proven the same way (`ccmp_replay_enforce`,
  `../emulator/scenarios/enforce/ccmp-replay.txt`: the replayed frames and the
  duplicate are dropped, everything else arrives, the other 35 scenarios
  pass with it on). It waited for a real rabbit to show N rising,
  `p0/0/0` and a rising packet number in V over a day that includes a
  group rekey: Nabazprout did on 2026-11-12 (N 37,573, p0/0/41, V
  rising), and **the refusal ships from Naboot 1.2.0.0 on**, with a
  probation: if more than half of a boot's first 256 protected frames
  are flagged, that boot only counts, so wrong words cannot make the
  rabbit deaf. On 2026-11-18 Nabazprout's `-gzrf-tel` showed N 0x644c,
  p0/0/2e: nothing refused but the AP's own retransmissions.
  Known limits when enforced: a TKIP group key (mixed mode) is not
  examined, and neither are protected management frames.

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

A systematic audit of the original Violet code ran separately (the `VIOLET-*`
files beside this one) and drove items 8, 9 and 11. The flash-space study
that followed is `VIOLET-CODEMORT.txt`.

## The 2026-11-18 review (Naboot 1.2.1.0)

A second review, of the patched tree of `naboot-1.2.0.1-gzrf-tel` (121,372
bytes), read with the 2026-11 fixes above as the baseline: where one of
them had left a sibling of the fixed code unfixed, it is here. Each fix is
an apply-mods step of its own, in `naboot-1.2.1.0` and `-tel` and in no
released mode; each has its scenario or host test, run before the step (the
control) and after. Measured means a tool reproduced it; read means it was
read only.

### Fixed

- **S-1, the element walks still read past the frame (Low, measured).**
  Item 8 bounded the RSN and vendor-WPA suite loops and item 3 the
  probe-request SSID *destination*; the walks themselves went on reading an
  element's length byte and body past `frame_end` -- SSID, RATES/XRATES
  (the Violet audit's A3), DSPARMS, the RSNXE, the vendor OUI, the loop
  step, the probe request's SSID source, and the association response's
  status word. Reads only, into the receive buffer's tail or `.bss`, from
  any station in radio range while the rabbit scans or is its own access
  point. `ie_walk_bounds`: one test at the top of each loop body, an
  element whose header or declared body runs past the frame ends the walk.
  Proof: `../emulator/tools/ie-walk-proof.sh <Nab.elf>` -- the emulator under ASan,
  which delivers every frame in a heap block of exactly its length; the
  build without the step reports `heap-buffer-overflow READ` in
  `ieee80211_input_mgt` for `../emulator/scenarios/forged-beacon-elements.txt` (a
  stranger's beacons beside every beacon round: the scan runs before the
  first timed event) and for `../emulator/scenarios/vm/probe-ssid-short.txt` under the
  built bootloader (config mode is the bootloader's); the build with it
  reports none, lists "Forged" with what the frame held, answers the
  short probe as one with no SSID, and reaches RUN. `make check` and `make asan` green with the step in the
  default list.

- **S-2, the CCMP replay probation could be turned off for a boot (Medium
  as a bypass, measured).** `ieee80211_ccmp_enforcing()` decided once, at
  the 256th protected frame, from counters that count the frames an
  attacker replays: 200 replays of one captured frame in the first seconds
  after association and the refusal was off until the next boot
  (`../emulator/scenarios/enforce/ccmp-probation.txt`: replays 196..200 handed up, a
  genuine frame, the same replay through twice more -- 65, 66, 67, 68). The
  probation was there in case a board's RXD words were not the packet
  number; Nabazprout answered on 2026-11-12 (N 37,573, p0/0/41, V rising)
  and again on 2026-11-18 (N 0x644c, p0/0/2e). `ccmp_no_probation` drops it:
  refused from the first frame, counters and telemetry kept. Proof:
  `../emulator/tools/ccmp-enforce-proof.sh` -- the probation scenario fails on the
  control and passes with the step (60, 61, 61, 61), `../emulator/scenarios/enforce/ccmp-replay.txt`
  passes on both. `SIM_MAX_EVENTS` raised to 512 for it (C-9).

- **S-3, the TLS alert description was read after `drop()` had moved the
  buffer (Low, measured).** `nab_tls_next()` read `body[1]` after the
  memmove, so a `close_notify` with another record buffered behind it was
  classified as a fatal failure (state FAILED, a fatal alert queued back)
  and the reverse misreading was possible. Both sites now read the
  description first. Proof: `../tests/test_tls.c` -- an alert alone, the same
  with a record behind it (FAILED before, CLOSED after), a fatal one with a
  record behind it.

- **S-4, `OPstrcmp` compared the longer length (Low, read; replica
  measured).** Item 11 fixed the same mistake in `sysCmp` and left this
  copy: `memcmp` over `max(pl, ql)` reads past the shorter string into the
  next VM object, and "ab" equals "abc" when the byte after "ab" happens to
  be 'c'. `vm_strcmp_bound`: the shorter length, then the lengths. Proof:
  `../tests/test_vm_strcmp.c`, the two expressions as they stand in
  `vinterp.c` over heap strings of exact length under ASan (`BEFORE=1 make
  vm_strcmp` reports the READ; `make vm_strcmp` is clean and ordered). A
  host replica of one line, not the ARM binary.

- **S-5, the RNG seed was readable and writable by runtime bytecode (Low,
  read).** Item 1 hid the key (conf 224..256) after the hand-over; the
  entropy pool's stored seed, 32 bytes at 4064 in the same sector, was
  still a `load` and a `save` away. `vm_seed_hidden`: the same latch, the
  same shape, for 4064.. too. `../tests/apply_mods/test_review_fixes.py` keeps
  the offset equal to `NAB_RNG_SEED_OFF`.

- **S-6, the configuration access point took orders from anyone (Medium,
  design).** Config mode is an open AP and `cbhttp` applied `?name=value`
  arguments from every request, whatever the page: anyone who joined it
  could `GET /s.css?a=<their warren>` and a rabbit whose key is pending then
  takes that warren's bytecode. `config_presence`, the cheapest change that
  keeps the phone flow: arguments count on the save (`/b.htm`) only, and a
  save that *changes* the server URL -- compared to the one the page
  offered, so Violet's or an empty one reading as the factory server is not
  a change -- is taken only while the head button is held, the proof of
  presence `owner_unlock` already asks of an unsigned upload; otherwise
  nothing is saved and the error page answers. The Wi-Fi fields need no
  button. The setup page says so under the field (+35 bytes gzipped).
  Proof: `../emulator/tools/config-presence.sh` on the built bootloader --
  `config-server-nohead` (a stranger: the error page, the rabbit stays its
  own access point -- `expect-mode master`, since RUN is the state of a
  station and of the access point alike -- and the page keeps its
  server), `config-server-head` (the owner, head held: taken, a station in
  RUN), `config-wifi-nohead` (the ordinary save: taken, a station in RUN);
  the control is the base 1.2.1.0 image, whose nohead run takes the server
  and joins. One lesson on the way: Metal's `if` jumps on 0 only, nil is
  true (`OPelse`), so `needhead` answers an int both ways.
  `../emulator/tools/config-pages.sh` and `../emulator/tools/default-server-proof.sh` still pass.

- **S-7, `tlsIn` dropped silently what the buffer did not take (Low,
  read).** `nab_tls_in` keeps what fits and returns how much; the opcode
  ignored it, so a segment arriving over a partial record would be
  truncated and the stream desynchronise into `bad_record_mac` with no
  trace of why. `tls_in_overflow`: `nab_tls_overflow()` ends the session
  with `record_overflow`. Proof: `../tests/test_tls.c` (the alert queued, no
  effect outside a connection). It cannot happen with a server that honours
  `max_fragment_length` and a 1460-byte MSS.

- **C-1, apply-mods read CRLF vendor files with `read_text()`.** 22
  read/write pairs on `vinterp.c`, `boot.0.0.0.13.mtl`, `vaudio.c`, `vbc.h`,
  `vmem.h`, `mem.h` and the cold_state headers converted the file to LF as
  a side effect, which later anchors depended on. They use
  `read_flat`/`write_flat` now. Proof: `../tools/mods-matrix.py` over all 43
  modes, every tree identical with CRLF read as LF; `Nab.bin` of
  `naboot-1.2.1.0` byte-identical before and after.

- **C-2, the mode table drifts invisibly.** `../tools/mode-diff.py` prints each
  mode against a base as added/missing steps; `../tests/apply_mods/test_review_fixes.py` pins
  the variants (`naboot-1.2.0.1-kxprobe` without `owner_unlock` is the one
  documented exception). **C-9**, `SIM_MAX_EVENTS` 64 -> 512.

- **O-4, dead bootloader bytecode.** `boot_strip_wav` (the WAV player that
  never ran: `wavstarthttp` has been commented out in `main` since the file
  exists) and `boot_strip_login` (Violet's `o`/`p` login fields, which the
  page has not had since modernize_pages; the boot URL keeps `&l=&p=`, the
  Terrier reads them as identity). `prune_unreachable` drops what they
  orphan. 22,105 -> 21,760 bytes of bytecode, the page's hint and `needhead`
  included.

### Deferred, with the reason

- **S-8, entropy credit per frame, whoever sent it (Low, inferred).** A
  flooder's 64 frames make the pool declare itself ready on the first boot
  ever; every later boot holds 128 bits of credit in the stored seed before
  the first frame, and SRAM noise is mixed in regardless. A per-source cap
  was weighed and left out: a rabbit alone with its one access point would
  then never reach `NAB_RNG_MIN_CREDIT` on that first boot and SAE would
  never start. Documented in `../crypto/nab_rng.h`.

- **O-1, `-Os` on the pure crypto files** (`opt_size_crypto`, mode
  `naboot-1.2.1.0-oscrypto`) and **O-2, ChaCha20-Poly1305 as the only AEAD**
  (`tls_chacha_only`, mode `naboot-1.2.1.0-chacha`; `nab_tls.c` under
  `NAB_TLS_CHACHA_ONLY`, `make tls-interop-chacha`): measurement modes for a
  hardware test, in no release. `-Os` for the whole image failed on
  hardware three times (FLASH-MAP.md). Sizes in FLASH-MAP.md.

- **O-3, byte-loop `strchr`/`memmove`/`memcmp`**: not done; nothing proves
  them equal to newlib's on this hardware yet, and 1.2.1.0 has room.
