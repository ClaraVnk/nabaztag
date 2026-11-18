"""The findings of the add-on review of 2026-11-18, each as the safe
behaviour. On 0.18.0 (d6c13b9) the reviewer's eleven proofs fail; the
others were added with their fixes in 0.19.0. Stdlib only, loopback only.

    cd nabaztag-violet && python3 -m unittest discover -s tests -v
"""
import ast
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
sys.path.insert(0, ADDON)

TMP = tempfile.mkdtemp(prefix="addon-review-")
if "server" not in sys.modules:
    # Alone; under discover the first module that imports the server sets these.
    BODY = (b"\x00" * 40 + b"xmpp-tls:0" + b"xmpp-rfid:0" + b"xmpp-res:0" + b"xmpp-rsc:0"
            + b"xmpp-mic:0" + b"xmpp-ota:0" + b"xmpp-https:0" + b"\x01" * 40)
    RUNTIME = b"amber" + b"%08x" % len(BODY) + BODY + b"Mind"
    for name in ("bootcode.tls", "bootcode.hybrid", "bootcode.https"):
        with open(os.path.join(TMP, name), "wb") as fh:
            fh.write(RUNTIME)
    os.environ.update({
        "ADOPTION_KEYS_FILE": os.path.join(TMP, "keys.json"),
        "BOOTCODE_FILE": os.path.join(TMP, "bootcode.hybrid"),
        "BOOTCODE_TLS_FILE": os.path.join(TMP, "bootcode.tls"),
        "BOOTCODE_HTTPS_FILE": os.path.join(TMP, "bootcode.https"),
        "STATE_FILE": os.path.join(TMP, "state.json"),
        "API_TOKEN_FILE": os.path.join(TMP, "api_token"),
        "MODELS_DIR": os.path.join(TMP, "models"),
        "SERVER_ADDRESS": "127.0.0.1",
    })

import adoption  # noqa: E402
import server    # noqa: E402


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# udp_mic_server reads the port when it starts.
server.MIC_UDP_PORT = UDP_PORT = _free_udp_port()

RABBIT_ADDR = ("192.0.2.77", 40000)     # a rabbit elsewhere on the LAN
HERE_ADDR = ("127.0.0.1", 40000)        # a rabbit where the test's HTTP comes from
STRANGER = "81.2.69.142"


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


BOOT, BOOT_URL = _serve(server.BootHandler)
API, API_URL = _serve(server.ApiHandler)
threading.Thread(target=server.udp_mic_server, daemon=True).start()
time.sleep(0.2)
TOKEN = {"X-API-Token": server.API_TOKEN}


class _Drain(threading.Thread):
    """The rabbit's end of a socketpair: keeps what the server wrote."""

    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock, self.data = sock, b""
        self.start()

    def run(self):
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            self.data += chunk


def fake_rabbit(mac, addr=RABBIT_ADDR):
    """A bound session for `mac`, registered as the server does."""
    ours, theirs = socket.socketpair()
    s = server.XmppSession(ours, addr)
    s.mac = mac
    s.bound = True
    s.resource = "idle"
    s._register()
    return s, _Drain(theirs)


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def confirm_key(mac):
    priv, pub = adoption.keypair()
    key, colors = adoption.derive(priv, pub, pub, pub)
    server.KEYS.offer(mac, key, colors)
    server.KEYS.decide(mac, True)
    return key


WAV = (b"RIFF" + (36 + 8).to_bytes(4, "little") + b"WAVEfmt "
       + (16).to_bytes(4, "little") + bytes(16) + b"data" + (8).to_bytes(4, "little") + bytes(8))


