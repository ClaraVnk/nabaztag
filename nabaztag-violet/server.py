#!/usr/bin/env python3
"""Minimal Nabaztag (Violet protocol) server — Phase 1.

A clean, dependency-free replacement for the dead Violet/OpenJabNab servers:
  * HTTP  : serves the original bootcode (/vl/bc.jsp) + /vl/locate.jsp
  * XMPP  : accepts the rabbit's connection with the documented SASL
            "success-bypass", keeps it alive, and can push violet:packet
            commands. Everything the rabbit sends is logged (RE platform).
  * API   : a small HTTP control API for Home Assistant (ears/led/sound/raw).

Protocol references: nabaztag.com/doc (InteractionServeurs, APIV2) and the
OpenJabNab source. Packet framing: 0x7F | type | len(3, big-endian) | data | 0xFF
sent base64-encoded inside <message><packet xmlns='violet:packet'>…</packet>.
"""
import base64
import json
import logging
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# Configuration (from /data/options.json or env)
# --------------------------------------------------------------------------- #
def _load_options():
    try:
        with open("/data/options.json", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}

_OPTS = _load_options()
SERVER_ADDRESS = os.environ.get("SERVER_ADDRESS") or _OPTS.get("server_address") or "192.0.2.10"
LOG_LEVEL = (os.environ.get("LOG_LEVEL") or _OPTS.get("log_level") or "Info").upper()
HTTP_PORT = int(os.environ.get("HTTP_PORT", _OPTS.get("http_port", 80)))
XMPP_PORT = int(os.environ.get("XMPP_PORT", _OPTS.get("xmpp_port", 5222)))
API_PORT = int(os.environ.get("API_PORT", _OPTS.get("api_port", 8080)))
# Which bytecode to serve at /vl/bc.jsp. The bytecode and the locate format are
# a matched pair: 'ojn' (OpenJabNab) expects the locate WITH the xmpp port (what
# we now send); 'violet' is the original; 'pub' is the live community server's
# (newest) build, fetched at build for comparison/RE.
BOOTCODE_CHOICE = (os.environ.get("BOOTCODE") or _OPTS.get("bootcode") or "ojn").lower()
BOOTCODE_FILE = os.environ.get(
    "BOOTCODE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), f"bootcode.{BOOTCODE_CHOICE}"),
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nabaztag")

# Registry of connected rabbits: mac -> XmppSession
BUNNIES: dict[str, "XmppSession"] = {}
BUNNIES_LOCK = threading.Lock()

# --------------------------------------------------------------------------- #
# Violet packet helpers
# --------------------------------------------------------------------------- #
# Packet framing (from OpenJabNab packet.cpp): each packet is
#   0x7F  type(1)  len(3, big-endian)  payload  0xFF
# Several can be concatenated. Sent base64'd inside the XMPP <packet> element.
def frame_packet(ptype: int, payload: bytes) -> bytes:
    n = len(payload)
    return bytes([0x7F, ptype & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + payload + bytes([0xFF])


# Packet type codes (OpenJabNab packet.h, verified): Ambient=0x04, Message=0x0A,
# Sleep=0x0B.
PKT_AMBIENT = 0x04
PKT_MESSAGE = 0x0A
PKT_SLEEP = 0x0B

# AmbientPacket services (ambientpacket.h). This drives the belly icons (weather,
# stock, mail, air quality), the ear positions and the nose blink.
SVC_DISABLE = 0x00
SVC_WEATHER = 0x01      # 0 Sun,1 Cloudy,2 Smog,3 Rain,4 Snow,5 Storm
SVC_STOCK = 0x02
SVC_PERIPH = 0x03
SVC_EAR_LEFT = 0x04    # value = ear position
SVC_EAR_RIGHT = 0x05   # value = ear position
SVC_EMAIL = 0x06
SVC_AIRQUALITY = 0x07
SVC_NOSE = 0x08        # 0 None,1 Blink,2 DoubleBlink
SVC_BOTTOMLED = 0x09
SVC_TAICHI = 0x0E
WEATHER = {"sun": 0, "cloudy": 1, "smog": 2, "rain": 3, "snow": 4, "storm": 5}


def ambient_internal(services: dict) -> bytes:
    """AmbientPacket internal data (ambientpacket.cpp): 0x7FFFFFFE header, then
    (service, value) byte pairs sorted by service id (Qt QMap order)."""
    out = bytes([0x7F, 0xFF, 0xFF, 0xFE])
    for k in sorted(services):
        out += bytes([k & 0xFF, services[k] & 0xFF])
    return out


def ambient_packet(services: dict) -> bytes:
    return frame_packet(PKT_AMBIENT, ambient_internal(services))


def pack_list(packets) -> bytes:
    """Concatenate packets like OpenJabNab Packet::GetData(list): a single leading
    0x7F, then {type, len(3), data} per packet, and one trailing 0xFF."""
    out = bytes([0x7F])
    for ptype, data in packets:
        n = len(data)
        out += bytes([ptype & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + data
    out += bytes([0xFF])
    return out


# The packet the rabbit needs as the answer to its violet:iq:sources query to
# finish booting (OpenJabNab Bunny::GetInitPacket): AmbientPacket (nose off, ears
# at 0) + SleepPacket(Wake_Up = 0).
INIT_PACKET = pack_list([
    (PKT_AMBIENT, ambient_internal({SVC_NOSE: 0, SVC_EAR_LEFT: 0, SVC_EAR_RIGHT: 0})),
    (PKT_SLEEP, bytes([0])),
])


# --------------------------------------------------------------------------- #
# XMPP server (one thread per rabbit)
# --------------------------------------------------------------------------- #
class XmppSession(threading.Thread):
    """Handles a single rabbit XMPP connection.

    State machine following InteractionServeurs.pdf:
      stream -> features(SASL) -> auth -> challenge -> response -> SUCCESS
      -> (stream restart) -> features(bind/session) -> bind -> session -> idle
    SASL is bypassed (we just answer <success/>) per the documented trick.
    """

    def __init__(self, conn: socket.socket, addr):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buf = ""
        self.mac = None
        self.authed = False
        self.bound = False
        self.domain = SERVER_ADDRESS
        self.resource = "Boot"
        self.msg_nb = 0
        self._alive = True

    # -- low level --------------------------------------------------------- #
    def send(self, data: str):
        try:
            self.conn.sendall(data.encode("utf-8"))
            log.debug("-> rabbit %s: %s", self.addr[0], data[:300])
        except OSError:
            self._alive = False

    @property
    def jid(self):
        return f"{self.mac or '0000'}@{self.domain}/{self.resource}"

    # -- command push ------------------------------------------------------ #
    def send_violet_packet(self, raw: bytes):
        self.msg_nb += 1
        b64 = base64.b64encode(raw).decode("ascii")
        stanza = (
            f"<message from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='ojn-{self.msg_nb}'>"
            f"<packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet>"
            f"</message>"
        )
        self.send(stanza)

    # -- handshake --------------------------------------------------------- #
    def _stream_header(self, features: str):
        self.send(
            "<?xml version='1.0'?>"
            f"<stream:stream xmlns='jabber:client' "
            f"xmlns:stream='http://etherx.jabber.org/streams' "
            f"id='nab-{os.urandom(4).hex()}' from='{self.domain}' "
            f"version='1.0' xml:lang='en'>" + features
        )

    def _features_sasl(self):
        return (
            "<stream:features>"
            "<mechanisms xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>"
            "<mechanism>DIGEST-MD5</mechanism><mechanism>PLAIN</mechanism>"
            "</mechanisms>"
            "<register xmlns='http://violet.net/features/violet-register'/>"
            "</stream:features>"
        )

    def _features_bind(self):
        return (
            "<stream:features>"
            "<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><required/></bind>"
            "<session xmlns='urn:ietf:params:xml:ns:xmpp-session'/>"
            "</stream:features>"
        )

    def _handle_stanza(self, frag: str):
        f = frag.strip()
        if not f:
            return
        log.info("<- rabbit %s: %s", self.addr[0], f[:400])

        if f.startswith("<auth"):
            # DIGEST-MD5: send one challenge, then we'll accept any response.
            challenge = base64.b64encode(
                f'realm="{self.domain}",nonce="{os.urandom(6).hex()}",'
                'qop="auth",charset=utf-8,algorithm=md5-sess'.encode()
            ).decode()
            # capture username (the MAC) if present
            m = re.search(r'username="?([0-9a-fA-F]{8,})"?', f)
            if m:
                self.mac = m.group(1).lower()
            self.send(f"<challenge xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>{challenge}</challenge>")
            return

        if f.startswith("<response"):
            m = re.search(r'username="?([0-9a-fA-F]{8,})"?', f)
            if m:
                self.mac = m.group(1).lower()
            # success-bypass: tell the rabbit it authenticated
            self.authed = True
            self.send("<success xmlns='urn:ietf:params:xml:ns:xmpp-sasl'/>")
            return

        if "urn:ietf:params:xml:ns:xmpp-bind" in f and "<iq" in f:
            # resource bind
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            res = re.search(r"<resource>\s*([^<]*?)\s*</resource>", f)
            if res and res.group(1).strip():
                self.resource = res.group(1).strip()
            frm = re.search(r"from='([0-9a-fA-F]{8,})@", f) or re.search(r'from="([0-9a-fA-F]{8,})@', f)
            if frm:
                self.mac = frm.group(1).lower()
            self.send(
                f"<iq type='result' id='{iq_id}'>"
                f"<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><jid>{self.jid}</jid></bind></iq>"
            )
            self._register()
            return

        if "urn:ietf:params:xml:ns:xmpp-session" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.bound = True
            self.send(f"<iq type='result' id='{iq_id}'/>")
            log.info("rabbit %s (%s) is now bound and idle — ready for commands", self.addr[0], self.mac)
            return

        if "violet:iq:sources" in f and "<iq" in f:
            # The rabbit asks for its config; answering with the init packet lets
            # it finish booting (otherwise it stays stuck in the boot resource).
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            frm = re.search(r"from='([^']*)'", f) or re.search(r'from="([^"]*)"', f)
            to = re.search(r"to='([^']*)'", f) or re.search(r'to="([^"]*)"', f)
            frm = frm.group(1) if frm else self.jid
            to = to.group(1) if to else f"net.violet.platform@{self.domain}/sources"
            b64 = base64.b64encode(INIT_PACKET).decode("ascii")
            self.send(
                f"<iq from='{to}' to='{frm}' type='result' id='{iq_id}'>"
                f"<query xmlns='violet:iq:sources'><packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet></query></iq>"
            )
            log.info("rabbit %s: answered violet:iq:sources (init packet) — boot should complete", self.addr[0])
            return

        if "<unbind" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.send(f"<iq type='result' id='{iq_id}'/>")
            self.bound = True
            log.info("rabbit %s: boot unbind done — now operational, ready for commands", self.addr[0])
            return

        if f.startswith("<iq") and "ping" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.send(f"<iq type='result' id='{iq_id}'/>")
            return
        # Anything else (events: button/RFID, presence, packets) -> logged above.

    def _register(self):
        if self.mac:
            with BUNNIES_LOCK:
                BUNNIES[self.mac] = self
            log.info("registered bunny mac=%s from %s", self.mac, self.addr[0])

    def run(self):
        log.info("XMPP connection from %s", self.addr[0])
        self.conn.settimeout(900)
        try:
            while self._alive:
                try:
                    chunk = self.conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                self._feed(chunk.decode("utf-8", "replace"))
        finally:
            if self.mac:
                with BUNNIES_LOCK:
                    if BUNNIES.get(self.mac) is self:
                        BUNNIES.pop(self.mac, None)
            try:
                self.conn.close()
            except OSError:
                pass
            log.info("XMPP disconnect %s (mac=%s)", self.addr[0], self.mac)

    def _feed(self, data: str):
        """Incremental XMPP parser: extracts complete top-level stanzas.
        Handles the (never-closed) <stream:stream> opener and restarts, the
        <?xml?> declaration, self-closing tags and full <tag>..</tag> stanzas."""
        self.buf += data
        while self._alive:
            self.buf = self.buf.lstrip()
            if not self.buf:
                break
            if self.buf.startswith("<?xml"):
                e = self.buf.find("?>")
                if e == -1:
                    break
                self.buf = self.buf[e + 2:]
                continue
            if self.buf.startswith("</stream:stream"):
                self._alive = False
                break
            if self.buf.startswith("<stream:stream"):
                e = self.buf.find(">")
                if e == -1:
                    break
                self.buf = self.buf[e + 1:]
                log.info("<- rabbit %s: stream open/restart (authed=%s)", self.addr[0], self.authed)
                self._on_stream_open()
                continue
            m = re.match(r"<([a-zA-Z0-9:_-]+)", self.buf)
            if not m:
                nxt = self.buf.find("<", 1)
                self.buf = "" if nxt == -1 else self.buf[nxt:]
                continue
            tag = m.group(1)
            ot = self.buf.find(">")
            if ot == -1:
                break
            if self.buf[:ot + 1].rstrip().endswith("/>"):
                stanza = self.buf[:ot + 1]
                self.buf = self.buf[ot + 1:]
                self._handle_stanza(stanza)
                continue
            close = "</%s>" % tag
            ci = self.buf.find(close, ot + 1)
            if ci == -1:
                break  # incomplete; wait for more
            stanza = self.buf[:ci + len(close)]
            self.buf = self.buf[ci + len(close):]
            self._handle_stanza(stanza)

    def _on_stream_open(self):
        self._stream_header(self._features_bind() if self.authed else self._features_sasl())


def xmpp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", XMPP_PORT))
    srv.listen(8)
    log.info("XMPP listening on 0.0.0.0:%d (domain advertised: %s)", XMPP_PORT, SERVER_ADDRESS)
    while True:
        conn, addr = srv.accept()
        XmppSession(conn, addr).start()


# --------------------------------------------------------------------------- #
# HTTP boot server (the rabbit) — /vl/bc.jsp, /vl/locate.jsp
# --------------------------------------------------------------------------- #
class BootHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet/0.1"

    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        if path.endswith("/bc.jsp"):
            try:
                with open(BOOTCODE_FILE, "rb") as fh:
                    data = fh.read()
            except OSError:
                self.send_error(500, "bootcode missing")
                return
            log.info("serving bootcode (%d bytes) to %s [%s]", len(data), self.address_string(), p.query)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.endswith("/locate.jsp"):
            # Exact format expected by the bytecode (from OpenJabNab's locate
            # plugin): LF line endings, and xmpp_domain MUST include the port —
            # without ":<port>" the rabbit can't open the XMPP session.
            body = (f"ping {SERVER_ADDRESS}\n"
                    f"broad {SERVER_ADDRESS}\n"
                    f"xmpp_domain {SERVER_ADDRESS}:{XMPP_PORT}\n").encode()
            log.info("locate -> %s for %s [%s]", SERVER_ADDRESS, self.address_string(), p.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Catch-all for other /vl/* (ping/broad polls) — 200 + log for RE.
        log.info("unhandled boot GET %s from %s", self.path, self.address_string())
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def http_boot_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), BootHandler)
    log.info("HTTP boot server on 0.0.0.0:%d (bootcode=%s)", HTTP_PORT, BOOTCODE_FILE)
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
# Control API for Home Assistant
# --------------------------------------------------------------------------- #
class ApiHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet-api/0.1"

    def log_message(self, fmt, *args):
        log.debug("API %s - %s", self.address_string(), fmt % args)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bunny(self, q):
        mac = (q.get("mac", [None])[0] or "").lower()
        with BUNNIES_LOCK:
            if mac and mac in BUNNIES:
                return BUNNIES[mac]
            if not mac and len(BUNNIES) == 1:
                return next(iter(BUNNIES.values()))
        return None

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        path = p.path.rstrip("/")

        if path in ("", "/api", "/api/status"):
            with BUNNIES_LOCK:
                conn = {m: {"addr": s.addr[0], "bound": s.bound} for m, s in BUNNIES.items()}
            return self._json(200, {"server_address": SERVER_ADDRESS, "bunnies": conn})

        b = self._bunny(q)
        if b is None:
            return self._json(404, {"error": "no connected bunny (give ?mac=)"})

        try:
            if path == "/api/raw":
                b.send_violet_packet(base64.b64decode(q.get("b64", [""])[0]))
            elif path == "/api/ears":
                # ?left=&right= (or ?angle= for both). Position value (0..16-ish).
                both = q.get("angle", ["0"])[0]
                left = int(q.get("left", [both])[0]); right = int(q.get("right", [both])[0])
                b.send_violet_packet(ambient_packet({SVC_EAR_LEFT: left, SVC_EAR_RIGHT: right}))
            elif path == "/api/weather":
                # ?v=sun|cloudy|smog|rain|snow|storm (the belly icons)
                v = q.get("v", ["sun"])[0].lower()
                wv = WEATHER.get(v, int(v) if v.isdigit() else 0)
                b.send_violet_packet(ambient_packet({SVC_WEATHER: wv}))
            elif path == "/api/nose":
                # ?v=0 none / 1 blink / 2 double-blink
                b.send_violet_packet(ambient_packet({SVC_NOSE: int(q.get("v", ["1"])[0])}))
            elif path == "/api/ambient":
                # generic AmbientPacket: ?svc=<id>&val=<v> (repeatable)
                svcs = {int(s): int(v) for s, v in zip(q.get("svc", []), q.get("val", []))}
                b.send_violet_packet(ambient_packet(svcs or {SVC_NOSE: 1}))
            elif path == "/api/sleep":
                on = 1 if q.get("on", ["1"])[0] in ("1", "true", "yes") else 0
                b.send_violet_packet(frame_packet(PKT_SLEEP, bytes([on])))
            else:
                return self._json(404, {"error": "unknown endpoint"})
        except Exception as exc:  # noqa
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"ok": True, "mac": b.mac})


def api_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", API_PORT), ApiHandler)
    log.info("Control API on 0.0.0.0:%d", API_PORT)
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
def main():
    log.info("nabaztag-violet starting | server_address=%s http=%d xmpp=%d api=%d",
             SERVER_ADDRESS, HTTP_PORT, XMPP_PORT, API_PORT)
    if not os.path.exists(BOOTCODE_FILE):
        log.warning("bootcode file not found at %s — /vl/bc.jsp will 500", BOOTCODE_FILE)
    threading.Thread(target=xmpp_server, daemon=True).start()
    threading.Thread(target=api_server, daemon=True).start()
    http_boot_server()  # blocks


if __name__ == "__main__":
    main()
