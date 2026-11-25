#!/usr/bin/env python3
"""Emit an Intel-HEX PIC18 stub that writes one flash row from reset.

    selfwrite_stub.py out.hex

The control for v1-icsp/VM-ESCAPE.md: eighteen native instructions that load a
holding latch with TBLWT and run the EECON1 flash-write sequence (WREN+EEPGD,
55h/AAh on EECON2, then WR), the one path Violet's firmware never takes. Run on
the emulator it prints "flash: write row 00100", so the hostile VM program's
silence is a measurement, not a model that cannot write flash.

DS39646C (PIC18F6627) / DS39612C (PIC18F6525) chapter 6: the write happens in
64-byte rows from 64 holding latches, armed by 55h then AAh on EECON2. The
standard instruction set (CONFIG4L XINST off), the SFRs at their access-bank
low bytes (TBLPTRU 0xF8 ... EECON1 0xA6, EECON2 0xA7).
"""
import sys

# (word) each 16-bit PIC18 instruction, little-endian in the image.
WORDS = [
    0x0E00,  # movlw 0x00
    0x6EF8,  # movwf TBLPTRU   (0xFF8)
    0x0E01,  # movlw 0x01
    0x6EF7,  # movwf TBLPTRH   (0xFF7)  -> TBLPTR = 0x000100, a blank scratch row
    0x0E00,  # movlw 0x00
    0x6EF6,  # movwf TBLPTRL   (0xFF6)
    0x0EA5,  # movlw 0xA5
    0x6EF5,  # movwf TABLAT    (0xFF5)
    0x000C,  # tblwt*          -> latch[0] = 0xA5
    0x0E84,  # movlw 0x84      EEPGD | WREN
    0x6EA6,  # movwf EECON1    (0xFA6)
    0x0E55,  # movlw 0x55
    0x6EA7,  # movwf EECON2    (0xFA7)
    0x0EAA,  # movlw 0xAA
    0x6EA7,  # movwf EECON2
    0x0E86,  # movlw 0x86      EEPGD | WREN | WR
    0x6EA6,  # movwf EECON1    -> flash: write row 00100
    0x0003,  # sleep           (halts the emulator)
]


def ihex_record(addr, data, rectype=0):
    n = len(data)
    body = [n, (addr >> 8) & 0xFF, addr & 0xFF, rectype] + list(data)
    chk = (-sum(body)) & 0xFF
    return ":" + "".join("%02X" % b for b in body + [chk])


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    img = bytearray()
    for w in WORDS:
        img.append(w & 0xFF)
        img.append((w >> 8) & 0xFF)
    lines = []
    for off in range(0, len(img), 16):
        lines.append(ihex_record(off, img[off:off + 16]))
    lines.append(":00000001FF")        # EOF
    with open(sys.argv[1], "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
