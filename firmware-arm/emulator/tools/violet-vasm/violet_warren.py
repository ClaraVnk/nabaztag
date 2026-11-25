#!/usr/bin/env python3
"""Answer a v1's p3.jsp polls the way Violet's server did, for the emulator.

    python3 violet_warren.py --port 28080 --frame wait.frame --ears 5 10 --after 3
    python3 violet_warren.py ... --ears 5 10 --ears 2 3 --after 3 --every 4   # two orders
    sudo python3 violet_warren.py --host 0.0.0.0 --port 80 --frame wait.frame ...  # a real v1

The 05 frame is Violet's Nabaztag_wait.vasm put together by Violet's own
assembler (Containerfile here); the packet around it follows Violet's
MessageSerializer.generatePingPacket for a waiting rabbit: 7F, the 04 sources
(id 7FFFFFFF, eight empty (service, value) pairs, LEFT ear, RIGHT ear, 00),
the program when the rabbit does not already run it (tc), the 03 poll
interval, and FF. --ears changes the ears once the rabbit has polled --after
times, as an owner's order would; given twice, the next order follows --every
polls later. --messages N tells the rabbit N messages wait (a 5 per message
after the ears, two at most): a click then sends sd=0003 (2026-11-24: the
one-motor proof needs two orders and a click in one run). --source T V fills
the (service, value) pairs the belly shows (14 V: how often the rabbit moves
by itself). --down FROM TO closes polls FROM..TO without an answer, a server
gone away for a while (2026-11-24: the review's proofs).
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

EV_DUMMY = 0x7FFFFFFF


def frame(kind: int, body: bytes) -> bytes:
    return bytes([kind]) + len(body).to_bytes(3, "big") + body


def sources(left: int, right: int, messages: int = 0, pairs: list | None = None) -> bytes:
    waiting = bytes([5] * min(messages, 2))
    info = bytearray(16)
    for i, (service, value) in enumerate((pairs or [])[:8]):
        info[2 * i], info[2 * i + 1] = service & 255, value & 255
    return frame(4, EV_DUMMY.to_bytes(4, "big") + bytes(info) + bytes([left, right]) + waiting + b"\0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 for a real v1 on the LAN")
    ap.add_argument("--port", type=int, default=28080)
    ap.add_argument("--frame", required=True, help="the 05 frame (podman run violet-vasm ...)")
    ap.add_argument("--ears", type=int, nargs=2, metavar=("LEFT", "RIGHT"), action="append",
                    default=None, help="an order; repeat for the next ones")
    ap.add_argument("--after", type=int, default=3, help="polls before the first order")
    ap.add_argument("--every", type=int, default=4, help="polls between two orders")
    ap.add_argument("--interval", type=int, default=1, help="03 frame value")
    ap.add_argument("--messages", type=int, default=0, help="messages waiting for the owner")
    ap.add_argument("--source", type=int, nargs=2, metavar=("SERVICE", "VALUE"), action="append",
                    default=None, help="a (service, value) pair for the belly; up to eight")
    ap.add_argument("--down", type=int, nargs=2, metavar=("FROM", "TO"), default=None,
                    help="polls FROM..TO get no answer (the connection closes)")
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
                nth = min((state["polls"] - args.after - 1) // args.every, len(args.ears) - 1)
                left, right = args.ears[nth]
            tc = (q.get("tc") or [""])[0].upper()
            if args.down and args.down[0] <= state["polls"] <= args.down[1]:
                print(f"poll {state['polls']}: {self.path} -> no answer (server down)", flush=True)
                self.close_connection = True
                return
            body = b"\x7f" + sources(left, right, args.messages, args.source)
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

    ThreadingHTTPServer((args.host, args.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
