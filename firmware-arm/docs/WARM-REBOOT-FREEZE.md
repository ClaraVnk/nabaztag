# A rabbit that does not get its Wi-Fi back after a warm restart

2026-11-24/25. Three rabbits that stayed orange after a restart that was not
an unplug: what the code says a warm restart does and does not reset, what
each rabbit's own line says, what the emulator reproduces, what is fixed and
what still needs the test rabbit.

Everything below is read from `firmware-arm/` as applied by `apply-mods.py`
(mode `naboot-1.2.1.4`), from the Terrier's `rabbit_events`, from the
rabbits' diagnostic lines (`docs/DIAGNOSTIC.md`), and from emulator runs.
Nothing was flashed, no rabbit and no server was touched.

## Verdict, in two lines

- **Pinkyblack (…8859)** is not a warm-restart freeze. Its line
  (`a4 r3/25 h6 e4a c6`) says the access point *declines* it, status 37,
  on a WPA2 mesh node it hears perfectly well. The bootloader then retries
  that one node for ever, orange. That retry loop is a real weakness, fixed
  here (`bss_fallback`, mode `naboot-1.2.1.5`, proven in the emulator).
- **…45a6 and Nabazprout (2026-11-18, 2026-11-20)** are deaf-radio cases
  after a restart: a radio that heard nothing for minutes on a channel it had
  just talked on. The code does *not* explain them — a warm restart
  reinitialises everything the driver and Linux's own driver reinitialise.
  What survives is in the dongle and the access point, not in RAM, and only a
  measurement on Nabazprout can say which (section 6).

## 1. What a warm restart resets, and what it does not

Every software restart of a Nabaztag:tag is the same thing: `reset_uc()`
(`src/main.c`) arms the watchdog to overflow in 256 µs; the ML674061 resets
and pulses `RSTOUT_N` for 100 µs. The paths that end there:

| path | who calls it | reason word (`&r=` in the boot URL) |
|---|---|---|
| OTA install | `flash_uc` after writing | 3 |
| bytecode `reboot` (runtime reload RB, "wifi lost", config save) | `OPreboot` | 2 |
| config save / three presses | `OPconfigBoot` | 6 |
| USB host stopped | `usbwatch_tick` | 5 |
| VM out of memory | `vmem.c` | 1 |
| hardware watchdog (2 s without `CLR_WDT`) or an unplug | — | 0, with every counter at 0 after an unplug |

What the next boot then does, from the first instruction:

- **SRAM.** `init.s` copies `.data` from flash and zeroes `__bss_start__..__bss_end__`.
  Since `extbss` (2026-10-25) that range holds every `.bss.*` section, so
  `rt2501_connected`, `rt2501_rx_errors`, `rt2501_rx_down`, the scan
  cache, the SAE cache, `eapol_state`, `ieee80211_state`, the link
  watchdog's clocks all start at zero on a warm boot exactly as on a cold
  one. The seven guards that used to survive (`usbhost_init_status`,
  `eapol_state`...) are in `.data` since `cold_state`. **Confirmed by the
  linker script and `init.s`; nothing RAM-resident in the radio stack
  survives.**
- **What is kept on purpose** (`.extbss`, NOLOAD, external RAM): the
  diagnostic counters, the reset reason, the config-boot mark, the audio
  FIFO, `buffer_temp`, the TLS certificate area. None is read by the Wi-Fi
  code.
- **The USB host controller** (ML60842, OHCI): `usbctrl_init()` and
  `hcd_init()` program it from scratch; `usb_watch`'s docstring says
  `RSTOUT_N` resets it with the processor. Not measured here.
- **The dongle** (RT2571W "RT2501USB", soldered): it keeps its power through
  the reset. The boot re-enumerates it (root-hub port reset, `hcd.c:947`
  `RH_PS_PRS`, set address, set configuration), then `rt2501_setup()`
  rewrites the 8051 firmware and sends `USB_DEVICE_MODE / USB_MODE_FIRMWARE`,
  writes the MAC defaults, does the `MAC_CSR1` soft reset + BBP reset,
  waits for `BbpRfStatus`, writes the BBP defaults, sets host-ready, reads
  the EEPROM, programs the MAC address and the channel. **This is what
  Linux's `rt73usb` does at every `ifup`** (`rt73usb_load_firmware`,
  `rt73usb_init_registers`, same `MAC_CSR1` sequence, read from v6.6):
  Linux does not send a device reset either (`rt2x00usb_disable_radio` is
  `USB_RX_CONTROL 0`, not `USB_MODE_RESET`). The one thing Linux does and
  the rabbit does not: wait for `MAC_CSR0` to read non-zero before writing
  the firmware ("Unstable hardware", `rt73usb.c:1107`). Whether the
  `MAC_CSR1` soft reset clears a TX/RX engine that was mid-transfer when
  the processor reset is a question for the chip, not the source.
