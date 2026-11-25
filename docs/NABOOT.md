# Naboot: flash, adopt, troubleshoot

The Nabaztag:tag (v2) on Naboot, step by step. The illustrated guide is on [Le Terrier](https://terrier.cyberloutre.fr/naboot).

## Flash Naboot & adopt your rabbit on Le Terrier

> ⚠️ **Flashing is done at your own risk** — no warranty, no liability. A
> failed flash can leave the rabbit unable to re-enter config mode,
> recoverable only over JTAG
> (see [`firmware-arm/docs/RECOVERY.md`](../firmware-arm/docs/RECOVERY.md)). Flashing is
> **optional**: a stock rabbit works fine on the
> [Home Assistant add-on](ADDON.md), with nothing
> flashed.

Put your rabbit on the public warren —
**[Le Terrier](https://terrier.cyberloutre.fr/)** — in four steps:

1. **Download Naboot 1.2.1.4** — the illustrated 5-minute flashing guide is
   at <https://terrier.cyberloutre.fr/naboot>. The image is reproducible: it
   is mode `naboot-1.2.1.4` of `firmware-arm/apply-mods.py`, Ed25519-signed,
   118 768 of 126 976 bytes of flash. The signed file is also in
   [`firmware-arm/releases/`](../firmware-arm/releases/)
   (`naboot-1.2.1.4.signed.sim`, with its sha256 and which way in from each
   starting firmware); every earlier release is in `firmware-arm/releases/archive/`.
2. **Flash it from your phone** via the rabbit's config-mode page: unplug, hold
   the head button while plugging the power back in (belly turns **blue**), join
   the open `Nabaztag-XXXX` Wi-Fi, open `http://192.168.0.1`, tap *firmware
   upgrade* and upload the file. **Don't unplug** during the ~30 s flash. When
   it reboots, the first belly light blinks **orange** while it looks for your
   Wi-Fi: that is expected, and since 1.0.0.0 the rabbit comes back **by itself**
   (measured on an over-the-air install: 54 s from download to boot URL,
   nobody at the plug). Only builds before 2026-11-08 needed a power cycle
   after every flash — their post-flash software reset left the 2006 radio
   asleep — so if you are coming from one and it is still orange after a
   minute, unplug it, wait ten seconds, plug it back in, once.
   This is the only manual flash — from here on, updates are pushed from the
   warren's page and the rabbit installs them and restarts on its own.
3. **Point it at Le Terrier.** After it reboots, re-enter config mode (three
   short presses on the head, or head button while plugging in → belly
   **blue** again; it closes by itself after ten minutes with nobody on the page) and reopen
   `http://192.168.0.1` — it now shows *Naboot's* setup page. Enter your home
   Wi-Fi, then under **Advanced → Server** set **Boot URL host** to
   **`terrier.cyberloutre.fr/vl`** and tap *Connect rabbit*. The field holds
   `host/vl`, and the rabbit then requests `http://<that>/bc.jsp`.
   Le Terrier and the add-on both answer whether or not the `/vl` is there,
   so dropping it is survivable here — but keep it anyway: it is the path a
   stock Violet or OpenJabNab server expects, and the one every other document
   about this hardware assumes. No `https://` either, in any case: the 2006
   firmware speaks plain HTTP only. (Running your own warren? Put
   `your-host/vl` here instead.)
4. **Adopt it — the spoken code, then the colours.** On its first boot
   against Le Terrier the rabbit runs a key exchange with the warren (a few
   seconds) and then lights **five colours on its belly for one minute**. It
   is not crashed: it is showing you its key. It then fetches its bytecode,
   connects, and **says its adoption code out loud** (e.g. *“BRAMBLE 4 2”*).
   Sign up on the Terrier and type that code on the **`/pair`** page — the
   rabbit is yours. The warren's page then shows the five colours *it*
   computed and asks whether the belly showed the same: **confirm**, and from
   then on the warren signs that rabbit's bytecode (the rabbit refuses
   unsigned bytecode), its XMPP moves onto TLS, and every update reaches it
   over the air. On the LEDs **orange looks like yellow and white like
   cyan**: the page names each colour, and those pairs count as the same.
   Answer *No* only for a real difference — a refused key needs a reset of
   the rabbit to get new colours; when unsure, choose *Not sure* and replug
   it to see them again. Until you confirm, the colours come back for a minute at
   every boot — unplug and replug to see them again. *Missed the code?
   **Press the rabbit's head**: while it is not adopted, it repeats the code
   slowly, one letter at a time. Or, on the `/pair` page, “Can't catch the
   code?” e-mails it to you, then press its head within ten minutes to prove
   the rabbit is in your hands.*

A rabbit on **Naboot 1.0.0.0** or later whose owner has confirmed its adoption
key runs its XMPP stream inside **TLS 1.3 (PSK)** on port **5223**, keyed from
that adoption key; from **1.0.0.2** its boot request (`bc.jsp`) goes there too.
The plain port stays for every other rabbit.

> **Nothing happens after step 3 — no code, no voice?** Then the rabbit never
> reached the Terrier, and the firmware is not the first suspect. Check, in
> order: that **Boot URL host** has no `https://` and no typo (a wrong host
> fails silently — though a missing `/vl` is fine here, both forms answer);
> the belly LEDs (**blue** = still in config mode; **orange** = looking for
> Wi-Fi, normal for up to a minute after a flash; **five colours for a
> minute** = the adoption exchange of step 4, wait it out); that your 2.4 GHz
> network lets a 2006 **802.11b** radio in (b rates enabled, no "n/g only");
> on **WPA3**, that you run 1.0.0.2 if the access point is set to
> H2E-only (hash-to-element; beta) — 1.0.0.0 only does SAE *hunting-and-pecking*;
> WPA3-only and WPA2/WPA3 transition both work; and that the rabbit can resolve
> `terrier.cyberloutre.fr` (it needs working DNS). A rabbit that misbehaves
> after a flash is flashed again from its own setup page, with the release
> itself; [`RECOVERY.md`](../firmware-arm/docs/RECOVERY.md) covers the rest.

> The custom firmware is **optional**: a stock rabbit works fine on the
> [Home Assistant add-on](ADDON.md) (nothing flashed).
> Naboot is what unlocks **WPA3**, the **adoption key** with signed bytecode and
> **TLS**, **over-the-air updates**, the modern config UI, and dialing a remote
> warren like Le Terrier.

## Hardware

- Tested on the **Nabaztag:tag** (v2, 2006 — mic + RFID). The v1 (2005) and
  the Karotz (v3) work on Le Terrier only (above), not with the add-on. The optional **Naboot**
  custom firmware is flashed once from the config-mode upload page, then
  updated over the air, for WPA3, the adoption key, TLS, signed OTA and a
  modern config UI. The Violet add-on drives a **stock** rabbit with no
  hardware mod and **no flashing required**.
- The add-on's host architectures are listed in
  [its section](ADDON.md#host-architecture).

## Troubleshooting

### "After a reboot the belly LEDs are stuck blinking/solid orange — did I brick it?"

**No — don't panic.** Orange (blinking or solid) means the rabbit is *awake and
looking for its Wi-Fi / server*, not dead. A bricked rabbit shows **no** boot
animation at all.

**On Naboot 1.0.0.0, give it a minute or two.** The rabbit comes back on its
own: after a flash or an OTA (measured: 54 s from download to boot URL), after
a server restart (it reconnects, base LED violet, no reboot), and after an
access point that dropped or forgot it (it notices within a few minutes and
rejoins). If it is *still* orange after that, look at the network before the
rabbit: 2.4 GHz with 802.11b rates allowed, DNS for the server name, and on
WPA3 an access point that allows SAE hunting-and-pecking, or 1.0.0.2 if it
is set to H2E-only.

**Five colours on the belly for a minute** is not a fault either: that is the
adoption exchange waiting for you to confirm the colours on the warren's page
(see [*Flash Naboot & adopt*](NABOOT.md#flash-naboot--adopt-your-rabbit-on-le-terrier)).
Until you do, it repeats at every boot.

**On a stock rabbit, or a Naboot build from before 2026-11-08,** the old advice
stands: these 2006 radios are flaky at re-associating on boot, and a software
reset leaves the USB radio asleep. Unplug the power, wait ~10 seconds (so it
fully discharges), plug it back in. It then boots → fetches its bytecode →
`locate` → connects → **breathes**. If the first try doesn't catch, do it once
more — it's normal, not a fault.

Tips: it's the Wi-Fi association that's flaky, so a strong signal / 2.4 GHz / a
less crowded channel helps. Don't restart the server (or the add-on) repeatedly
in a short window (each restart drops the rabbit's connection and makes it
re-seek). A `ping` from HA failing is **not** a sign of trouble if the rabbit is
on a different VLAN (cross-VLAN ICMP is usually blocked) — check the warren's
page or the add-on's status/logs instead.

### Telling a real brick apart from Wi-Fi flakiness

| Symptom | Meaning | Fix |
|---|---|---|
| Belly LEDs **blink/glow orange**, boot animation plays | Booted, seeking Wi-Fi/server | On 1.0.0.0: wait a minute or two, then check the network. Stock or older builds: power-cycle (unplug ~10 s) |
| **Five colours** on the belly for a minute, then a normal boot | Adoption key waiting to be confirmed | Confirm the colours on the warren's page |
| **Orange forever** right after WPA3 was turned off on your Wi-Fi (1.0.0.2+) | The rabbit refuses a WPA3 → WPA2 downgrade | Re-save the Wi-Fi settings in config mode (see below) |
| **No LEDs / no boot animation at all**, frozen | Possible bad flash | Config-mode upload (hold head button on power-up → upload a signed `.sim`); otherwise the JTAG station: `firmware-arm/jtag/station.sh flash` |

### Orange after the Wi-Fi went from WPA3 back to WPA2 (1.0.0.2 and later)

Once a rabbit on 1.0.0.2 has joined a network with WPA3 (SAE), it remembers
that this network requires WPA3 and **refuses to join it with WPA2 again**.
That is on purpose: otherwise an attacker could pose as your access point with
WPA2 only and make the rabbit fall back to it (a "downgrade").

So if you turn WPA3 off on that network (or a router update switches a
WPA2/WPA3 transition network to WPA2 only), the rabbit stays **orange** and
never connects. To unlock it:

1. Put the rabbit in config mode (three short presses on the head on
   1.2.1.3 and later; or hold the head button while plugging it in, release
   when the light turns blue — not more than 7 s, or it enters the factory
   test mode).
2. Join its Wi-Fi, open its setup page and **save the Wi-Fi settings again**
   (same network name and password are fine).

That save clears the requirement; the rabbit joins over WPA2 and remembers
WPA3 again the next time it gets it. Changing the network name or the
password clears it too. The requirement is stored once per network in the
configuration flash sector, next to the Wi-Fi password.