class Base(unittest.TestCase):
    def setUp(self):
        with server.BUNNIES_LOCK:
            server.BUNNIES.clear()
        server.WAKE.update(on=False, streaming=False, rearm=False, hold=0, capturing=False,
                           mac=None)
        with server.MIC_LOCK:
            server.MIC_STREAM.update(adpcm=bytearray(), packets=0, expect=None, sender=None)
        server._KX_SEEN.clear()
        server.TLS_ANCHORS.clear()
        self.events = []
        self._fire = server.fire_ha_event
        server.fire_ha_event = lambda t, d: self.events.append((t, d))

    def tearDown(self):
        server.fire_ha_event = self._fire


class XmppLimits(Base):
    def test_unclosed_stanza_is_bounded(self):
        """R3: `<a>` then a megabyte, before any auth (Le Terrier closes past
        MAX_BUFFER = 32 KiB)."""
        ours, theirs = socket.socketpair()
        _Drain(theirs)
        s = server.XmppSession(ours, (STRANGER, 1))
        for _ in range(64):
            s._feed("<a>" + "x" * 16384)
        self.assertTrue(not s._alive or len(s.buf) <= 32 * 1024,
                        f"session alive with a {len(s.buf)}-char buffer")

    def test_unauthenticated_session_is_closed(self):
        """R5: a socket that never authenticates is not kept forever, even
        when it keeps sending."""
        ours, theirs = socket.socketpair()
        s = server.XmppSession(ours, (STRANGER, 1))
        s.tcp_at -= server.PREAUTH_TIMEOUT_S + 1
        s.start()
        theirs.sendall(b"<x/>")
        s.join(5)
        self.assertFalse(s.is_alive(), "an unauthenticated session outlived PREAUTH_TIMEOUT_S")
        theirs.close()

    def test_connection_caps(self):
        """R5: concurrent XMPP connections are capped per address and in all."""
        ip = "192.0.2.200"
        got = [server._admit(ip) for _ in range(server.MAX_CONN_PER_IP + 1)]
        self.assertEqual(got.count(True), server.MAX_CONN_PER_IP)
        self.assertFalse(got[-1])
        for _ in range(server.MAX_CONN_PER_IP):
            server._release(ip)
        self.assertTrue(server._admit(ip))
        server._release(ip)
        self.assertNotIn(ip, server._CONNS)

    def test_http_handlers_time_out(self):
        """R5: a slow client does not hold a handler thread forever."""
        self.assertTrue(server.BootHandler.timeout and server.ApiHandler.timeout)

    def test_iq_id_is_not_echoed_as_markup(self):
        """Q5: the iq id comes back between quotes; a quote in it is not."""
        s, drain = fake_rabbit("0019dbdeadc1")
        s._handle_stanza("<iq type='get' id=\"x'/><evil/>\"><ping xmlns='urn:xmpp:ping'/></iq>")
        time.sleep(0.1)
        self.assertNotIn(b"<evil/>", drain.data)
        self.assertIn(b"id='1'", drain.data)


class Presence(Base):
    def test_silent_bind_gets_a_presence(self):
        """R6: a bind with no presence after it gets one (Le Terrier
        check_presence_nudge, Choubaztag 2026-10-30)."""
        s, drain = fake_rabbit("0019dbdeadc2")
        now = time.time()
        s._nudge_from = now - server.NUDGE_AFTER_S - 1
        s.check_presence_nudge(now)
        time.sleep(0.1)
        self.assertIn(b"<presence ", drain.data)
        self.assertEqual(s._nudges, 1)

    def test_a_rabbit_that_sent_its_presence_is_left_alone(self):
        s, drain = fake_rabbit("0019dbdeadc3")
        now = time.time()
        s._nudge_from = now - 60
        s._handle_stanza("<presence/>")          # its own, answered
        time.sleep(0.1)
        before = drain.data.count(b"<presence ")
        s.check_presence_nudge(now + 60)
        time.sleep(0.1)
        self.assertEqual(drain.data.count(b"<presence "), before)