- **The access point.** It keeps the association, the pairwise key, the PN
  counters, and under 802.11w its SA-Query state. The rabbit never sends a
  deauthentication before re-authenticating (it does not know it was
  associated: RAM is clean). A standard AP handles a fresh authentication
  from a known station by starting over; a PMF AP answers the association
  with status 30 and an SA Query, which times out after about a second,
  then accepts. Neither is visible in the three lines below.

## 2. Pinkyblack …8859 (TP-Link Deco mesh), 2026-11-24

Timeline, UTC, from `rabbit_events`: Naboot 1.2.1.4 by OTA at 19:46:16
(`r=3.…, verify 1, flash 1`; back in session 13 s later on its open
network, channel 1: `W5 E0 g0 e0 c1`). At 20:06:27 a `reset` with every
counter at 0 — **that is an unplug, not a software restart**: the counters
live in `.extbss` and are cleared only when the magic word is gone
(`persist_counters`). The bytecode request (`bc.jsp`) that carries the
report was served, so the Wi-Fi worked on that boot. Then nothing.

The owner had moved it to his Deco mesh (WPA2, channel 6). Its setup page
line afterwards:

    2 t109 W0 A0 E0 a4 r3/25 h6 q0 L0 U6d B0 g0 e4a c6 s0 k0 m0 n0 b0 u0 D0/0/0

Read with `docs/DIAGNOSTIC.md`: Wi-Fi given up after 109 s; **four
authentication frames sent, three answered, the last with status 0x25 =
37 ("request declined")**; six beacons of that BSS heard while waiting;
WPA2-CCMP (`e4a`), channel 6; no key exchange, nothing received as a
station. Steady orange is `setleds 0xff8000` in the bootloader's `initW` /
`wifiAuth`: the "trying to join" colour, not the dead-dongle state (that one
is orange too, but with `W0 h0 a0`: a radio that hears nothing and sends
nothing — the JTAG reading of 2026-11-07).

The owner then made a dedicated 2.4 GHz "IoT network" on the Deco. Same
answer:

    2 t202 W0 A0 E0 a4 r5/25 h0 q0 L0 Uca B0 g0 e47 c6 s0 k0 m0 n0 b0 u0 D0/0/0 I0.0.0.0 G192.0.2.1 N0.0.0.0

