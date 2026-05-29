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
# MessagePacket (type 0x0A) — "programs": the rich command channel.
# --------------------------------------------------------------------------- #
# A program is plain text, one "KEY value" command per line (LF-separated; the
# bytecode strips \r and splits on the first space — main.mtl filterconfig). The
# payload is obfuscated with the rolling cipher from OpenJabNab messagepacket.cpp
# (verified against the bytecode's matching de-obfuscation in main.mtl/info.mtl).
# Commands the bytecode executes (main.mtl runEvalOneCommand):
#   ST <url>  live-stream + play audio from a URL (the local-TTS / sound path)
#   MU <url>  play a short downloaded sound resource (blocking, <=300 KB)
#   MW        wait for the current sound to finish
#   CH <url>  download + play a choreography resource (ears + the 5 RGB LEDs)
#   WT <ms>   wait, staying interactive
# Audio (ST/MU) and choreography (CH) resources are fetched by the rabbit over
# HTTP from THIS server (see the /res/ route on the boot server).
_INVERSION_TABLE = bytes([
    1, 171, 205, 183, 57, 163, 197, 239, 241, 27, 61, 167, 41, 19, 53, 223,
    225, 139, 173, 151, 25, 131, 165, 207, 209, 251, 29, 135, 9, 243, 21, 191,
    193, 107, 141, 119, 249, 99, 133, 175, 177, 219, 253, 103, 233, 211, 245, 159,
    161, 75, 109, 87, 217, 67, 101, 143, 145, 187, 221, 71, 201, 179, 213, 127,
    129, 43, 77, 55, 185, 35, 69, 111, 113, 155, 189, 39, 169, 147, 181, 95,
    97, 11, 45, 23, 153, 3, 37, 79, 81, 123, 157, 7, 137, 115, 149, 63,
    65, 235, 13, 247, 121, 227, 5, 47, 49, 91, 125, 231, 105, 83, 117, 31,
    33, 203, 237, 215, 89, 195, 229, 15, 17, 59, 93, 199, 73, 51, 85, 255,
])


def message_internal(text: bytes) -> bytes:
    """Obfuscated MessagePacket payload (OpenJabNab messagepacket.cpp
    GetInternalData): leading 0x00, then each byte rolled through the inversion
    table keyed on the previous *plaintext* byte (seed 35)."""
    out = bytearray([0x00])
    prev = 35
    for c in text:
        out.append((_INVERSION_TABLE[prev % 128] * c + 47) & 0xFF)
        prev = c
    return bytes(out)


def message_packet(program: str) -> bytes:
    """Frame a program (newline-separated 'KEY value' lines) as a type-0x0A
    violet packet."""
    if not program.endswith("\n"):
        program += "\n"
    return frame_packet(PKT_MESSAGE, message_internal(program.encode("latin-1", "replace")))


# --------------------------------------------------------------------------- #
# Choreography binary (OpenJabNab choregraphy.cpp) — ears + the 5 RGB LEDs.
# --------------------------------------------------------------------------- #
# Layout: len(4, big-endian, of the body) | body | 4-byte zero trailer, where
#   body = 0x00 0x01 <tempo/10>  then per action  <deltaTicks(1)> <action bytes>
#   motor action: 0x08 <ear 0=L/1=R> <angle/18> <dir 0=fwd/1=back>
#   led   action: 0x07 <led 0=bottom,1=left,2=middle,3=right,4=top> <r> <g> <b> 0x00 0x00
LED_NAMES = {"bottom": 0, "left": 1, "middle": 2, "right": 3, "top": 4}
EAR_NAMES = {"left": 0, "right": 1}