class Bosh(Base):
    def test_http_bind_gets_terminate(self):
        """R1: after three failed XMPP tries the rabbit posts to /http-bind
        and leaves BOSH only on a failure (Le Terrier 2026-11-10)."""
        code, body = http(BOOT_URL + "/http-bind", data=b"<body rid='1'/>",
                          headers={"Content-Type": "text/xml"})
        self.assertIn(b"type='terminate'", body, f"answered {code} {body!r}")
        self.assertIn(b"</stream:stream>", body)


class StaleSession(Base):
    def test_bytecode_fetch_closes_held_session(self):
        """R2: a rabbit that fetches bc.jsp has reloaded its interpreter; the
        session still held for it is a zombie (Le Terrier _drop_stale_session).
        The reviewer's proof fetched from another address than the session's;
        Le Terrier closes only from the same one (next test)."""
        mac = "0019dbdeadb1"
        s, _ = fake_rabbit(mac, HERE_ADDR)
        code, _ = http(BOOT_URL + f"/vl/bc.jsp?m={mac}&v=0.0.0.13")
        self.assertEqual(code, 200)
        time.sleep(0.2)
        self.assertFalse(s._alive, "the old session is still held and still gets commands")

    def test_bytecode_fetch_from_elsewhere_keeps_session(self):
        """R2: one GET naming a MAC printed under the rabbit does not cut its
        session (Le Terrier, Nabazprout 2026-11-11: ears up until unplugged)."""
        mac = "0019dbdeadb8"
        s, _ = fake_rabbit(mac, RABBIT_ADDR)
        http(BOOT_URL + f"/vl/bc.jsp?m={mac}&v=0.0.0.13")
        time.sleep(0.2)
        self.assertTrue(s._alive)


