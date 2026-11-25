#!/usr/bin/env python3
"""What a Nabaztag v1's Wi-Fi card said when the native firmware did not
associate, read back from the PIC's data EEPROM (from 0x40).

The native firmware (firmware-arm/v1-native, prism_report) writes it once,
30 s after a join that brought no link. Lazare stayed at blue twice
(2026-11-24/25) with no readable serial log; this is how to see why.

    card-report.py <ICSP dump.hex>      a read with read.sh
    card-report.py --log <emulator log> the emulator's "eeprom: [040] <- 43" lines
"""
import sys

PORT = {0: "disabled", 1: "initialised", 2: "searching (BSS)", 3: "connected (IBSS)",
        4: "connected (BSS)", 5: "out of range"}
LINK = {0: "none yet", 1: "connected", 2: "disconnected", 3: "AP changed",
        4: "AP out of range", 5: "AP in range", 6: "association failed"}


def from_hex(path):
    mem, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        b = bytes.fromhex(line[1:])
        addr, kind = (b[1] << 8) | b[2], b[3]
        if kind == 4:
            base = ((b[4] << 8) | b[5]) << 16
        elif kind == 0:
            for i, x in enumerate(b[4:4 + b[0]]):
                if 0xF00000 <= base + addr + i < 0xF00400:
                    mem[base + addr + i - 0xF00000] = x
    return mem


def from_log(path):
    mem = {}
    for line in open(path, errors="replace"):
        i = line.find("eeprom: [")
        if i >= 0:
            a, v = line[i + 9:].split("] <- ")
            mem[int(a, 16)] = int(v.strip()[:2], 16)
    return mem


def report(mem):
    b = lambda a: mem.get(a, 0xFF)
    w = lambda a: b(a) | (b(a + 1) << 8)
    if b(0x40) != ord("C"):
        return "no card report (EEPROM 0x40 is %02X): the firmware associated, or never ran 30 s" % b(0x40)
    port, link = w(0x48), w(0x46)
    bssid = ":".join("%02x" % b(0x4C + i) for i in range(6))
    n = min(b(0x58), 8)
    ssid = bytes(b(0x5A + i) for i in range(n)).decode("latin-1")
    q = [w(0x52 + 2 * i) for i in range(3)]
    if b(0x4C) == 0xFF and w(0x4A) == 0xFFFF:   # the card held no record
        return "\n".join([
            "card report, format %d" % b(0x41),
            "  join result %d, %d info frame(s), last type %04X" % (b(0x42), b(0x43), w(0x44)),
            "  last link status %d (%s)" % (link, LINK.get(link, "?")),
            "  port status %d (%s)" % (port, PORT.get(port, "?")),
            "  no channel, BSSID, SSID or signal: the card never joined a network",
        ])
    return "\n".join([
        "card report, format %d" % b(0x41),
        "  join result %d, %d info frame(s), last type %04X" % (b(0x42), b(0x43), w(0x44)),
        "  last link status %d (%s)" % (link, LINK.get(link, "?")),
        "  port status %d (%s)" % (port, PORT.get(port, "?")),
        "  channel %d, BSSID %s, SSID %r (%d bytes)" % (w(0x4A), bssid, ssid, b(0x58)),
        "  quality %d, signal %d, noise %d" % tuple(q),
    ])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--log":
        print(report(from_log(sys.argv[2])))
    elif len(sys.argv) == 2:
        print(report(from_hex(sys.argv[1])))
    else:
        sys.exit(__doc__)
