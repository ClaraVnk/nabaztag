#!/usr/bin/env python3
"""The v1 native firmware's images: what the Terrier serves and what ICSP programs.

    mkimage.py image --app app.hex --key KEY --out app.img
        The application as the Terrier serves it: a 64-byte header (board.h
        struct app_hdr: 'NBV1', format, board, version, length; the
        HMAC-SHA-256 at 16; the rest 0xFF) then the image, from 0x3000,
        rounded up to 64 bytes with 0xFF. The HMAC covers the header's first
        12 bytes and the image. Prints the HMAC.
    mkimage.py full --loader loader.hex --app app.hex --key KEY --out full.hex
        One hex for the PICkit and the emulator: the loader (with the CONFIG
        words), the application with its length patched in, the key in the
        data EEPROM (0xF00000). The loader seeds slot A from these bytes at
        the first boot, with the same HMAC as `image` prints.
    mkimage.py hmac --img app.img --key KEY
        Recompute an image's HMAC and compare (the proof's check).

KEY is a file with 32 bytes in hex (a line starting with # is a comment).
"""
import argparse
import hashlib
import hmac
import struct
import sys

APP_BASE = 0x3000
HDR_OFF = 0x40
HDR_SIZE = 64
HMAC_OFF = 16


def read_hex(path):
    mem, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        n, addr, kind = int(line[1:3], 16), int(line[3:7], 16), int(line[7:9], 16)
        data = bytes.fromhex(line[9:9 + 2 * n])
        if kind == 4:
            base = int(line[9:13], 16) << 16
        elif kind == 0:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif kind == 1:
            break
    return mem


def write_hex(path, mem):
    out, base = [], None
    addrs = sorted(mem)
    i = 0
    while i < len(addrs):
        a = addrs[i]
        if (a >> 16) != base:
            base = a >> 16
            out.append(record(0, 4, struct.pack(">H", base)))
        chunk = bytearray()
        while i < len(addrs) and addrs[i] == a + len(chunk) and len(chunk) < 16 and (addrs[i] >> 16) == base:
            chunk.append(mem[addrs[i]])
            i += 1
        out.append(record(a & 0xFFFF, 0, bytes(chunk)))
    out.append(":00000001FF")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def record(addr, kind, data):
    body = bytes([len(data), addr >> 8, addr & 0xFF, kind]) + data
    return ":" + body.hex().upper() + "%02X" % ((-sum(body)) & 0xFF)


def read_key(path):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            key = bytes.fromhex(line)
            if len(key) != 32:
                sys.exit("the key must be 32 bytes")
            return key
    sys.exit("no key in " + path)


def app_image(path):
    """The application's bytes from APP_BASE, length patched, and its header."""
    mem = read_hex(path)
    code = {a: b for a, b in mem.items() if APP_BASE <= a < 0x200000}
    if not code:
        sys.exit("no application code from 0x%X in %s" % (APP_BASE, path))
    top = max(code) + 1
    length = (top - APP_BASE + 63) & ~63
    img = bytearray(b"\xff" * length)
    for a, b in code.items():
        img[a - APP_BASE] = b
    hdr = img[HDR_OFF:HDR_OFF + 16]
    if hdr[:4] != b"NBV1" or hdr[4] != 1:
        sys.exit("the application has no header at 0x%X" % (APP_BASE + HDR_OFF))
    struct.pack_into("<I", img, HDR_OFF + 8, length)
    return bytes(img), bytes(img[HDR_OFF:HDR_OFF + 16]), mem


def sign(hdr12, img, key):
    return hmac.new(key, hdr12[:12] + img, hashlib.sha256).digest()


def cmd_image(a):
    img, hdr, _ = app_image(a.app)
    key = read_key(a.key)
    mac = sign(hdr, img, key)
    head = bytearray(b"\xff" * HDR_SIZE)
    head[:12] = hdr[:12]
    head[12:16] = b"\0\0\0\0"
    head[HMAC_OFF:HMAC_OFF + 32] = mac
    with open(a.out, "wb") as f:
        f.write(bytes(head) + img)
    board, version, length = hdr[5], struct.unpack_from("<H", hdr, 6)[0], len(img)
    print(f"image: board 0x{board:02X} version {version} length {length} hmac {mac.hex()}")


def cmd_full(a):
    img, hdr, _ = app_image(a.app)
    mem = read_hex(a.loader)
    for i, b in enumerate(img):
        mem[APP_BASE + i] = b
    key = read_key(a.key)
    for i, b in enumerate(key):
        mem[0xF00000 + i] = b
    write_hex(a.out, mem)
    mac = sign(hdr, img, key)
    print(f"full: loader {sum(1 for x in mem if x < APP_BASE)} B, app {len(img)} B, key in EEPROM; app hmac {mac.hex()}")


def cmd_hmac(a):
    data = open(a.img, "rb").read()
    head, img = data[:HDR_SIZE], data[HDR_SIZE:]
    mac = sign(head, img, read_key(a.key))
    ok = hmac.compare_digest(mac, head[HMAC_OFF:HMAC_OFF + 32])
    print(f"hmac {mac.hex()} {'matches' if ok else 'DOES NOT MATCH'} the header's")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("image"); s.add_argument("--app", required=True); s.add_argument("--key", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("full"); s.add_argument("--loader", required=True); s.add_argument("--app", required=True); s.add_argument("--key", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("hmac"); s.add_argument("--img", required=True); s.add_argument("--key", required=True)
    a = p.parse_args()
    {"image": cmd_image, "full": cmd_full, "hmac": cmd_hmac}[a.cmd](a)


if __name__ == "__main__":
    main()