class Spoofing(Base):
    def test_rfid_jsp_ignored_for_a_rabbit_that_reports_in_session(self):
        """S4: a confirmed rabbit served `rfid` reports tags inside TLS; a
        plain rfid.jsp in its name is a forger (Le Terrier _serve_rfid)."""
        mac = "0019dbdeadb2"
        confirm_key(mac)
        server.KEYS.note_served(mac, {"rfid", "res"})
        http(BOOT_URL + f"/vl/rfid.jsp?sn={mac}&t=d0021a0352b7c1e1")
        self.assertEqual(self.events, [], "forged tag fired a Home Assistant event")

    def test_rfid_jsp_of_an_unadopted_rabbit_still_fires(self):
        """Control: a stock rabbit reports tags this way and nothing else."""
        http(BOOT_URL + "/vl/rfid.jsp?sn=0019dbdeadb9&t=d0021a0352b7c1e1")
        time.sleep(0.1)
        self.assertEqual([(t, d["tag"], d["verified"]) for t, d in self.events],
                         [("nabaztag_event", "d0021a0352b7c1e1", False)])

    def test_rfid_jsp_tag_must_be_hex(self):
        http(BOOT_URL + "/vl/rfid.jsp?sn=0019dbdeadb9&t=%3Cx%3E")
        self.assertEqual(self.events, [])

    def test_plain_bc_jsp_cannot_rewrite_session_policies(self):
        """S5: anyone may GET bc.jsp?m=<confirmed MAC> in the clear and
        rewrite what the server believes the rabbit's runtime reads."""
        mac = "0019dbdeadb3"
        confirm_key(mac)
        server.KEYS.note_served(mac, {"rfid", "res", "rsc", "mic", "ota", "https"})
        before = server.KEYS.get(mac)["served"]
        http(BOOT_URL + f"/vl/bc.jsp?m={mac}&v=1.0.0.0")
        self.assertEqual(server.KEYS.get(mac)["served"], before)

    def test_bc_jsp_from_the_anchor_still_records(self):
        """Control for S5: the rabbit itself, from where its TLS session
        connects, still records what its new runtime reads."""
        mac = "0019dbdeadba"
        confirm_key(mac)
        server.KEYS.note_served(mac, {"rfid"})
        server.TLS_ANCHORS[mac] = "127.0.0.1"
        http(BOOT_URL + f"/vl/bc.jsp?m={mac}&v=1.0.0.0")
        self.assertNotEqual(server.KEYS.get(mac)["served"], frozenset({"rfid"}))

    def test_record_from_a_stranger_does_not_reach_the_agent(self):
        """S2: record.jsp took any POST and fed it to STT and the
        conversation agent; the rabbit connects from elsewhere."""
        calls = []
        saved = server.handle_voice, server.VOICE_PIPELINE
        server.handle_voice = lambda pcm, *a: calls.append(len(pcm))
        server.VOICE_PIPELINE = True
        try:
            fake_rabbit("0019dbdeadb4")
            code, _ = http(BOOT_URL + "/vl/record.jsp?m=0", data=WAV,
                           headers={"Content-Type": "application/octet-stream"})
            time.sleep(0.3)
        finally:
            server.handle_voice, server.VOICE_PIPELINE = saved
        self.assertEqual(calls, [], "a POST from a non-rabbit address reached the voice pipeline")
        self.assertEqual(code, 403)

    def test_record_from_the_rabbit_reaches_the_agent_on_that_rabbit(self):
        """Control for S2, and R7: the reply goes to the rabbit that recorded."""
        calls = []
        saved = server.handle_voice, server.VOICE_PIPELINE
        server.handle_voice = lambda pcm, bunny=None: calls.append(bunny.mac)
        server.VOICE_PIPELINE = True
        try:
            fake_rabbit("0019dbdeadbb", RABBIT_ADDR)
            fake_rabbit("0019dbdeadbc", HERE_ADDR)
            code, _ = http(BOOT_URL + "/vl/record.jsp?m=0", data=WAV,
                           headers={"Content-Type": "application/octet-stream"})
            time.sleep(0.3)
        finally:
            server.handle_voice, server.VOICE_PIPELINE = saved
        self.assertEqual((code, calls), (200, ["0019dbdeadbc"]))

    def test_record_body_is_capped(self):
        fake_rabbit("0019dbdeadbd", HERE_ADDR)
        s = socket.create_connection(("127.0.0.1", BOOT.server_address[1]))
        s.sendall(b"POST /vl/record.jsp HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n"
                  % (server.RECORD_MAX_BYTES + 1))
        self.assertIn(b" 413 ", s.recv(4096))
        s.close()

    def test_udp_mic_ignores_another_sender(self):
        """S3: the UDP mic receiver took `snd` datagrams from any address
        while listening was on; Le Terrier keeps the first sender only."""
        b, _ = fake_rabbit("0019dbdeadb5")
        server.WAKE["on"] = True
        b._take_mic()
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(5):
            tx.sendto(b"snd" + b"\x88" * 256, ("127.0.0.1", UDP_PORT))
        tx.close()
        time.sleep(0.3)
        with server.MIC_LOCK:
            got = server.MIC_STREAM["packets"]
        self.assertEqual(got, 0, f"{got} datagrams from a non-rabbit address buffered as mic audio")

    def test_udp_mic_keeps_the_first_sender_of_the_rabbit(self):
        """Control for S3: the rabbit's own flow is kept, a second flow from
        its address after it is not."""
        b, _ = fake_rabbit("0019dbdeadbe", HERE_ADDR)
        server.WAKE["on"] = True
        b._take_mic()
        one = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        two = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(3):
            one.sendto(b"snd" + b"\x88" * 256, ("127.0.0.1", UDP_PORT))
        time.sleep(0.2)
        for _ in range(3):
            two.sendto(b"snd" + b"\x88" * 256, ("127.0.0.1", UDP_PORT))
        time.sleep(0.3)
        one.close()
        two.close()
        with server.MIC_LOCK:
            self.assertEqual(server.MIC_STREAM["packets"], 3)

    def test_mic_buffer_is_bounded(self):
        """R4: while the wake loop is busy (STT up to 120 s, agent 60 s,
        speech) nothing drains the mic buffer."""
        server.WAKE["on"] = True
        with server.MIC_LOCK:
            server.MIC_STREAM["expect"] = STRANGER
        for _ in range(64):
            server.mic_feed(b"\x88" * 65536, STRANGER)
        with server.MIC_LOCK:
            n = len(server.MIC_STREAM["adpcm"])
        self.assertLessEqual(n, 1 << 20, f"mic buffer holds {n} bytes")

    def test_events_say_whether_the_rabbit_proved_itself(self):
        """S7: a plain session cannot be told from a forger; events say so."""
        s, _ = fake_rabbit("0019dbdeadbf")
        s._handle_stanza("<message><button xmlns='violet:nabaztag:button'><clic>1</clic>"
                         "</button></message>")
        self.assertEqual(self.events[0][1]["verified"], False)

    def test_kx_is_rate_limited_per_address(self):
        """S6: one address walking MACs got a pending key and a notification
        each, without bound."""
        codes = []
        for i in range(server.KX_PER_IP_PER_HOUR + 1):
            _, pub = adoption.keypair()
            codes.append(http(BOOT_URL + f"/vl/kx.jsp?m=0019dbdee{i:03x}&p={pub.hex()}")[0])
        for i in range(server.KX_PER_IP_PER_HOUR + 1):
            server.KEYS.forget(f"0019dbdee{i:03x}")
        self.assertEqual(codes, [200] * server.KX_PER_IP_PER_HOUR + [429])


