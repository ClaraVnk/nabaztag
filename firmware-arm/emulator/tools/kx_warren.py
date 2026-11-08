"""A warren that answers the adoption exchange, for whole-rabbit tests.

  python3 tools/kx_warren.py --port 8080 --bytecode <runtime.hybrid> [--no-kx]

/vl/kx.jsp?m=<mac>&p=<64 hex>  answers with its own public key in hex and
                               prints the key and the five colours it
                               expects the belly to show
/vl/bc.jsp                      serves --bytecode, wrapped amber...Mind
--no-kx                         a warren that predates kx.jsp: 404

X25519 and HKDF are OpenSSL's, through `cryptography` -- not the rabbit's
TweetNaCl and not the Terrier's kx.py -- so agreement means something.
"""
import argparse
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                              X25519PublicKey)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NAMES = ["red", "green", "blue", "yellow", "cyan", "magenta", "white", "orange"]
RGB = ["ff0000", "00ff00", "0000ff", "ffff00", "00ffff", "ff00ff", "ffffff", "ff8000"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bytecode", required=True)
    ap.add_argument("--no-kx", action="store_true")
    a = ap.parse_args()
    runtime = open(a.bytecode, "rb").read()
    if not runtime.startswith(b"amber"):
        runtime = b"amber" + b"%08x" % len(runtime) + runtime + b"Mind"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path.endswith("/kx.jsp"):
                if a.no_kx:
                    print("kx.jsp asked, answering 404", flush=True)
                    return self.send(404, b"not found")
                rpub = bytes.fromhex(q["p"][0])
                w = X25519PrivateKey.generate()
                wpub = w.public_key().public_bytes(serialization.Encoding.Raw,
                                                   serialization.PublicFormat.Raw)
                shared = w.exchange(X25519PublicKey.from_public_bytes(rpub))
                okm = HKDF(hashes.SHA256(), 36, b"nabaztag adoption v1",
                           b"nabkx" + rpub + wpub).derive(shared)
                bits = (okm[32] << 8) | okm[33]
                cols = [(bits >> (3 * (4 - i))) & 7 for i in range(5)]
                print(f"kx: rabbit {q.get('m', ['?'])[0]} pub {rpub.hex()[:16]}..", flush=True)
                print("kx: expected colours " + " ".join(f"#{RGB[c]}" for c in cols)
                      + "  (" + " ".join(NAMES[c] for c in cols) + ")", flush=True)
                print(f"kx: key {okm[:4].hex()}..", flush=True)
                return self.send(200, wpub.hex().encode())
            if u.path.endswith("/bc.jsp"):
                print(f"bc.jsp asked: {u.query}", flush=True)
                return self.send(200, runtime)
            self.send(404, b"")

    print(f"kx_warren on 127.0.0.1:{a.port}", flush=True)
    HTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
