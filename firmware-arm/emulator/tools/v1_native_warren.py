#!/usr/bin/env python3
"""A server shaped like the Terrier's p3.jsp for the native v1 firmware's
proofs: prints each request and answers 7F, a frame 03 (poll interval 1),
FF 0A -- what the Terrier sends a rabbit it has no program for. With
--offer, the answer also carries our frame 10 (the image version the server
holds, two bytes) and GET /vl/v1/fw serves --image, as DESIGN.md section 8
asks of the Terrier.

    python3 tools/v1_native_warren.py 28160 [--offer 31 --image app-31.img]
"""
import argparse
import socket

p = argparse.ArgumentParser()
p.add_argument("port", type=int)
p.add_argument("--offer", type=int, default=0, help="frame 10: the version offered")
p.add_argument("--image", help="the file GET /vl/v1/fw serves")
p.add_argument("--leds", help="frame 12 in the second answer: five LEDs (bits R1 G2 B4, top first) and seconds, as 1,2,4,3,7,3")
p.add_argument("--sound", help="frame 13 in the second answer: an Audi file, served at GET /vl/v1/snd")
p.add_argument("--asleep", type=int, default=0, help="frame 14 in the third answer: asleep for MINUTES")
p.add_argument("--config", nargs=2, metavar=("SERVER", "SSID"), help="frame 15 in the third answer")
p.add_argument("--rest", type=int, help="frame 16 with every answer: the base's breathing colour (bits R1 G2 B4)")
p.add_argument("--ears", type=int, nargs=2, metavar=("LEFT", "RIGHT"),
               help="frame 11: the ears' places, 0-16 holes from the top, with every answer")
a = p.parse_args()

BODY = bytes([0x7F, 0x03, 0, 0, 1, 1])
if a.offer:
    BODY += bytes([0x10, 0, 0, 2, a.offer >> 8, a.offer & 0xFF])
if a.ears:
    BODY += bytes([0x11, 0, 0, 2, a.ears[0], a.ears[1]])
if a.rest is not None:
    BODY += bytes([0x16, 0, 0, 1, a.rest])
BODY += bytes([0xFF, 0x0A])
# Frames 12 and 13 once, in the second answer (the first one confirms the
# image and starts the ears): the LEDs ordered, the sound offered.
ONCE = b""
if a.leds:
    v = [int(x) for x in a.leds.split(",")]
    ONCE += bytes([0x12, 0, 0, 6] + v[:6])
SOUND = open(a.sound, "rb").read() if a.sound else b""
if SOUND:
    ONCE += bytes([0x13, 0, 0, 4, 1]) + len(SOUND).to_bytes(3, "big")
THIRD = b""
if a.asleep:
    THIRD += bytes([0x14, 0, 0, 2]) + a.asleep.to_bytes(2, "big")
if a.config:
    tlv = b""
    for t, v in ((1, a.config[0]), (2, a.config[1])):
        tlv += bytes([t, len(v)]) + v.encode()
    THIRD += bytes([0x15]) + len(tlv).to_bytes(3, "big") + tlv
polls = 0

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", a.port))
s.listen(4)
while True:
    c, _ = s.accept()
    c.settimeout(5)
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    first = data.split(b"\r\n")[0].decode("latin-1", "replace")
    print("REQUEST", first, flush=True)
    if "/vl/v1/fw" in first and a.image:
        body = open(a.image, "rb").read()
        ctype = b"application/octet-stream"
    elif "/vl/v1/snd" in first and SOUND:
        body, ctype = SOUND, b"application/octet-stream"
    else:
        # Counted from each start (st=00): the once-frames come to every run
        # of a proof that starts the rabbit several times (the sound switch
        # on silent, 2026-11-26), not to the first run alone.
        polls = 1 if "st=00" in first else polls + 1
        body = BODY[:-2] + (ONCE if polls == 2 else THIRD if polls == 3 else b"") + BODY[-2:]
        ctype = b"text/html;charset=ISO-8859-1"
    head = (b"HTTP/1.0 200 OK\r\nContent-Type: " + ctype + b"\r\n"
            b"Content-Length: %d\r\n\r\n" % len(body))
    try:
        c.sendall(head + body)
    except OSError as e:
        print("SEND FAILED", e, flush=True)
    c.close()
