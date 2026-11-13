#!/usr/bin/env python3
"""A radio and a podcast for the emulated rabbit, with nothing to decode.

    tools/fake_radio.py --port 19781 [--kbps 128] [--episode-bytes 600000]

    GET /feed.xml      a podcast feed: episode 0 is /episode.mp3, 1 is /radio.mp3
    GET /radio.mp3     endless: two seconds at once, as an Icecast does on
                       connect, then --kbps in real time
    GET /episode.mp3   --episode-bytes bytes as fast as they are taken;
                       a Range request is honoured (206)

What is served is not audio. Every four bytes are their own position in the
stream, big-endian -- for the radio with the number of the connection in
the top byte -- so that what reached the rabbit's decoder (rabbit-vm
--audio-dir keeps it) can be checked for a byte lost, repeated or out of
place anywhere between here and there:

    tools/fake_radio.py --check audio/clip-001.bin

prints the runs it found. An episode played whole is one run from 0; a
radio is one run per connection the warren made to it.

The Terrier refuses a feed or a stream on a private address
(outbound.check) unless TERRIER_WEBHOOK_ALLOW_PRIVATE=1, which
tools/xmpp-tls-proof.sh --fake-radio sets on the container it starts.
"""

from __future__ import annotations

import argparse
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BURST_S = 2
PIECE = 1024


def words(first: int, count: int, top: int = 0) -> bytes:
    return b"".join(struct.pack(">I", (top << 24) | ((first + i) & 0xFFFFFF))
                    for i in range(count))


class Handler(BaseHTTPRequestHandler):
    kbps = 128
    episode_bytes = 600_000
    connections = 0
    lock = threading.Lock()

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/feed.xml":
            host = self.headers.get("Host") or "127.0.0.1"
            body = (f"<?xml version='1.0'?><rss version='2.0'><channel><title>Fake radio</title>"
                    f"<item><title>An episode</title><enclosure url='http://{host}/episode.mp3' "
                    f"type='audio/mpeg' length='{self.episode_bytes}'/></item>"
                    f"<item><title>A station</title><enclosure url='http://{host}/radio.mp3' "
                    f"type='audio/mpeg'/></item></channel></rss>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/episode.mp3":
            self.episode()
        elif path == "/radio.mp3":
            self.radio()
        else:
            self.send_error(404)

    def episode(self):
        start, total = 0, self.episode_bytes
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes=") and rng[6:].rstrip("-").isdigit():
            start = min(int(rng[6:].rstrip("-")), total)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{total - 1}/{total}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(total - start))
        self.end_headers()
        sent = start
        try:
            while sent < total:
                first = sent // 4
                piece = words(first, PIECE // 4 + 1)[sent % 4:][:min(PIECE, total - sent)]
                self.wfile.write(piece)
                sent += len(piece)
            self.log_message("episode: served %d..%d", start, sent)
        except OSError:
            self.log_message("episode: the listener left at %d", sent)

    def radio(self):
        with Handler.lock:
            Handler.connections += 1
            conn = Handler.connections
        rate = self.kbps * 125
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.end_headers()
        sent, t0 = 0, time.monotonic()
        try:
            while True:
                due = int((time.monotonic() - t0 + BURST_S) * rate) & ~3
                while sent < due:
                    n = min(PIECE, due - sent) & ~3
                    if not n:
                        break
                    self.wfile.write(words(sent // 4, n // 4, conn))
                    sent += n
                time.sleep(0.05)
        except OSError:
            self.log_message("radio: listener %d left after %d bytes, %.0f s",
                             conn, sent, time.monotonic() - t0)


def check(path: str) -> int:
    data = open(path, "rb").read()
    # The player ends a sound with 2048 zeros (audiolib.mtl, wavgetzeros).
    body = data[:-2048] if len(data) >= 2048 and not any(data[-2048:]) else data
    runs, i = [], 0
    while i + 4 <= len(body):
        w = struct.unpack_from(">I", body, i)[0]
        if runs and runs[-1][2] == i and runs[-1][3] + 1 == w:
            runs[-1][2], runs[-1][3] = i + 4, w
        else:
            runs.append([i, w, i + 4, w])
        i += 4
    if len(runs) > 12:
        # A sentence, a jingle: a sound that did not come from here.
        print(f"{path}: {len(data)} bytes, not this radio's ({len(runs)} runs)")
        return 0
    print(f"{path}: {len(data)} bytes, {len(body)} of stream, {len(runs)} run(s)")
    for at, first, end, last in runs:
        print(f"  bytes {at}..{end}: connection {first >> 24}, stream bytes "
              f"{(first & 0xFFFFFF) * 4}..{(last & 0xFFFFFF) * 4 + 4}")
    if len(body) % 4:
        print(f"  and {len(body) % 4} byte(s) of a last word")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int)
    ap.add_argument("--kbps", type=int, default=128)
    ap.add_argument("--episode-bytes", type=int, default=600_000)
    ap.add_argument("--check", metavar="CLIP")
    args = ap.parse_args()
    if args.check:
        return check(args.check)
    if not args.port:
        ap.error("--port or --check")
    Handler.kbps, Handler.episode_bytes = args.kbps, args.episode_bytes
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"fake radio on 127.0.0.1:{args.port}: /feed.xml, /radio.mp3 at {args.kbps} kbit/s, "
          f"/episode.mp3 of {args.episode_bytes} bytes", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
