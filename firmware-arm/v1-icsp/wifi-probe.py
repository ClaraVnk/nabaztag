#!/usr/bin/env python3
"""Lazare's 1.4b with a probe that keeps the Wi-Fi card's station firmware.

    firmware-arm/v1-icsp/wifi-probe.py <in.hex> <out.hex>   the probe image
    firmware-arm/v1-icsp/wifi-probe.py --read <read.hex>   what it kept

Why (2026-11-24): WPA2 on the v1 is parked until the card's station firmware
version is known (1.7.0 at least for host-side encryption, hostap_hw.c), and
neither Violet firmware ever reads it (IMPROVEMENTS.md section 6). This adds
88 bytes in the free end of the 1.4b's program (0xBFA8-0xBFFF) and moves one
call: where the firmware sends ENABLE to the card (0x299A, command 0x0001),
it first runs the probe, which

- reads the station identity record (RID 0xFD20): ACCESS 0x0021 through
  the firmware's own command routine (0x35C8), BAP0 select (0x348C, which
  waits for the card), six words from DATA0 (register 0x36, read by 0x372C);
- keeps the low byte of each in the PIC's data EEPROM, 0x000-0x007, which
  neither firmware uses (blank in the dump): length 05, type 20 (FD20),
  component id, variant, major, minor, then two bytes past the record (the
  loop ends on bit 3 of the address, 2 bytes shorter than a compare);
  `read.sh` reads them back over ICSP with the program (`05201F010107` at
  0xF00000 for 1.7.1);
- puts ENABLE back in the command word and jumps to 0x35C8, so the firmware
  goes on exactly as before with ENABLE's result.

Not the serial port: Lazare writes no text on J3 (v1-serial/README.md,
2026-11-22), so a version printed there would be lost. The 55h/AAh unlock
runs with interrupts on: an interrupt inside it makes the PIC refuse that
byte, which then reads FF, never a wrong version (no room left to mask
them). WREN stays set after: a write still needs the unlock.

Only the 1.4b dump (sha256 74a9d1be...) is accepted: every address above
is read from it, and every byte this changes is checked first.
"""
import hashlib
import sys

SHA = "74a9d1be"
HOOK = 0x299A      # call 0x35C8 sending ENABLE (EE=0x01, EF=0 set just before)
PROBE = 0xBFA8     # first free byte after the program (0xBFA2-0xBFA7 is code)
END = 0xC000       # PIC18LF6525: 48 KB


def call(k):
    return [0xEC00 | ((k >> 1) & 0xFF), 0xF000 | ((k >> 9) & 0xFFF)]


def goto(k):
    return [0xEF00 | ((k >> 1) & 0xFF), 0xF000 | ((k >> 9) & 0xFFF)]


def movlw(k): return [0x0E00 | k]
def movwf_b(f): return [0x6F00 | f]          # banked, BSR = 0 (set at 0x2994)
def clrf_b(f): return [0x6B00 | f]
def movwf_a(f): return [0x6E00 | f]          # access bank: SFRs, 0x00-0x5F
def clrf_a(f): return [0x6A00 | f]
def movf_w(f): return [0x5000 | f]
def incf_a(f): return [0x2A00 | f]
def bsf_a(f, b): return [0x8000 | (b << 9) | f]
def btfsc_a(f, b): return [0xB000 | (b << 9) | f]
def btfss_a(f, b): return [0xA000 | (b << 9) | f]


EECON1, EECON2, EEDATA, EEADR, EEADRH = 0xA6, 0xA7, 0xA8, 0xA9, 0xAA


def bra(here, to):
    return [0xD000 | (((to - here - 2) >> 1) & 0x7FF)]


