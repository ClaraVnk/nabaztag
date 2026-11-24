#!/usr/bin/env python3
"""A server shaped like the Terrier's p3.jsp for the native v1 firmware's
proof: prints each request and answers 7F, a frame 03 (poll interval 1),
FF 0A -- what the Terrier sends a rabbit it has no program for.

    python3 tools/v1_native_warren.py 28160
"""
import socket
import sys

BODY = bytes([0x7F, 0x03, 0, 0, 1, 1, 0xFF, 0x0A])

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
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
    print("REQUEST", data.split(b"\r\n")[0].decode("latin-1", "replace"), flush=True)
    head = (b"HTTP/1.0 200 OK\r\nContent-Type: text/html;charset=ISO-8859-1\r\n"
            b"Content-Length: %d\r\n\r\n" % len(BODY))
    c.sendall(head + BODY)
    c.close()