def build_choreography(tempo_ms: int, actions: list) -> bytes:
    """actions: list of (time_ticks, "motor"|"led", params). motor params =
    (ear, angle_deg, dir); led params = (led, r, g, b)."""
    if tempo_ms > 2550:
        t = 0xFF
    elif tempo_ms < 10:
        t = 0x01
    else:
        t = tempo_ms // 10
    body = bytearray([0x00, 0x01, t & 0xFF])
    last = 0
    for time, kind, params in sorted(actions, key=lambda a: a[0]):
        delta = min(max(time - last, 0), 255)
        body.append(delta & 0xFF)
        if kind == "motor":
            ear, angle, d = params
            body += bytes([0x08, ear & 0xFF, (angle // 18) & 0xFF, d & 0xFF])
        else:  # led
            led, r, g, b = params
            body += bytes([0x07, led & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0x00])
        last = time
    n = len(body)
    return bytes([(n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + bytes(body) + bytes(4)


def parse_choreography_spec(spec: str):
    """Parse the comma form 'tempo,time,order,p3,p4,p5,p6,...' (OpenJabNab
    Choregraphy::Parse): order=motor → p3=ear,p4=angle,p6=dir; order=led →
    p3=led,p4=r,p5=g,p6=b. Returns (tempo_ms, actions)."""
    parts = [s.strip() for s in spec.split(",")]
    tempo = int(parts[0])
    actions = []
    i = 1
    while i + 5 < len(parts) + 1 and i + 5 <= len(parts):
        time = int(parts[i]); order = parts[i + 1].lower()
        p3 = int(parts[i + 2]); p4 = int(parts[i + 3]); p5 = int(parts[i + 4]); p6 = int(parts[i + 5])
        if order == "motor":
            actions.append((time, "motor", (p3, p4, p6)))
        elif order == "led":
            actions.append((time, "led", (p3, p4, p5, p6)))
        else:
            raise ValueError(f"bad choreography order {order!r}")
        i += 6
    return tempo, actions


# --------------------------------------------------------------------------- #
# Resource store — the rabbit downloads audio / choreography resources over HTTP
# from this server (referenced by full URL in ST/MU/CH program commands).
# --------------------------------------------------------------------------- #
RESOURCES: dict[str, tuple[str, bytes]] = {}
RES_LOCK = threading.Lock()


def store_resource(content: bytes, content_type: str = "application/octet-stream") -> str:
    token = os.urandom(8).hex()
    with RES_LOCK:
        RESOURCES[token] = (content_type, content)
    return token


def resource_url(token: str) -> str:
    return f"http://{SERVER_ADDRESS}/res/{token}"


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

    def send_program(self, program: str):
        """Push a code-0x0A program (newline-separated 'KEY value' commands)."""
        self.send_violet_packet(message_packet(program))

    def send_audio(self, url: str):
        """Stream + play audio from a URL (ST). The rabbit fetches it over HTTP,
        so it can be a /res/ URL on this server (HA-generated TTS, a sound…)."""
        self.send_program(f"ST {url}")

    def send_choreography(self, tempo_ms: int, actions: list):
        """Store a choreography resource and tell the rabbit to play it (CH)."""
        token = store_resource(build_choreography(tempo_ms, actions))
        self.send_program(f"CH {resource_url(token)}")

    def send_state_query(self):
        """Ask the rabbit to report its internal state via the bytecode's
        `getrunningstate` ad-hoc command. The reply (sState/gSleepState/run…)
        arrives as a normal incoming stanza and is logged."""
        self.msg_nb += 1
        self.send(
            f"<iq type='set' from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='state-{self.msg_nb}'>"
            f"<command xmlns='http://jabber.org/protocol/commands' node='getrunningstate' "
            f"action='execute'/></iq>"
        )

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
            if self.resource == "idle":
                threading.Timer(2.5, self.send_state_query).start()
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

        if f.startswith("<presence"):
            # CRITICAL: after binding its operational resource the rabbit sends a
            # presence and then sits in the ssPresence state WAITING to receive a
            # presence back from the server; only then does it move to ssFree —
            # the one state in which it acts on pushed iq/message commands. Without
            # this reply it stays stuck and silently ignores everything we push.
            self.send(f"<presence from='{self.domain}' to='{self.jid}'/>")
            log.info("rabbit %s: presence <-> presence sent (rabbit should now go free)", self.addr[0])
            return
        # Anything else (events: button/RFID, packets) -> logged above.

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
        if path.startswith("/res/"):
            token = path[len("/res/"):]
            with RES_LOCK:
                entry = RESOURCES.get(token)
            if entry is None:
                self.send_error(404, "no such resource")
                return
            ctype, data = entry
            log.info("serving resource %s (%d bytes, %s) to %s", token, len(data), ctype, self.address_string())
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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
                conn = {m: {"addr": s.addr[0], "bound": s.bound, "resource": s.resource}
                        for m, s in BUNNIES.items()}
            return self._json(200, {"server_address": SERVER_ADDRESS, "bunnies": conn})

        body = b""
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                body = self.rfile.read(length)

        b = self._bunny(q)
        if b is None:
            return self._json(404, {"error": "no connected bunny (give ?mac=)"})

        def _one(name, default=None):
            return q.get(name, [default])[0]

        try:
            if path == "/api/raw":
                b.send_violet_packet(base64.b64decode(_one("b64", "")))
            elif path == "/api/program":
                # ?text=ST%20http://... — newline OR '|' separated commands.
                b.send_program(_one("text", "").replace("|", "\n"))
            elif path == "/api/play":
                # Audio: ?url=<mp3/wav> streamed via ST, OR POST the audio body
                # (stored + served from /res/ and streamed). ?wait=1 blocks (MW).
                url = _one("url")
                if not url and body:
                    ctype = self.headers.get("Content-Type", "audio/mpeg")
                    url = resource_url(store_resource(body, ctype))
                if not url:
                    return self._json(400, {"error": "give ?url= or POST audio body"})
                prog = f"ST {url}"
                if _one("wait") in ("1", "true", "yes"):
                    prog += "\nMW"
                b.send_program(prog)
            elif path == "/api/ears":
                # Real ear positioning via a choreography motor action.
                # ?left=&right= positions (~0..16); ?dir=0|1 rotation direction.
                both = _one("angle", "0")
                left = int(_one("left", both)); right = int(_one("right", both))
                d = int(_one("dir", "0"))
                b.send_choreography(200, [
                    (0, "motor", (EAR_NAMES["left"], left * 18, d)),
                    (0, "motor", (EAR_NAMES["right"], right * 18, d)),
                ])
            elif path == "/api/earwiggle":
                # The AmbientPacket "ears" effect: a wiggle back to home + a beep
                # (the original "your friend moved their ears" feature).
                b.send_violet_packet(ambient_packet({SVC_EAR_LEFT: int(_one("left", "1")),
                                                      SVC_EAR_RIGHT: int(_one("right", "1"))}))
            elif path == "/api/led":
                # Full RGB on one of the 5 LEDs via a choreography.
                # ?led=bottom|left|middle|right|top &r=&g=&b=
                led = _one("led", "bottom").lower()
                lid = LED_NAMES.get(led, int(led) if led.isdigit() else 0)
                r = int(_one("r", "0")); g = int(_one("g", "0")); bl = int(_one("b", "0"))
                b.send_choreography(200, [(0, "led", (lid, r, g, bl))])
            elif path == "/api/choreography":
                # ?spec=tempo,time,order,p3,p4,p5,p6,... (OpenJabNab comma form).
                tempo, actions = parse_choreography_spec(_one("spec", ""))
                b.send_choreography(tempo, actions)
            elif path == "/api/weather":
                # ?v=sun|cloudy|smog|rain|snow|storm (the belly icons)
                v = _one("v", "sun").lower()
                wv = WEATHER.get(v, int(v) if v.isdigit() else 0)
                b.send_violet_packet(ambient_packet({SVC_WEATHER: wv}))
            elif path == "/api/nose":
                # ?v=0 none / 1 blink / 2 double-blink
                b.send_violet_packet(ambient_packet({SVC_NOSE: int(_one("v", "1"))}))
            elif path == "/api/bottomled":
                # Bottom belly LED via AmbientPacket (palette index, no fetch).
                b.send_violet_packet(ambient_packet({SVC_BOTTOMLED: int(_one("v", "0"))}))
            elif path == "/api/ambient":
                # generic AmbientPacket: ?svc=<id>&val=<v> (repeatable)
                svcs = {int(s): int(v) for s, v in zip(q.get("svc", []), q.get("val", []))}
                b.send_violet_packet(ambient_packet(svcs or {SVC_NOSE: 1}))
            elif path == "/api/sleep":
                on = 1 if _one("on", "1") in ("1", "true", "yes") else 0
                b.send_violet_packet(frame_packet(PKT_SLEEP, bytes([on])))
            elif path == "/api/state":
                # Ask the rabbit to report its XMPP/run state (reply is logged).
                b.send_state_query()
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
