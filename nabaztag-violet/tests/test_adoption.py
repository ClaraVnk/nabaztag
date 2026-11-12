"""The adoption key and TLS on the add-on, against the firmware's vectors and
a real TLS 1.3 PSK client.

    cd nabaztag-violet && python3 -m unittest discover -s tests -v

Python 3.13 or later (the TLS tests use the PSK callbacks).
"""
import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="nabaztag-violet-test-")
BODY = b"\x00" * 40 + b"xmpp-tls:0" + b"\x01" * 40
TLS_RUNTIME = b"amber" + b"%08x" % len(BODY) + BODY + b"Mind"
HYBRID = b"amber" + b"%08x" % 4 + b"oldz" + b"Mind"
for name, data in (("bootcode.tls", TLS_RUNTIME), ("bootcode.hybrid", HYBRID)):
    with open(os.path.join(TMP, name), "wb") as fh:
        fh.write(data)
os.environ.update({
    "ADOPTION_KEYS_FILE": os.path.join(TMP, "keys.json"),
    "BOOTCODE_FILE": os.path.join(TMP, "bootcode.hybrid"),
    "BOOTCODE_TLS_FILE": os.path.join(TMP, "bootcode.tls"),
    "SERVER_ADDRESS": "127.0.0.1",
})

import adoption  # noqa: E402
import server    # noqa: E402

# The firmware's tests/vectors_kx.h, the same three Le Terrier is held to.
EXCHANGES = [
    {'rpriv': '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f', 'wpriv': 'c8cbced1d4d7dadde0e3e6e9eceff2f5f8fbfe0104070a0d101316191c1f2225', 'rpub': '8f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f', 'wpub': 'eebeea3c07a2a7f60ec9a6fa2b9c069a639a2a3976660561e6ae241099142843', 'key': '7f3fef6b1703e9d8f4acdcca634275d6b394745d22544fc459a97d3f7b61e42b', 'check': [7, 0, 2, 0, 3]},  # gitleaks:allow (test seeds)
    {'rpriv': '0708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20212223242526', 'wpriv': 'bdc0c3c6c9cccfd2d5d8dbdee1e4e7eaedf0f3f6f9fcff0205080b0e1114171a', 'rpub': '07b8c542487686a78301855fcb6d3f6a8a911cd7f1983a9b44dc9dcd22839d23', 'wpub': '7e2d43ac3b381f811c63c7ab4ec5a9367797e366c789d699da0c11312911bb6f', 'key': '9b92e4a3c7e607d1b3041deb7dd579b54cfc5631205e14023707b8f1092f6baa', 'check': [0, 5, 0, 7, 6]},  # gitleaks:allow (test seeds)
]
# RFC 7748 5.2.
RFC_K = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")  # gitleaks:allow
RFC_U = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")  # gitleaks:allow
RFC_OUT = bytes.fromhex("c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")  # gitleaks:allow
# The firmware's nab_tls_psk of the key 0..31 (tests/vectors_tls).
PSK0 = bytes.fromhex("b1e76e75b9f83eff4cd47e6381fe2e853743292e5e76d064ed98fda4fcba6fbf")  # gitleaks:allow

MAC = "0019db00beef"


class Crypto(unittest.TestCase):
    def test_x25519_rfc7748(self):
        self.assertEqual(adoption.x25519(RFC_K, RFC_U), RFC_OUT)

    def test_both_ends_agree_with_the_rabbit(self):
        for v in EXCHANGES:
            r, w = bytes.fromhex(v["rpriv"]), bytes.fromhex(v["wpriv"])
            rpub, wpub = adoption.public_key(r), adoption.public_key(w)
            self.assertEqual((rpub.hex(), wpub.hex()), (v["rpub"], v["wpub"]))
            key, check = adoption.derive(w, rpub, rpub, wpub)
            self.assertEqual((key.hex(), check), (v["key"], v["check"]))

    def test_tls_psk_is_the_firmwares(self):
        self.assertEqual(adoption.tls_psk(bytes(range(32))), PSK0)

    def test_low_order_point_is_refused(self):
        with self.assertRaises(adoption.LowOrderPoint):
            adoption.derive(bytes.fromhex(EXCHANGES[0]["rpriv"]), bytes(32), bytes(32), bytes(32))


