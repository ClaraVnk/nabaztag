#!/usr/bin/env python3
"""A stand-in for Mindscape's platform (platform.karotz.com:9123), to read
what a Karotz's own controller says to it.

The controller dials the platform, and every message either way is a
4-byte big-endian length then a net.violet.voos.message.VoosMsg protobuf
(Voos-message.proto, github.com/dirk-thomas/karotz). This sends an
AuthenticationToken, prints every field of every message that comes back,
and tries the obvious MD5 formulas on the AuthenticationResponse against
the test credentials boot.sh wrote into voos.conf.

No protobuf library: the few fields used are encoded by hand, and replies
are printed as raw (field number: value) trees.

    fake_platform.py [--port 9123] [--id ID] [--pwd PWD] [--token TOKEN]
"""
import argparse
import hashlib
import socket
import struct
import time

AUTH_TOKEN = 400        # VoosMsg.authenticationToken
AUTH_RESPONSE = 401     # VoosMsg.authenticationResponse
PING = 1000             # VoosMsg.ping


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(num, payload):
    """A length-delimited field (strings and sub-messages)."""
    return varint(num << 3 | 2) + varint(len(payload)) + payload


def zigzag(n):
    return (n << 1) ^ (n >> 31)


def vfield(num, n):
    """A varint field (ints, bools, enums)."""
    return varint(num << 3) + varint(n)


# What the platform asks once the rabbit is in: Voos-message.proto's fields
# (interactiveMode 201, led 102 light 4, ears 101 sint32, tts 104).
IID = b"terrier-interactive-1"
UUID = b"terrier-instance-1"
# Config (VoosMsg field 600): one app, deployed "external" -- it runs on a
# server, nothing to download -- with one instance. The interactive mode is
# then opened in that instance's name (configId), the way Mindscape's
# platform opened it for an app (2026-11-17: without it, "handKill").
CONFIG = field(600, field(1,
    field(1, b"terrier") + field(2, b"1") + field(3, b"Le Terrier") + field(4, b"external")
    + field(100, field(13, b"terrier") + field(14, UUID))))
SCRIPT = [
    ("Config: app terrier (external), instance terrier-instance-1", CONFIG),
    ("interactive mode START", field(201, vfield(1, 1) + field(2, IID) + field(10, UUID))),
    ("LED 00FF00", field(3, IID) + field(102, field(4, field(1, b"00FF00")))),
    ("ears left 5 right -3", field(3, IID) + field(101, vfield(1, zigzag(5)) + vfield(2, zigzag(-3)))),
    ("TTS", field(3, IID) + field(104, vfield(1, 1) + field(2, b"Bonjour le Terrier") + field(3, b"fr-FR"))),
]


def parse(buf):
    """Raw protobuf -> [(field, value)], sub-messages parsed when they parse."""
    out, i = [], 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
            try:
                sub = parse(v)
                if sub and not all(32 <= c < 127 for c in v):
                    v = sub
            except (IndexError, ValueError):
                pass
        elif wire == 5:
            v, i = buf[i:i + 4], i + 4
        elif wire == 1:
            v, i = buf[i:i + 8], i + 8
        else:
            raise ValueError(f"wire type {wire}")
        out.append((num, v))
    return out


def _varint(buf, i):
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def show(msg, depth=0):
    for num, v in msg:
        if isinstance(v, list):
            print("  " * depth + f"{num}:")
            show(v, depth + 1)
        else:
            print("  " * depth + f"{num}: {v!r}")


def recv(conn):
    head = conn.recv(4, socket.MSG_WAITALL)
    if len(head) < 4:
        return None
    n, = struct.unpack(">I", head)
    return conn.recv(n, socket.MSG_WAITALL)


def send(conn, body):
    conn.sendall(struct.pack(">I", len(body)) + body)


def guesses(ident, pwd, token):
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
    return {
        "md5(token+pwd)": md5(token + pwd), "md5(pwd+token)": md5(pwd + token),
        "md5(id+token+pwd)": md5(ident + token + pwd), "md5(id+pwd+token)": md5(ident + pwd + token),
        "md5(token+id+pwd)": md5(token + ident + pwd), "md5(token+md5(pwd))": md5(token + md5(pwd)),
        "md5(md5(pwd)+token)": md5(md5(pwd) + token), "md5(pwd)": md5(pwd),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9123)
    ap.add_argument("--id", default="0123456789abcdef")
    ap.add_argument("--pwd", default="terrier-test-pwd")
    ap.add_argument("--token", default="0f1e2d3c4b5a6978")
    a = ap.parse_args()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", a.port))
    srv.listen(1)
    print(f"platform: listening on {a.port}", flush=True)
    while True:
        conn, peer = srv.accept()
        print(f"platform: the controller connected from {peer[0]}", flush=True)
        token = field(AUTH_TOKEN, field(1, a.token.encode()))
        send(conn, field(1, b"terrier-auth-1") + token)
        print(f"platform: sent AuthenticationToken {a.token!r}", flush=True)
        while True:
            body = recv(conn)
            if body is None:
                print("platform: the controller closed", flush=True)
                break
            msg = parse(body)
            print(f"platform: {len(body)} bytes from the controller:", flush=True)
            show(msg)
            ids = [v for num, v in msg if num == 1]
            if any(num == PING for num, _ in msg):
                # A ping answered with a ping that names it (correlationId).
                send(conn, field(1, b"terrier-pong") + field(2, ids[0]) + field(PING, b""))
                print("platform: answered the ping", flush=True)
            for num, v in msg:
                if num == AUTH_RESPONSE and isinstance(v, list):
                    got = {n: x.decode("latin-1") for n, x in v if isinstance(x, bytes)}
                    for name, h in guesses(a.id, a.pwd, a.token).items():
                        if h == got.get(2, "").lower():
                            print(f"platform: authentication = {name}", flush=True)
                    print(f"platform: login {got.get(1)!r}, authentication {got.get(2)!r}", flush=True)
                    for k, (what, body) in enumerate(SCRIPT):
                        send(conn, field(1, b"terrier-cmd-%d" % k) + body)
                        print(f"platform: sent {what}", flush=True)
                        time.sleep(2)


if __name__ == "__main__":
    main()