def probe():
    w = []
    pc = lambda: PROBE + 2 * len(w)
    w += movlw(0x20) + movwf_b(0xFA) + movlw(0xFD) + movwf_b(0xFB)
    w += movlw(0x02) + call(0x3754)                  # PARAM0 <- 0xFD20
    w += movlw(0x21) + movwf_b(0xEE) + clrf_b(0xEF)
    w += call(0x35C8)                                # ACCESS (read RID)
    w += movlw(0x20) + movwf_b(0xEE) + movlw(0xFD) + movwf_b(0xEF)
    w += clrf_b(0xF0) + clrf_b(0xF1)
    w += call(0x348C)                                # BAP0 <- FD20, offset 0
    w += clrf_a(EEADRH) + clrf_a(EEADR)
    loop = pc()
    w += movlw(0x36) + call(0x372C)                  # DATA0 -> 0x00 lo, 0x01 hi
    w += movf_w(0x00) + movwf_a(EEDATA)
    w += movlw(0x04) + movwf_a(EECON1)               # WREN, data EEPROM
    w += movlw(0x55) + movwf_a(EECON2) + movlw(0xAA) + movwf_a(EECON2)
    w += bsf_a(EECON1, 1)                            # WR
    wait = pc()
    w += btfsc_a(EECON1, 1)
    w += bra(pc(), wait)
    w += incf_a(EEADR)
    w += btfss_a(EEADR, 3)                           # eight bytes, 0x000-0x007
    w += bra(pc(), loop)
    w += movlw(0x01) + movwf_b(0xEE) + clrf_b(0xEF)  # ENABLE again
    w += goto(0x35C8)
    return w


def load(path):
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
                mem[base + addr + i] = x
    return mem


def put(mem, at, words):
    for i, x in enumerate(words):
        mem[at + 2 * i] = x & 0xFF
        mem[at + 2 * i + 1] = x >> 8


def save(mem, path):
    out, upper = [], None
    for a in range(0, max(mem) + 1, 16):
        chunk = [mem.get(a + i) for i in range(16)]
        if all(c is None for c in chunk):
            continue
        if a >> 16 != upper:
            upper = a >> 16
            r = bytes([2, 0, 0, 4, upper >> 8, upper & 0xFF])
            out.append(r + bytes([(-sum(r)) & 0xFF]))
        i = 0
        while i < 16:                      # runs of known bytes, as read
            if chunk[i] is None:
                i += 1
                continue
            j = i
            while j < 16 and chunk[j] is not None:
                j += 1
            lo = (a + i) & 0xFFFF
            r = bytes([j - i, lo >> 8, lo & 0xFF, 0]) + bytes(chunk[i:j])
            out.append(r + bytes([(-sum(r)) & 0xFF]))
            i = j
    out.append(bytes([0, 0, 0, 1, 0xFF]))
    open(path, "w").write("".join(":" + r.hex().upper() + "\n" for r in out))


def main(src, dst):
    if not hashlib.sha256(open(src, "rb").read()).hexdigest().startswith(SHA):
        sys.exit(f"{src} is not Lazare's 1.4b dump ({SHA}...)")
    mem = load(src)
    if [mem[HOOK + i] for i in range(4)] != [0xE4, 0xEC, 0x1A, 0xF0]:
        sys.exit("0x299A is not call 0x35C8")
    if [mem[HOOK - 8 + i] for i in range(8)] != [0x01, 0x0E, 0x00, 0x01,
                                                  0xEE, 0x6F, 0xEF, 0x6B]:
        sys.exit("0x2992 does not set ENABLE")
    w = probe()
    if PROBE + 2 * len(w) > END:
        sys.exit(f"probe is {2 * len(w)} bytes, {END - PROBE} free")
    if any(mem.get(a, 0xFF) != 0xFF for a in range(PROBE, END)):
        sys.exit("0xBFA8-0xBFFF is not blank")
    put(mem, PROBE, w)
    put(mem, HOOK, call(PROBE))
    save(mem, dst)
    print(f"probe {2 * len(w)} bytes at 0x{PROBE:04X}, hook at 0x{HOOK:04X}")


def report(path):
    """The version from a dump read after the probed rabbit has booted."""
    mem = load(path)
    ee = [mem.get(0xF00000 + i) for i in range(6)]
    if None in ee:
        sys.exit(f"{path} holds no data EEPROM (0xF00000)")
    print("EEPROM 0x000-0x005:", " ".join(f"{x:02X}" for x in ee))
    if ee[:2] != [0x05, 0x20]:
        sys.exit("no station identity record there (05 20 expected): "
                 "the probe did not run, or the card refused the read")
    if 0xFF in ee[2:]:
        print("a byte reads FF: an interrupt hit its write; boot it again")
    print(f"station firmware {ee[4]}.{ee[5]}.{ee[3]} (component 0x{ee[2]:02X})"
          f": WPA2 needs 1.7.0 or later")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--read"]:
        report(sys.argv[2])
    else:
        main(*sys.argv[1:3])