class Keys(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "k.json")
        self.store = adoption.KeyStore(self.path)

    def test_first_come_first_kept(self):
        self.assertTrue(self.store.offer(MAC, b"a" * 32, [1, 2, 3, 4, 5]))
        self.assertFalse(self.store.offer(MAC, b"b" * 32, [0] * 5))
        self.assertEqual(self.store.get(MAC)["key"], b"a" * 32)

    def test_a_yes_applies_to_the_key_shown(self):
        self.store.offer(MAC, b"a" * 32, [1, 2, 3, 4, 5])
        self.assertFalse(self.store.decide(MAC, True, adoption.fingerprint(b"b" * 32)))
        self.assertTrue(self.store.decide(MAC, True, adoption.fingerprint(b"a" * 32)))
        self.assertEqual(self.store.get(MAC)["state"], "confirmed")
        self.assertFalse(self.store.decide(MAC, False, adoption.fingerprint(b"a" * 32)))

    def test_a_rejected_key_makes_way_and_the_file_survives(self):
        self.store.offer(MAC, b"a" * 32, [1, 2, 3, 4, 5])
        self.store.decide(MAC, False, adoption.fingerprint(b"a" * 32))
        self.assertTrue(self.store.offer(MAC, b"c" * 32, [0] * 5))
        again = adoption.KeyStore(self.path)
        self.assertEqual(again.get(MAC)["key"], b"c" * 32)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")

    def test_an_unreadable_file_is_not_replaced(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(RuntimeError):
            adoption.KeyStore(self.path)


class Seal(unittest.TestCase):
    def test_confirmed_requires_tls_under_the_hmac(self):
        key = bytes(range(32))
        out = adoption.seal(TLS_RUNTIME, {"state": "confirmed", "key": key})
        self.assertIn(b"xmpp-tls:1", out)
        self.assertNotIn(b"xmpp-tls:0", out)
        n = int(out[5:13], 16)
        tag = hmac.new(key, out[13:13 + n], hashlib.sha256).hexdigest().encode()
        self.assertTrue(out.endswith(b"Hmac" + tag))

    def test_pending_or_none_changes_nothing(self):
        self.assertEqual(adoption.seal(TLS_RUNTIME, None), TLS_RUNTIME)
        self.assertEqual(adoption.seal(TLS_RUNTIME, {"state": "pending", "key": b"k" * 32}),
                         TLS_RUNTIME)


def _client_ctx(identity, psk):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_psk_client_callback(lambda hint: (identity, psk))
    return ctx


@unittest.skipIf(server.TLS_CTX is None, "this Python cannot do an external PSK")
class EndToEnd(unittest.TestCase):
    """The add-on's own listeners on ephemeral ports, a rabbit played by
    urllib and Python's TLS client."""

    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.BootHandler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.tls = socket.socket()
        cls.tls.bind(("127.0.0.1", 0))
        cls.tls.listen(8)

        def accept():
            while True:
                c, a = cls.tls.accept()
                threading.Thread(target=server._tls_client, args=(c, a), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()
        cls.tls_port = cls.tls.getsockname()[1]

    def setUp(self):
        for row in server.KEYS.listing():
            server.KEYS.forget(row["mac"])

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.http.server_port}{path}") as r:
            return r.read()

    def _api(self, path, body=None, ctype="application/json"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.api.server_port}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": ctype}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _adopt(self):
        """kx.jsp as the rabbit runs it, then the owner says yes."""
        rpriv = bytes.fromhex(EXCHANGES[0]["rpriv"])
        rpub = adoption.public_key(rpriv)
        wpub = bytes.fromhex(self._get(f"/vl/kx.jsp?m=00:19:db:00:be:ef&p={rpub.hex()}").decode())
        key, colors = adoption.derive(rpriv, wpub, rpub, wpub)
        row = self._api("/api/adoption")[1]["keys"][0]
        self.assertEqual((row["state"], row["colors"]), ("pending", colors))
        code, _ = self._api("/api/adoption/confirm", {"mac": MAC, "fingerprint": row["fingerprint"]})
        self.assertEqual(code, 200)
        return key

    def test_a_stock_rabbit_gets_the_hybrid(self):
        self.assertEqual(self._get("/vl/bc.jsp?v=0.0.0.10&m=00:19:db:aa:bb:cc"), HYBRID)
        self.assertNotIn(b"xmpp_tls", self._get("/vl/locate.jsp?sn=0019dbaabbcc"))

    def test_kx_refuses_a_second_exchange(self):
        rpub = adoption.public_key(bytes.fromhex(EXCHANGES[0]["rpriv"]))
        self._get(f"/vl/kx.jsp?m={MAC}&p={rpub.hex()}")
        with self.assertRaises(urllib.error.HTTPError) as e:
            self._get(f"/vl/kx.jsp?m={MAC}&p={rpub.hex()}")
        self.assertEqual(e.exception.code, 409)

    def test_pending_gets_the_tls_runtime_unsigned_and_no_tls_port(self):
        rpub = adoption.public_key(bytes.fromhex(EXCHANGES[0]["rpriv"]))
        self._get(f"/vl/kx.jsp?m={MAC}&p={rpub.hex()}")
        self.assertEqual(self._get(f"/vl/bc.jsp?m={MAC}"), TLS_RUNTIME)
        self.assertNotIn(b"xmpp_tls", self._get(f"/vl/locate.jsp?sn={MAC}"))

    def test_confirm_needs_a_json_post_and_the_right_fingerprint(self):
        rpub = adoption.public_key(bytes.fromhex(EXCHANGES[0]["rpriv"]))
        self._get(f"/vl/kx.jsp?m={MAC}&p={rpub.hex()}")
        self.assertEqual(self._api("/api/adoption/confirm", {"mac": MAC, "fingerprint": "x"},
                                   ctype="text/plain")[0], 405)
        self.assertEqual(self._api("/api/adoption/confirm", {"mac": MAC, "fingerprint": "0" * 32})[0], 409)
        self.assertEqual(server.KEYS.get(MAC)["state"], "pending")

    def test_confirmed_rabbit_boots_and_talks_over_tls(self):
        key = self._adopt()
        sealed = self._get(f"/vl/bc.jsp?m={MAC}")
        self.assertIn(b"xmpp-tls:1", sealed)
        self.assertIn(f"xmpp_tls {server.XMPP_TLS_PORT}".encode(),
                      self._get(f"/vl/locate.jsp?sn={MAC}"))
        psk = adoption.tls_psk(key)

        # bc.jsp inside the TLS session, as the 1.0.0.2+ bootloader asks.
        with _client_ctx(MAC, psk).wrap_socket(socket.create_connection(("127.0.0.1", self.tls_port))) as s:
            self.assertEqual(s.version(), "TLSv1.3")
            s.sendall(f"GET /vl/bc.jsp?m=00:19:db:00:be:ef HTTP/1.0\r\n\r\n".encode())
            got = b""
            while True:
                b = s.recv(65536)
                if not b:
                    break
                got += b
        self.assertTrue(got.startswith(b"HTTP/1.0 200"))
        self.assertTrue(got.endswith(sealed))

        # XMPP inside the TLS session.
        with _client_ctx(MAC, psk).wrap_socket(socket.create_connection(("127.0.0.1", self.tls_port))) as s:
            s.sendall(b"<?xml version='1.0' encoding='UTF-8'?><stream:stream to='127.0.0.1' "
                      b"xmlns='jabber:client' xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>")
            s.settimeout(5)
            self.assertIn(b"stream:features", s.recv(4096))

    def test_a_wrong_key_fails_the_handshake(self):
        self._adopt()
        with self.assertRaises((ssl.SSLError, ConnectionError)):
            with _client_ctx(MAC, b"\x00" * 32).wrap_socket(
                    socket.create_connection(("127.0.0.1", self.tls_port))) as s:
                s.recv(1)

    def test_bc_over_tls_for_another_mac_is_refused(self):
        key = self._adopt()
        with _client_ctx(MAC, adoption.tls_psk(key)).wrap_socket(
                socket.create_connection(("127.0.0.1", self.tls_port))) as s:
            s.sendall(b"GET /vl/bc.jsp?m=0019db112233 HTTP/1.0\r\n\r\n")
            self.assertIn(b" 403 ", s.recv(4096))

    def test_the_plain_port_refuses_a_confirmed_mac(self):
        self._adopt()
        a, b = socket.socketpair()
        sess = server.XmppSession(a, ("127.0.0.1", 1))
        resp = base64.b64encode(f'username="{MAC}",realm="x",nonce="y"'.encode()).decode()
        sess._handle_stanza(f"<response xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>{resp}</response>")
        b.settimeout(2)
        self.assertIn(b"<failure", b.recv(4096))
        self.assertFalse(sess._alive)
        self.assertIsNone(sess.mac)

    def test_an_unknown_rabbit_on_the_plain_port_is_served_as_before(self):
        a, b = socket.socketpair()
        sess = server.XmppSession(a, ("127.0.0.1", 1))
        resp = base64.b64encode(b'username="0019dbaabbcc",realm="x"').decode()
        sess._handle_stanza(f"<response xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>{resp}</response>")
        b.settimeout(2)
        self.assertIn(b"<success", b.recv(4096))
        self.assertEqual(sess.mac, "0019dbaabbcc")

    def test_keepalive_goes_out_while_the_rabbit_chatters(self):
        """The rabbit writes a blank every few seconds: the server's socket
        never times out, and the keepalive must not wait for it to."""
        a, b = socket.socketpair()
        sess = server.XmppSession(a, ("127.0.0.1", 1))
        sess._mac, sess.bound, sess.authed, sess.resource = MAC, True, True, "idle"
        sess.last_tx -= server.KEEPALIVE_S + 1
        threading.Thread(target=sess.run, daemon=True).start()
        b.sendall(b" ")
        b.settimeout(3)
        self.assertIn(b"id='ka-", b.recv(4096))
        sess.close()

    def test_keepalive_is_a_whole_stanza(self):
        a, b = socket.socketpair()
        sess = server.XmppSession(a, ("127.0.0.1", 1))
        sess._mac = MAC
        sess.keepalive()
        b.settimeout(2)
        got = b.recv(4096)
        self.assertTrue(got.startswith(b"<iq ") and got.endswith(b"</iq>"))
        self.assertIn(b"getrunningstate", got)


if __name__ == "__main__":
    unittest.main()
