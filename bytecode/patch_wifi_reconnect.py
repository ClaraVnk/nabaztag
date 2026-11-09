#!/usr/bin/env python3
"""Let the runtime bytecode rejoin a WPA/WPA2 network after a link loss.

Diagnosed by pacifox in ClaraVnk/nabaztag#24. On RedoXyde/Naboot firmware a
rabbit that loses its Wi-Fi association never gets it back: it sits amber
until somebody pulls the plug. A reboot fixes it, which is why nobody
noticed it was a separate bug from the XMPP reconnect — but a soft reboot
leaves the radio asleep on this hardware, so "a reboot" means a power cycle.

Three things are wrong, and all three are in the runtime's `wifi.mtl`.

**1. The runtime speaks the old Violet config encoding.** `wificrypttype`
only knows `crypt==1` (WEP) and `crypt==2` (WPA). The Naboot config page
stores the C encoding instead — `IEEE80211_CRYPT_WEP=16`, `WPA=32`,
`WPA2=64`, the same values `mtl/boot/boot.0.0.0.13.mtl:46-48` uses. A WPA2
config therefore reaches `netAuth` as `encrypt = 0` with `key = nil`.

Confirmed in the C: `OPnetAuth` only calls `netAuth` when
`(crypt & 0xF0) == encrypt` (`src/vm/vinterp.c:921`), and the scan sets
`0x40` for a WPA2 AP. `0x40 != 0`, so the call is **skipped with no error**.
`wifiAuth` still returns 1, the state becomes `gostationW`, and that waits
for an association nobody requested.

**2. `gostationW` waits for ever.** No timeout, so step 1 never surfaces.

**3. The reconnect replays the boot scan.** `reconnectW` reuses
`hd wifiscans`, restored by `envrestore` — the scan from boot. An AP that
came back on another channel or another BSSID is never found again, even on
an open network where step 1 does not apply.

The boot bytecode does all of this correctly, which is why a reboot works.

Idempotent. Usage: patch_wifi_reconnect.py <bootcode>/sources
"""
from __future__ import annotations

import sys
from pathlib import Path


# The stock Violet sources are latin-1 — there are accented French comments in
# them — so every read and write round-trips through it rather than assuming
# UTF-8 and losing a byte.
ENC = "latin-1"


def edit(path: Path, old: str, new: str, what: str) -> bool:
    s = path.read_text(encoding=ENC)
    if new in s:
        print(f"[skip] {path.name}: {what} already patched")
        return False
    if old not in s:
        sys.exit(f"FAIL {path}: {what} — anchor not found:\n{old}")
    path.write_text(s.replace(old, new, 1), encoding=ENC)
    print(f"[ok]   {path.name}: {what}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    src = Path(sys.argv[1]).resolve()
    wifi, main_mtl = src / "wifi.mtl", src / "main.mtl"
    for p in (wifi, main_mtl):
        if not p.is_file():
            sys.exit(f"not a bootcode sources dir: {p} missing")

    # 1. accept the Naboot config encoding, which OPnetAuth compares against
    #    the scan's high nibble. Values below 16 stay on the Violet path so a
    #    rabbit on stock bytecode with a Violet config is unaffected.
    edit(
        wifi,
        "fun wificrypttype crypt key=\n"
        "\tif crypt==1 then if 5==strlen key then IEEE80211_CRYPT_WEP64 else IEEE80211_CRYPT_WEP128",
        "fun wificrypttype crypt key=\n"
        "\t// Naboot stores the C encoding (WEP=16, WPA=32, WPA2=64), which is\n"
        "\t// what OPnetAuth compares with the scan's high nibble. Pass it\n"
        "\t// through. Violet's 1/2 keep the old path below.\n"
        "\tif crypt>=16 then crypt\n"
        "\telse if crypt==1 then if 5==strlen key then IEEE80211_CRYPT_WEP64 else IEEE80211_CRYPT_WEP128",
        "wificrypttype accepts the Naboot encoding",
    )

    # 2. load the right key for those encodings too
    edit(
        wifi,
        "\t\tlet if crypt==1 then wifiwepkey confGetWifikey0\n"
        "\t\t\telse if crypt==2 then confGetWifipmk -> key in",
        "\t\tlet if crypt==1 || crypt==16 then wifiwepkey confGetWifikey0\n"
        "\t\t\telse if crypt==2 || crypt==32 || crypt==64 then confGetWifipmk -> key in",
        "wifiAuth loads the WEP key for 16 and the PMK for 32/64",
    )

    # 3. an association that never completes is retried, not waited on
    edit(
        wifi,
        "\t\t|(gostationW x-> if state==RT2501_S_CONNECTED then\n"
        "\t\t\t\t(\n"
        "\t\t\t\t\tSecholn \"-------------dhcp\";\n"
        "\t\t\t\t\tif confGetDhcp then startdhcp;\n"
        "\t\t\t\t\tstartdnsclient;\n"
        "\t\t\t\t\tdhcpW time\n"
        "\t\t\t\t)\n"
        "\t\t\t)",
        "\t\t|(gostationW x-> if state==RT2501_S_CONNECTED then\n"
        "\t\t\t\t(\n"
        "\t\t\t\t\tSecholn \"-------------dhcp\";\n"
        "\t\t\t\t\tif confGetDhcp then startdhcp;\n"
        "\t\t\t\t\tstartdnsclient;\n"
        "\t\t\t\t\tdhcpW time\n"
        "\t\t\t\t)\n"
        "\t\t\t\telse let x -> [_ t0] in\n"
        "\t\t\t\t\t// netAuth can be skipped silently by OPnetAuth; without a\n"
        "\t\t\t\t\t// timeout this state waits for ever on an association\n"
        "\t\t\t\t\t// nobody asked for.\n"
        "\t\t\t\t\tif (time-t0)>30 then (Secholn \"assoc timeout, rescanning\"; set wifiscans=nil; initW)\n"
        "\t\t\t\t\telse gostationW x\n"
        "\t\t\t)",
        "gostationW gives up after 30 s and rescans",
    )

    # 4. scan again on link loss instead of replaying the scan from boot
    edit(
        main_mtl,
        "\t\tSecholn \"######### wifi lost\";\n"
        "\t\tset wifi=reconnectW;",
        "\t\tSecholn \"######### wifi lost\";\n"
        "\t\t// Not reconnectW: it replays `hd wifiscans`, the scan restored by\n"
        "\t\t// envrestore from boot. An AP that came back on another channel\n"
        "\t\t// or BSSID is never found again. initW scans for the configured\n"
        "\t\t// SSID, then authenticates, then DHCP.\n"
        "\t\tset wifiscans=nil;\n"
        "\t\tset wifi=initW;",
        "a link loss triggers a fresh scan",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
