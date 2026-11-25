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
a = p.parse_args()

BODY = bytes([0x7F, 0x03, 0, 0, 1, 1])
if a.offer:
    BODY += bytes([0x10, 0, 0, 2, a.offer >> 8, a.offer & 0xFF])
BODY += bytes([0xFF, 0x0A])

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
    else:
        body, ctype = BODY, b"text/html;charset=ISO-8859-1"
    head = (b"HTTP/1.0 200 OK\r\nContent-Type: " + ctype + b"\r\n"
            b"Content-Length: %d\r\n\r\n" % len(body))
    try:
        c.sendall(head + body)
    except OSError as e:
        print("SEND FAILED", e, flush=True)
    c.close()