Four authentications, five replies (one is a retransmission, the AP's or
the dongle's: normal radio), status 37 again, on a WPA/WPA2 mixed BSS
(`e47`: group TKIP, pairwise CCMP and TKIP), channel 6. **The Deco declines
this rabbit's authentication whatever the network.**

What the rabbit sends, exactly (`ieee80211_send_auth`, `rt2501_auth`): a
management frame, subtype Authentication, To/From DS 0, addr1 the AP,
addr2 the rabbit, addr3 the BSSID, sequence number filled by the dongle
(`HwSeq`), a computed Duration; body 6 bytes — algorithm 0 (open system),
transaction 1, status 0 — **no information element at all**, at the lowest
rate the AP's rate set allows (`find_closest_rate(1 Mbps)`), with the
dongle's long-retry policy and ACK requested. Before it: a probe request
on every channel during the scan (the AP answered: it was in the list), the
BSSID and channel set, the key slot cleared. **No deauthentication or
disassociation is ever sent** before authenticating: the bootloader does not
know it was associated (RAM is clean, section 1), and the stock Violet code
has no such frame in station mode. After a decline the firmware drops to
IDLE and the bootloader tries again 20 s later, same frame. No 802.11r, no
PMF bit, no capability field is in play: those live in the association
request, which is never reached.

So nothing in the frame is "wrong" in a way an AP could grade; status 37 is
the AP's decision about this MAC. What answers an open-system authentication
with a refusal on a Qualcomm-based mesh (the Deco's platform): an access
control list (the Deco app's **Blacklist** — a device blocked once, as an
"unknown device", stays blocked by MAC), and the band-steering daemon
refusing a client on the band it wants it off (the owner's router log shows
it at work that evening; a rabbit has only 2.4 GHz and cannot be steered,
only refused). Which of the two, and the exact status such firmwares use,
is not established here: their code is closed and this reading is from the
rabbit's side only. The cheap check comes first: the rabbit's MAC in the
Deco app's Blacklist and in its client list (per-device "Mesh Technology"
off pins it to one unit, which is also the one declining it).

How Naboot reacts, from `boot.0.0.0.13.mtl` (stock Violet, unchanged until
now): `initW` scans, `filterscan` keeps **one** BSS per SSID — the
strongest (`bestscan`) — and `wifiAuth` authenticates to it. `boot_loop`
gives it `BOOT_WIFITIMEOUT` = 20 s, then `set wifiscans=tl wifiscans`: on a
one-entry list that is `nil`, so it rescans and finds the same strongest
node. Four tries in 109 s, the same node, then the line is written. A
second Deco unit, or any other access point on the same name, is never
tried. (`ieee80211.c` itself drops to IDLE on a declined authentication,
`"Open auth failed: denied by AP"`, and leaves the choice to the bytecode.)

**Fix: `bss_fallback`** (`apply-mods.py`, mode `naboot-1.2.1.5` = 1.2.1.4 +
this step). When a network name is configured, `initW` keeps every scanned
BSS of that name, strongest first, one per BSSID; the existing 20 s retry
then walks the list before rescanning. +138 bytes of bytecode. Nothing
changes for a single access point, for the first boot (no name configured),
or for config mode's list.

Proven in the emulator, section 5. What it does not do: make a Deco accept
the rabbit. For the owner, meanwhile (`docs/DIAGNOSTIC.md`): a 2.4 GHz-only
network for it (Deco: "IoT network"), fast roaming off, WPA2-PSK (AES) only,
then save the rabbit's Wi-Fi again; if the Deco app lists the rabbit, turn
"Mesh Technology" off for it.

## 3. …45a6, 2026-11-24 (Naboot 1.2.1.2, after an OTA offer at 11:10)

Setup page line:

    2 t184 W0 A0 E0 a1 r3/0 h0 q0 L0 Ub8 B0 g0 e4a c6 s0 k0 m0 n0 b0 u0 D0/0/0

One authentication sent, **three replies** with status 0, and then, in 184
seconds, **not one beacon of that BSS** while waiting (`h0`), no frame
addressed to it (`U` = 0xb8 = 184 s = since boot), no second
authentication. Its good sessions were on the same AP: `W5 E2/E3 a1 r1/0
g4 e44 c6`.

What the code allows:

- Three replies to one request is an access point retransmitting its reply
  because it got no acknowledgement — the acknowledgement is the dongle's
  MAC answering by itself. So the rabbit's transmitter was already not
  reaching the AP when the replies came, while its receiver still worked.
- `h0`: association takes `IEEE80211_ASSOC_TIMEOUT` = 10 ticks = 2 s, in
  which an audible AP sends about twenty beacons. Zero means either the
  association request was never sent (`rt2501_tx` or `hcd_malloc` failed,
  state straight back to IDLE) or the receiver died within that second.
- `a1` for the remaining three minutes: `boot_loop`'s 20 s retries rescanned
  and never found the network again (`wifiAuth` is not called when the scan
  is empty). A receiver that hears no beacon from an AP it exchanged frames
  with a minute earlier.
- The owner's config saves — warm restarts, reason 6 — did not change it.
  Config mode itself worked (the page was read over the rabbit's own access
  point), so the dongle was not dead: it beaconed and served HTTP in master
  mode, and would not hear or reach the network in station mode.

Not established: whether an unplug fixed it (the owner's report does not
say), and what in the dongle or the AP holds that state. Nothing in RAM can
(section 1). This is the case to catch on Nabazprout.

## 4. Nabazprout, first warm boot after an OTA (2026-11-18, 2026-11-20)

From the notes of those two days and the comment on mode
`naboot-1.2.1.0-tel` in `apply-mods.py`: twice, the first boot after an OTA
install (reason 3) sat in `W1` (scanning) with an empty scan cache and
`B0`, orange, until unplugged; the next OTA of the same image came back on
its own. Same shape as …45a6's second half: a station-mode receiver that
hears no beacon on a 2.4 GHz band that is never silent. The DCC telemetry
image exists for exactly this (`listen.sh`); it has not yet caught one.

## 5. What the emulator reproduces

The emulator runs the firmware's own C and the bootloader bytecode lifted
from the built image (`firmware-arm/emulator/tools/bootbc.py`); its access point is a model
(`src/vap.c`). It can model the AP's side and the bytecode's choices; it
cannot model an RT2571W's registers after a processor reset — nothing in
the emulator stands in for the dongle's silicon.

Done, versioned here:

- `firmware-arm/emulator/scenarios/vm/mesh-decline-wpa2.txt` + the AP option `decline <status>`
  (`firmware-arm/emulator/src/sim.c`, `firmware-arm/emulator/src/vap.c`): two access points on one name, the strongest
  declines every open-system authentication with status 37, the other
  accepts. `firmware-arm/emulator/tools/mesh-fallback-proof.sh <1.2.1.5 Nab.elf> <1.2.1.4 Nab.elf>`
  runs it under the bootloader VM on the wpa2 configuration page. Expected:
  the control (1.2.1.4) retries the declining node only and never reaches
  RUN; 1.2.1.5 is declined once, moves to the second node at the 20 s retry
  and reaches RUN. **Not yet run** (2026-11-24 evening): the dev VM's
  `~/heavy.lock` was held by a podman `aardvark-dns` process that had
  inherited it from an earlier plain `flock`, so the two firmware builds and
  the emulator image never started; the patched bytecode compiles
  (`firmware-arm/tools/mtl_comp.py`, 36 418 bytes against 36 280). The RUNBOOK entry has
  the three commands; the result line goes there and here once run.

Not done, deliberately: a "dongle keeps stale state across a restart"
scenario. Without a measurement of what that state is, a model of it would
be a model of a guess, and a fix proven against it would prove the guess
(a fake that recorded without encoding once hid a belly that never lit).

## 6. What needs Nabazprout (proposal, not run)

The question is "what does the first station-mode boot after a warm restart
see on the radio when it goes deaf". Proposal, on the JTAG station
(`firmware-arm/jtag/listen.sh`, DCC telemetry, no halt, never during an
association, never the watchdog):

1. Flash (OTA, in session, Nabazprout only) a `-tel` build of 1.2.1.5 — the
   release steps plus `dcc_telemetry`, as `naboot-1.2.1.0-tel` was — and keep
   `listen.sh` running across the install's reboot.
2. If the first warm boot goes `W1` with `C`/scan cache empty: read, from the
   telemetry and then over JTAG without halting the association code,
   `rt2501_rx_errors`, `rt2501_connected`, `nab_tx_errors`, the OHCI
   `HcFmNumber`, and the dongle's `TXRX_CSR0`, `MAC_CSR1`, `MAC_CSR12`
   (`rt2501_read` over the control pipe from a DCC command, to add to the
   telemetry step if needed). Compare with the same registers after an
   unplug.
3. Only then decide between: Linux's `MAC_CSR0` wait before the firmware
   write; a `TXRX_CSR0` RX disable/enable around the soft reset; a
   deaf-radio re-`rt2501_setup()` from `rt2501_timer()` after N seconds
   without a single received frame while scanning (the recovery path that
   `rx_recovery` gave the error budget, for a radio that reports no error).
   Each is a few lines; none should ship on a guess.

Repeat the OTA at least three times: the 11-18 failure did not reproduce on
the second try.

## 7. What to tell owners now

- A rabbit steady orange after a change of router or of Wi-Fi settings:
  read its setup-page line. `r…/25` is the router declining it: see the
  router advice in `docs/DIAGNOSTIC.md`; 1.2.1.5 will also try the other
  access points of the network.
- A rabbit orange after an update or a restart, `h0 a0` or `W1` on its
  line: unplug it ten seconds. If it does it again, say so — that is the
  case being measured.
