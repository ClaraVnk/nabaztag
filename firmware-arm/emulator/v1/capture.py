#!/usr/bin/env python3
"""A server that prints what a v1 asks and answers 404: the request, raw.

    python3 v1/capture.py 18080 [bind address, default 127.0.0.1]

Also the Karotz proof's server, in its own container (tools/karotz-proof.sh).
"""
import socket
import sys

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1", int(sys.argv[1])))
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
    print("REQUEST", repr(data), flush=True)
    c.sendall(b"HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n")
    c.close()
