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


INIT_STEP = {1: "INITIALIZE command", 2: "reading the MAC", 3: "allocating the transmit buffer"}


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
    regs = "  card registers: before INITIALIZE CMD %04X EVSTAT %04X SWSUPPORT0 %04X; after CMD %04X EVSTAT %04X STATUS %04X" % tuple(w(0x64 + 2 * i) for i in range(6))
    if b(0x42) >= 0x80 and b(0x42) != 0xFF:
        return "\n".join([
            "card report, format %d" % b(0x41),
            "  the card never came up: init failed at step %d (%s)" % (b(0x42) & 0x7F, INIT_STEP.get(b(0x42) & 0x7F, "?")),
            regs,
        ])
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


NET_STATE = {0: "down (no link)", 1: "DHCP", 2: "DNS", 3: "ready (HTTP)"}
TCP_STATE = {0: "closed", 1: "SYN sent", 2: "open", 3: "FIN wait"}


def ip(mem, a):
    return ".".join(str(mem.get(a + i, 0xFF)) for i in range(4))


def net_report(mem):
    b = lambda a: mem.get(a, 0xFF)
    w = lambda a: b(a) | (b(a + 1) << 8)
    boots = w(0x7C)
    rc = b(0x7E)
    start = "starts since the image was written: %s, last start's RCON %02X (%s)" % (
        "none" if boots == 0xFFFF else boots, rc,
        "never written" if rc == 0xFF else "not modelled (the emulator)" if rc == 0 else
        "watchdog reset" if not rc & 0x08 else "brown-out" if not rc & 0x01 else
        "power-on" if not rc & 0x02 else "reset instruction" if not rc & 0x10 else "MCLR or other")
    if b(0x80) != ord("N"):
        return start + "\nno network report (EEPROM 0x80 is %02X)" % b(0x80)
    head = " ".join("%02X" % b(0x9A + i) for i in range(8))
    return "\n".join([
        start,
        "network report, format %d (every 40 s since the start)" % b(0x81),
        "  state now %s, furthest %s, tries %d, next hop known %d" % (
            NET_STATE.get(b(0x82), "?"), NET_STATE.get(b(0x83), "?"), b(0x84), b(0x85)),
        "  frames: %d received (%d refused by the card), %d sent (%d failed)" % (w(0x86), w(0x88), w(0x8A), w(0x8C)),
        "  received: %d not SNAP, %d ARP, %d IP, %d DHCP replies, %d DNS replies" % (
            w(0x8E), w(0x90), w(0x92), w(0x94), w(0x96)),
        "  last frame: status %04X, first bytes %s" % (w(0x98), head),
        "  DHCP: address %s mask %s gateway %s DNS %s" % (ip(mem, 0xA2), ip(mem, 0xA6), ip(mem, 0xAA), ip(mem, 0xAE)),
        "  DNS: the Terrier at %s" % ip(mem, 0xB2),
        "  TCP: %d connect(s), %d established, %d refused, %d reset, %d timed out; now %s" % (
            b(0xB6), b(0xB7), b(0xB8), b(0xB9), b(0xBA), TCP_STATE.get(b(0xBB), "?")),
        "  HTTP: phase %d, status 2xx %d, polls answered %d" % (b(0xBC), b(0xBD), w(0xBE)),
        "  card events: receive raised %d (BAP refused %d), every EVSTAT bit seen %04X" % (w(0xC0), w(0xC2), w(0xC4)),
        "  transmit: %d never confirmed, last frame status %04X, transmit command STATUS %04X" % (w(0xC6), w(0xC8), w(0xCA)),
        "  last frame sent: dst %s src %s len %s, then %s" % (
            ":".join("%02x" % b(0xCC + i) for i in range(6)), ":".join("%02x" % b(0xD2 + i) for i in range(6)),
            "%02x%02x" % (b(0xD8), b(0xD9)), " ".join("%02x" % b(0xDA + i) for i in range(2))),
        "  link: BSSID %s, port status %d, SSID %r" % (
            ":".join("%02x" % b(0xDC + i) for i in range(6)), w(0xE2),
            bytes(b(0xE6 + i) for i in range(min(b(0xE4), 8))).decode("latin-1") if b(0xE4) != 0xFF else "?"),
        "  our MAC (read from the card, in our frames): %s" % ":".join("%02x" % b(0xEE + i) for i in range(6)),
    ])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--log":
        m = from_log(sys.argv[2])
        print(report(m)); print(net_report(m))
    elif len(sys.argv) == 2:
        m = from_hex(sys.argv[1])
        print(report(m)); print(net_report(m))
    else:
        sys.exit(__doc__)
