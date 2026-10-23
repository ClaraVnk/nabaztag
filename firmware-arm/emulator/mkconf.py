#!/usr/bin/env python3
"""Write the 4096-byte configuration page an emulated rabbit boots from.

A Nabaztag keeps its settings in a flash page the bytecode reads through
sysLoad("conf.bin"). Without it the rabbit is a rabbit out of the box: it
comes up in configuration mode, becomes its own access point and serves the
Violet config pages, which is worth watching once and then never again.

The layout is not guessed. It is the boot code's own, from the constants at
the top of nabgcc's mtl/boot/boot.0.0.0.13.mtl:

    0   41  server URL, "host[:port]/path"
    41   1  1 for DHCP, 0 for a static address
    42   4  address        46  4  netmask
    50   4  gateway        54  4  DNS
    58  32  SSID
    90   1  authentication: 0 open system, 1 shared key
    91   1  cipher: 0x00 none, 0x10 WEP, 0x20 WPA, 0x40 WPA2
    92  64  the key, for WEP
    156  1  proxy enabled  157 4  proxy address   161 2  proxy port
    163  6  login          169 6  password
    175 32  the PMK, which is what WPA and WPA2 actually use
    207  1  0x47, the byte that says this page means something

The cipher byte matters more than it looks: it is what OPnetAuth compares the
scan result against before it will call netAuth at all, so setting it wrong
produces a rabbit that scans, finds its network and then does nothing, which
from the outside is indistinguishable from a broken radio.

  usage: mkconf.py --ssid Terrier --psk hunter2 --security wpa2 \
                   --server warren.example:8080/vl -o rabbit.eeprom
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import sys
from pathlib import Path

SIZE = 4096
CONF_LENGTH = 208

OFF_SERVERURL = 0
OFF_NETDHCP = 41
OFF_NETIP = 42
OFF_NETMASK = 46
OFF_NETGATEWAY = 50
OFF_NETDNS = 54
OFF_WIFISSID = 58
OFF_WIFIAUTH = 90
OFF_WIFICRYPT = 91
OFF_WIFIKEY0 = 92
OFF_PROXYENABLE = 156
OFF_PROXYIP = 157
OFF_PROXYPORT = 161
OFF_LOGIN = 163
OFF_PWD = 169
OFF_WIFIPMK = 175
OFF_MAGIC = 207

MAGIC = 0x47

# The high nibble of ieee80211_encryption, which is also what the
# configuration stores and what OPnetAuth compares against.
CIPHERS = {
    "open": 0x00,
    "wep": 0x10,
    "wpa": 0x20,
    "wpa2": 0x40,
}


def pmk(passphrase: str, ssid: str) -> bytes:
    """PBKDF2-HMAC-SHA1, 4096 iterations — what eapol.c's F() computes.

    The rabbit stores the PMK and not the passphrase: eapol_init() copies
    ieee80211_key straight into pmk without running a derivation, so a
    passphrase left in that field would be used as if it were already a
    master key.
    """
    return hashlib.pbkdf2_hmac("sha1", passphrase.encode(), ssid.encode(), 4096, 32)


def put(page: bytearray, offset: int, data: bytes, length: int, what: str) -> None:
    if len(data) > length:
        sys.exit(f"{what} is {len(data)} bytes, the field holds {length}")
    page[offset : offset + len(data)] = data


def main() -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.split("\n\n")[0],
    )
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--psk", default="", help="the passphrase; the PMK is derived from it")
    ap.add_argument("--security", default="wpa2", choices=sorted(CIPHERS))
    ap.add_argument("--shared-key", action="store_true",
                    help="WEP shared-key authentication instead of open system")
    ap.add_argument("--server", default="warren.example:8080/vl",
                    help="where the rabbit fetches its bytecode, host[:port]/path")
    ap.add_argument("--ip", help="a static address; without one the rabbit uses DHCP")
    ap.add_argument("--netmask", default="255.255.255.0")
    ap.add_argument("--gateway")
    ap.add_argument("--dns")
    ap.add_argument("--login", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("-o", "--out", type=Path, default=Path("rabbit.eeprom"))
    args = ap.parse_args()

    page = bytearray(SIZE)

    put(page, OFF_SERVERURL, args.server.encode(), 40, "the server URL")
    put(page, OFF_WIFISSID, args.ssid.encode(), 32, "the SSID")
    page[OFF_WIFIAUTH] = 1 if args.shared_key else 0
    page[OFF_WIFICRYPT] = CIPHERS[args.security]

    if args.security == "wep":
        put(page, OFF_WIFIKEY0, args.psk.encode(), 64, "the WEP key")
    elif args.security in ("wpa", "wpa2"):
        if not args.psk:
            sys.exit(f"{args.security} needs --psk to derive a PMK from")
        page[OFF_WIFIPMK : OFF_WIFIPMK + 32] = pmk(args.psk, args.ssid)

    if args.ip:
        page[OFF_NETDHCP] = 0
        network = ipaddress.IPv4Network(f"{args.ip}/{args.netmask}", strict=False)
        gateway = args.gateway or str(network.network_address + 1)
        for offset, value in (
            (OFF_NETIP, args.ip),
            (OFF_NETMASK, args.netmask),
            (OFF_NETGATEWAY, gateway),
            (OFF_NETDNS, args.dns or gateway),
        ):
            page[offset : offset + 4] = ipaddress.IPv4Address(value).packed
    else:
        page[OFF_NETDHCP] = 1

    put(page, OFF_LOGIN, args.login.encode(), 6, "the login")
    put(page, OFF_PWD, args.password.encode(), 6, "the password")
    page[OFF_MAGIC] = MAGIC

    args.out.write_bytes(bytes(page))

    how = "DHCP" if page[OFF_NETDHCP] else f"static {args.ip}"
    print(
        f"{args.out}: SSID {args.ssid!r}, {args.security}, {how}, "
        f"server {args.server!r}"
        + (f", PMK {page[OFF_WIFIPMK:OFF_WIFIPMK+4].hex()}…" if args.security in ("wpa", "wpa2") else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