class OpenApi(Base):
    def test_lan_host_cannot_adopt_a_rabbit_with_default_options(self):
        """S1: the default api_token was empty. A LAN host ran kx.jsp for a
        MAC, read the fingerprint from /api/adoption and confirmed its own
        key: it then held that rabbit's TLS PSK."""
        self.assertTrue(server.API_TOKEN)
        mac = "0019dbdeadb6"
        priv, pub = adoption.keypair()
        code, warren = http(BOOT_URL + f"/vl/kx.jsp?m={mac}&p={pub.hex()}")
        self.assertEqual(code, 200)
        attacker_key, _ = adoption.derive(priv, bytes.fromhex(warren.decode()), pub,
                                          bytes.fromhex(warren.decode()))
        code, listing = http(API_URL + "/api/adoption")
        self.assertEqual(code, 401)
        fp = adoption.fingerprint(attacker_key)
        http(API_URL + "/api/adoption/confirm",
             data=json.dumps({"mac": mac, "fingerprint": fp}).encode(),
             headers={"Content-Type": "application/json"})
        row = server.KEYS.get(mac)
        server.KEYS.forget(mac)
        self.assertFalse(row["state"] == "confirmed" and row["key"] == attacker_key,
                         "a LAN host confirmed its own key for the rabbit; it holds the PSK")

    def test_lan_host_cannot_switch_the_mic_on(self):
        """S1: GET /api/mic?on=1 with no credential started the rabbit's
        microphone (a plain <img src> from any web page did the same)."""
        _, drain = fake_rabbit("0019dbdeadb7")
        code, _ = http(API_URL + "/api/mic?on=1&mac=0019dbdeadb7")
        time.sleep(0.2)
        self.assertNotEqual(code, 200, "mic switched on without a credential "
                            f"({len(drain.data)} bytes pushed to the rabbit, WAKE on={server.WAKE['on']})")

    def test_cross_site_get_is_refused_even_with_the_token(self):
        """The GET-CSRF on the mic: a browser that says cross-site is refused."""
        fake_rabbit("0019dbdeadc4")
        code, _ = http(API_URL + "/api/mic?on=1&mac=0019dbdeadc4",
                       headers={**TOKEN, "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 403)
        self.assertFalse(server.WAKE["on"])

    def test_the_token_opens_the_api(self):
        code, body = http(API_URL + "/api/status", headers=TOKEN)
        self.assertEqual(code, 200)
        self.assertIn("bunnies", json.loads(body))

    def test_ingress_needs_no_token(self):
        saved = server.INGRESS_PEER
        server.INGRESS_PEER = "127.0.0.1"
        try:
            code, _ = http(API_URL + "/api/status")
            page = http(API_URL + "/")
        finally:
            server.INGRESS_PEER = saved
        self.assertEqual(code, 200)
        self.assertIn(b"<!doctype html>", page[1])

    def test_a_token_is_generated_once_and_kept(self):
        saved = server.API_TOKEN_OPTION, server.API_TOKEN_FILE
        server.API_TOKEN_OPTION = ""
        server.API_TOKEN_FILE = os.path.join(TMP, "generated_token")
        try:
            first = server._resolve_api_token()
            second = server._resolve_api_token()
            mode = stat.S_IMODE(os.stat(server.API_TOKEN_FILE).st_mode)
        finally:
            server.API_TOKEN_OPTION, server.API_TOKEN_FILE = saved
        self.assertEqual((first[1], second[1]), ("new", "file"))
        self.assertEqual(first[0], second[0])
        self.assertRegex(first[0], r"^[0-9a-f]{32}$")
        self.assertEqual(mode, 0o600)

    def test_the_token_is_not_logged(self):
        """S9: the request line, ?token= included, was logged at Debug."""
        h = server.ApiHandler.__new__(server.ApiHandler)
        h.client_address = ("127.0.0.1", 1)
        with self.assertLogs("nabaztag", "DEBUG") as cm:
            h.log_message('"%s" %s %s', "GET /api/say?token=s3cret&text=x HTTP/1.1", "200", "-")
        self.assertNotIn("s3cret", "\n".join(cm.output))

    def test_ota_path_is_confined(self):
        """S8: ?path= copied any readable file with the markers to :80/ota/."""
        outside = os.path.join(TMP, "outside.sim")
        with open(outside, "wb") as fh:
            fh.write(b"x-violet-x-sig-x")
        code, _ = http(API_URL + f"/api/ota?path={outside}", headers=TOKEN)
        self.assertEqual(code, 400)
        code, _ = http(API_URL + "/api/ota?name=..", data=b"x-violet-x-sig-x",
                       headers={**TOKEN, "Content-Type": "multipart/form-data; boundary=b"})
        self.assertEqual(code, 400)

    def test_volume_is_per_rabbit(self):
        """R7: one rabbit's volume was applied to all of them."""
        fake_rabbit("0019dbdeadc5")
        fake_rabbit("0019dbdeadc6")
        code, _ = http(API_URL + "/api/volume?mac=00:19:db:de:ad:c5&level=0", headers=TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(server.volume_for("0019dbdeadc5"), 120)
        self.assertEqual(server.volume_for("0019dbdeadc6"), server.VOLUME["hw"])


class Mic(Base):
    def test_a_second_rabbit_does_not_take_the_mic_on_connect(self):
        """R7: the mic is one rabbit's; a second one coming online leaves it."""
        a, _ = fake_rabbit("0019dbdeadc7")
        b, _ = fake_rabbit("0019dbdeadc8", HERE_ADDR)
        a._take_mic()
        self.assertFalse(b._may_take_mic())
        self.assertTrue(a._may_take_mic())

    def test_moving_the_mic_stops_the_previous_rabbit(self):
        a, da = fake_rabbit("0019dbdeadc9")
        b, _ = fake_rabbit("0019dbdeadca", HERE_ADDR)
        server.WAKE["on"] = True
        a._start_listen()
        time.sleep(0.1)
        sent = len(da.data)
        b._start_listen()
        time.sleep(0.1)
        self.assertEqual(server.WAKE["mac"], "0019dbdeadca")
        self.assertGreater(len(da.data), sent, "the first rabbit was not told RT")


class State(Base):
    def test_state_is_written_atomically_and_only_on_change(self):
        """R8, R9: written in place on every mic re-arm."""
        path = server._STATE_FILE
        server._STATE_SAVED.clear()
        server._save_state()
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(path + ".tmp"))
        os.unlink(path)
        server._save_state()                   # nothing changed
        self.assertFalse(os.path.exists(path))
        server.VOLUME["by_mac"]["0019dbdeadcb"] = 30
        try:
            server._save_state()
        finally:
            server.VOLUME["by_mac"].pop("0019dbdeadcb")
        with open(path) as fh:
            self.assertEqual(json.load(fh)["volume_by_mac"]["0019dbdeadcb"], 30)

    def test_sigterm_has_a_handler(self):
        """R8: PID 1 without a handler ignores SIGTERM."""
        self.assertTrue(callable(server._on_sigterm))


class Model(Base):
    """O2/S10: the Whisper model, no longer in the image, fetched pinned."""

    def setUp(self):
        super().setUp()
        self.blob = os.urandom(4096)
        blob = self.blob

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def log_message(self, *a):
                pass

        self.httpd, self.url = _serve(H)
        self.saved = (dict(server.WHISPER_MODELS), server.MODELS_DIR, server.STT_MODEL,
                      dict(server._STT_MODEL))
        server.MODELS_DIR = tempfile.mkdtemp(dir=TMP)
        server.STT_MODEL = "tiny"
        server._STT_MODEL.update(path=None, failed_at=0.0)

    def tearDown(self):
        super().tearDown()
        self.httpd.shutdown()
        models, server.MODELS_DIR, server.STT_MODEL, st = self.saved
        server.WHISPER_MODELS.clear()
        server.WHISPER_MODELS.update(models)
        server._STT_MODEL.clear()
        server._STT_MODEL.update(st)

    def test_downloaded_once_and_checked(self):
        server.WHISPER_MODELS["tiny"] = (self.url + "/m", hashlib.sha256(self.blob).hexdigest())
        p = server.whisper_model_path()
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), self.blob)

    def test_a_wrong_checksum_is_not_used(self):
        server.WHISPER_MODELS["tiny"] = (self.url + "/m", "0" * 64)
        self.assertIsNone(server.whisper_model_path())
        self.assertEqual(os.listdir(server.MODELS_DIR), [])
        self.assertTrue(server._STT_MODEL["failed_at"])

    def test_no_model_in_the_image(self):
        with open(os.path.join(ADDON, "Dockerfile")) as fh:
            df = fh.read()
        self.assertNotIn("/app/models", df)
        self.assertNotIn("python3 curl", df)


