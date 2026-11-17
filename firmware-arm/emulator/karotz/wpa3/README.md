# WPA3 on the Karotz's kernel

The Karotz's Wi-Fi is a Ralink RT73 on USB, driven by `rt73usb` on Linux's
own wireless stack (mac80211). [LapinLibre](https://github.com/sweetlilmre/LapinLibre)
runs it on Linux 2.6.32.71 with wpa_supplicant 2.11 and OpenSSL 3.6: the
user space can do WPA3, the kernel could not. Two patches, numbered to sit
after LapinLibre's own kernel patches:

- `patches/74-mac80211-sae.patch`: SAE with user space SME, as Linux 3.8
  added it, adapted to 2.6.32's work-item MLME. nl80211's
  `NL80211_AUTHTYPE_SAE`, `NL80211_ATTR_SAE_DATA` and
  `NL80211_ATTR_FEATURE_FLAGS` at the numbers wpa_supplicant sends;
  cfg80211 lets the confirm follow the commit (a commit is not an
  authentication yet); mac80211 sends the frames user space builds and
  hands the AP's back; the AKM suites SAE (8) and PSK-SHA256 (6) are let
  through at association.
- `patches/75-rt73usb-mfp.patch`: 802.11w, which WPA3 requires and the RT73's
  crypto engine cannot do: mac80211 does it in software, so `nohwcrypt`
  defaults to 1 and the driver says it is MFP-capable.

The patches change Linux and are GPL-2.0, as the kernel is.

## The proof

In QEMU (ARM Versatile, which runs the ARM920T's code): the same 2.6.32.71
with LapinLibre's modern-GCC patch, once without the two patches (the
control) and once with them, each with `mac80211_hwsim`'s two radios.
`init.c` starts hostapd 2.11 as a WPA3-only access point on one radio
(SAE, management frames protected, required) and wpa_supplicant 2.11 on
the other, both static for ARMv5 on OpenSSL 3.5.

```sh
podman build -t karotz-wpa3 firmware-arm/emulator/karotz/wpa3
podman run --rm karotz-wpa3
```

Measured 2026-11-17:

- control: `WPA: Failed to select authenticated key management type`,
  not connected;
- patched: `KEY_MGMT SAE`, `MGMT group cipher AES-128-CMAC`, `require MFP`,
  two authentication rounds, the AP's `SAE: State Confirmed -> Accepted`,
  association, `IGTK`, `Key negotiation completed [PTK=CCMP GTK=CCMP]`,
  `EAPOL-4WAY-HS-COMPLETED`, `CTRL-EVENT-CONNECTED`, in 4 s.

## What a real image needs besides

Found on the way, and true of LapinLibre on a real Karotz too:

- **OpenSSL's seeding.** On a kernel before 4.8, OpenSSL 3 waits for
  `/dev/random` to become readable before giving any random byte; on
  2.6.32 without an entropy source that is never, and SAE's first commit
  hangs (here, and the Terrier's Karotz client 0.1.3 stalled the same way on
  a real Karotz). Build OpenSSL `--with-rand-seed=devrandom`, and keep a
  random seed across boots so that `/dev/urandom` is seeded early
  (LapinLibre has `BR2_PACKAGE_URANDOM_SCRIPTS` off).
- **wpa_supplicant**: SAE (`BR2_PACKAGE_WPA_SUPPLICANT_WPA3`) and the nl80211
  driver (`-Dnl80211`; `network.sh` uses `-Dwext`, which has no SAE), and a
  network block with `key_mgmt=SAE WPA-PSK` and `ieee80211w=1` so that
  WPA2 networks still work.
- **Not here**: SAE's hash-to-element (H2E, status 126). The kernel passes
  the status through, but only hunting-and-pecking was proven; an access
  point set to H2E only would refuse the rabbit.

Not yet on a real Karotz: the RT73 in software crypto, at its real speed.
