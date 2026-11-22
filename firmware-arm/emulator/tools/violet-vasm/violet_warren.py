#!/usr/bin/env python3
"""Answer a v1's p3.jsp polls the way Violet's server did, for the emulator.

    python3 violet_warren.py --port 28080 --frame wait.frame --ears 5 10 --after 3

The 05 frame is Violet's Nabaztag_wait.vasm put together by Violet's own
assembler (Containerfile here); the packet around it follows Violet's
MessageSerializer.generatePingPacket for a waiting rabbit: 7F, the 04 sources
(id 7FFFFFFF, eight empty (service, value) pairs, LEFT ear, RIGHT ear, 00),
the program when the rabbit does not already run it (tc), the 03 poll
interval, and FF. --ears changes the ears once the rabbit has polled --after
times, as an owner's order would.
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

EV_DUMMY = 0x7FFFFFFF


def frame(kind: int, body: bytes) -> bytes:
    return bytes([kind]) + len(body).to_bytes(3, "big") + body


def sources(left: int, right: int) -> bytes:
    return frame(4, EV_DUMMY.to_bytes(4, "big") + bytes(16) + bytes([left, right, 0]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=28080)
    ap.add_argument("--frame", required=True, help="the 05 frame (podman run violet-vasm ...)")
    ap.add_argument("--ears", type=int, nargs=2, metavar=("LEFT", "RIGHT"), default=None)
    ap.add_argument("--after", type=int, default=3)
    ap.add_argument("--interval", type=int, default=1, help="03 frame value")
    args = ap.parse_args()
    program = open(args.frame, "rb").read()
    assert program[0] == 5 and program[4:9] == b"amber" and program[-4:] == b"mind", "not a 05 frame"
    state = {"polls": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlsplit(self.path).query)
            state["polls"] += 1
            left, right = (0, 0)
            if args.ears and state["polls"] > args.after:
                left, right = args.ears
            tc = (q.get("tc") or [""])[0].upper()
            body = b"\x7f" + sources(left, right)
            if tc != f"{EV_DUMMY:08X}":
                body += program
            body += frame(3, bytes([args.interval])) + b"\xff"
            print(f"poll {state['polls']}: {self.path} -> ears L{left} R{right}"
                  f"{' + program' if tc != f'{EV_DUMMY:08X}' else ''}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/html;charset=ISO-8859-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