class Utf8Split(Base):
    def test_multibyte_across_two_reads(self):
        """Q4: each TCP read was decoded on its own."""
        ours, theirs = socket.socketpair()
        _Drain(theirs)
        s = server.XmppSession(ours, RABBIT_ADDR)
        e = "<x>é</x>".encode()
        s.buf = ""
        s._handle_stanza = lambda st: setattr(s, "got", st)
        s._take(e[:4])
        s._take(e[4:])
        self.assertEqual(getattr(s, "got", None), "<x>é</x>")


class DnsHelper(unittest.TestCase):
    """tools/nabdns.py opens raw sockets when imported; its parser alone."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ADDON, "tools", "nabdns.py")) as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_qname")
        ns = {}
        exec(compile(ast.Module([fn], []), "nabdns.py", "exec"), ns)
        cls.parse = staticmethod(ns["parse_qname"])

    def test_a_good_name(self):
        self.assertEqual(self.parse(b"\x00" * 12 + b"\x03nab\x03lan\x00\x00\x01\x00\x01", 12),
                         (b"nab.lan", 21))

    def test_malformed_names_do_not_raise(self):
        for q in (b"\x05ab", b"\xc0\x0c", b"\x03nab"):
            self.assertIsNone(self.parse(b"\x00" * 12 + q, 12)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
