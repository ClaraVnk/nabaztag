"""crypto/nab_tls.c against two TLS stacks we did not write.

  python3 tls_interop.py [--client /tmp/t_tls_client] [--keep DIR]

Builds nothing: `make tls` compiles tls_client.c first and calls this.
For each of

  * `openssl s_server -tls1_3 -psk ... -allow_no_dhe_kex -rev` (the system
    OpenSSL, with -trace so the record-by-record transcript is kept), and
  * Python's `ssl` with `set_psk_server_callback` (what the Terrier runs),

the client connects, completes the handshake, sends a message, gets it
back, exchanges close_notify. Two messages: a short one, and one of 9,000
bytes, which the server may only return in records of 4,096 bytes or less
(max_fragment_length 2^12, RFC 6066), so a server that ignored the request
would fail the run with record_overflow instead of passing quietly.

A fake server written here would agree with the client's mistakes -- the
emulator's access point did, three times, on WPA3. These two do not.
Exit status: 0 when every run passed.

--keep saves the transcripts; the OpenSSL short run's dump is what
vectors_tls.py turns into the offline replay in test_tls.c.
"""
import argparse
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

PSK = bytes(range(32))
IDENTITY = "001d92aabbcc"
# The client's 32 random bytes, fixed so the OpenSSL transcript replays.
RANDOM = bytes(0xa0 + i for i in range(32))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_client(client, port, message, dump=None):
    cmd = [client, "127.0.0.1", str(port), PSK.hex(), IDENTITY, message, "--random", RANDOM.hex()]
    if dump:
        cmd += ["--dump", dump]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr


def openssl_server(port, log):
    cmd = ["openssl", "s_server", "-accept", f"127.0.0.1:{port}", "-tls1_3",
           "-psk", PSK.hex(), "-psk_identity", IDENTITY,
           "-ciphersuites", "TLS_AES_128_GCM_SHA256", "-nocert",
           "-allow_no_dhe_kex", "-rev", "-trace"]
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


class PyServer(threading.Thread):
    """Python's ssl module as the Terrier uses it: an external PSK looked
    up by identity, TLS 1.3 only, psk_ke allowed, no tickets. Echoes what
    it reads in 4,096-byte sends, then unwraps."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.report = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(1)

    def run(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        # SSL_OP_ALLOW_NO_DHE_KEX (openssl/ssl.h bit 10): without it OpenSSL
        # insists on a key share and answers our psk_ke-only hello with
        # handshake_failure. Python has no name for it; the number is stable
        # across 1.1.1 and 3.x.
        ctx.options |= 0x400
        ctx.num_tickets = 0
        ctx.set_psk_server_callback(
            lambda identity: PSK if identity == IDENTITY else None)
        conn, _ = self.sock.accept()
        try:
            t = ctx.wrap_socket(conn, server_side=True)
            self.report.append(f"version {t.version()} cipher {t.cipher()[0]}")
            total = 0
            while True:
                d = t.recv(16384)
                if not d:
                    break
                total += len(d)
                for i in range(0, len(d), 4096):
                    t.send(d[i:i + 4096])
            self.report.append(f"echoed {total} bytes")
            t.unwrap()
            self.report.append("close_notify exchanged")
        except Exception as e:  # noqa
            self.report.append(f"server error: {e!r}")
        finally:
            self.sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="/tmp/t_tls_client")
    ap.add_argument("--keep")
    a = ap.parse_args()
    work = a.keep or tempfile.mkdtemp(prefix="nab-tls-")
    os.makedirs(work, exist_ok=True)
    long_msg = "x" * 8997 + "END"
    fails = 0

    print(f"== OpenSSL: {subprocess.run(['openssl', 'version'], capture_output=True, text=True).stdout.strip()}")
    for name, msg in (("short", "hello rabbit\n"), ("long", long_msg + "\n")):
        port = free_port()
        log = open(os.path.join(work, f"openssl-{name}.log"), "w")
        srv = openssl_server(port, log)
        # No TCP probe: s_server would take it for the client. Wait for its
        # own ACCEPT line instead.
        for _ in range(100):
            if "ACCEPT" in open(log.name).read():
                break
            time.sleep(0.05)
        code, out = run_client(a.client, port, msg, os.path.join(work, f"openssl-{name}.dump"))
        time.sleep(0.3)
        srv.kill()
        srv.wait(timeout=10)
        log.close()
        trace = open(log.name).read()
        want = msg.strip()[::-1]            # -rev echoes each line reversed
        ok = (code == 0 and want[:20] in out and "handshake complete" in out
              and "max_fragment_length := 2^12" in trace
              and "NewSessionTicket" in trace)
        print(f"  {'ok  ' if ok else 'FAIL'}  openssl s_server, {name} message ({len(msg)} bytes), exit {code}")
        for line in out.splitlines():
            if line.startswith("client:") or not ok:
                print("        " + line[:110])
        fails += not ok

    print(f"== Python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}")
    for name, msg in (("short", "hello rabbit"), ("long", long_msg)):
        port = free_port()
        srv = PyServer(port)
        srv.start()
        code, out = run_client(a.client, port, msg, os.path.join(work, f"python-{name}.dump"))
        srv.join(timeout=10)
        ok = (code == 0 and "handshake complete" in out
              and any(r.startswith("version TLSv1.3") for r in srv.report)
              and f"echoed {len(msg)} bytes" in srv.report
              and "close_notify exchanged" in srv.report)
        print(f"  {'ok  ' if ok else 'FAIL'}  Python ssl, {name} message ({len(msg)} bytes), exit {code}")
        for r in srv.report:
            print("        server: " + r)
        for line in out.splitlines():
            if line.startswith("client:") or not ok:
                print("        " + line[:110])
        fails += not ok

    if a.keep:
        print(f"transcripts in {work}")
    elif not fails:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"transcripts in {work}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
