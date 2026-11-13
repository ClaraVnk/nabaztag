#!/usr/bin/env python3
"""The least a warren can be, for a whole boot with nothing but Python.

  python3 tools/plain_warren.py --port 8080 --bytecode <runtime.hybrid>

/vl/bc.jsp       the runtime bytecode, wrapped amber<len>...Mind, no HMAC
/vl/locate.jsp   ping, broad and xmpp_domain, all pointing back here
anything else    404 -- kx.jsp included, so the bootloader takes this for a
                 warren that predates the adoption exchange and boots on

Each answer's size is printed: with the gateway's own "bytes down" lines
that is how the largest legitimate HTTP_NORMAL answer of the bootloader
(bc.jsp) and of the runtime (locate.jsp) are read off a run. tools/
kx_warren.py does the adoption exchange too, and needs `cryptography`.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bytecode", required=True)
    ap.add_argument("--host", default="10.0.0.1", help="what locate.jsp names")
    a = ap.parse_args()
    runtime = open(a.bytecode, "rb").read()
    if not runtime.startswith(b"amber"):
        runtime = b"amber" + b"%08x" % len(runtime) + runtime + b"Mind"
    locate = (f"ping {a.host}:{a.port}\nbroad {a.host}:{a.port}\n"
              f"xmpp_domain {a.host}:5222\n").encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            code, body = 404, b""
            if path.endswith("/bc.jsp"):
                code, body = 200, runtime
            elif path.endswith("/locate.jsp"):
                code, body = 200, locate
            print(f"{path}: {code}, {len(body)} bytes of body", flush=True)
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"plain_warren on 127.0.0.1:{a.port}", flush=True)
    HTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
