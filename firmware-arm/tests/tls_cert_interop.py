"""The certificate session (NAB_TLS_CERTS) against `openssl s_server`.

  python3 tls_cert_interop.py --client /tmp/t_tls_cert_client

Makes a chain the way the web has them -- an RSA-4096 root, a P-384
intermediate and a P-256 leaf (Let's Encrypt's shape), and an all-RSA one --
serves a page of 40 KB (records of 16 KB, which OpenSSL sends whatever it is
asked) under each cipher suite, and checks that the client gets it. Then
what must fail: another host name, a date outside the certificate, a root
it does not trust, a chain cut before its intermediate.
"""
import argparse, datetime, os, re, socket, subprocess, sys, tempfile, time
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec

T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
T1 = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
NOW = "20261113120000"

def name(cn): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

def cert(subject, key, issuer, ikey, h, ca, san=None):
    b = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(T0).not_valid_after(T1)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in san]), critical=False)
    return b.sign(ikey, h)

def pem(c): return c.public_bytes(serialization.Encoding.PEM)
def keypem(k): return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def run(client, d, chain, leafkey, suite, sni, now, anchor, want_ok, label, rounds=1):
    open(f"{d}/leaf.pem", "wb").write(pem(chain[0]))
    open(f"{d}/chain.pem", "wb").write(b"".join(pem(c) for c in chain[1:]))
    open(f"{d}/key.pem", "wb").write(keypem(leafkey))
    port = free_port()
    args = ["openssl", "s_server", "-accept", str(port), "-tls1_3", "-ciphersuites", suite,
            "-cert", f"{d}/leaf.pem", "-key", f"{d}/key.pem", "-WWW", "-quiet"]
    if len(chain) > 1:
        args += ["-cert_chain", f"{d}/chain.pem"]
    srv = subprocess.Popen(args, cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(0.8)
    try:
        out = subprocess.run([client, "127.0.0.1", str(port), sni, now, anchor, "/page.html"],
                             capture_output=True, text=True, timeout=240,
                             env={**os.environ, "NAB_TLS_ROUNDS": str(rounds)})
    finally:
        srv.kill(); srv.wait()
    ok = out.returncode == 0 and "200" in out.stdout and "40" in out.stdout.split("answer")[-1]
    passed = ok if want_ok else out.returncode != 0
    if rounds > 1:
        # The second handshake finds its links proven (nab_tls.c signed_by):
        # only the server's own signature and X25519 are left to compute.
        ticks = [int(t) for t in re.findall(r"handshake complete, (\d+) watchdog ticks", out.stdout)]
        passed = passed and len(ticks) == rounds and ticks[1] * 2 < ticks[0]
        label += f" (ticks {ticks})"
    print(f"  {'ok  ' if passed else 'FAIL'}  {label}: {out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr.strip()[-200:]}")
    return passed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    a = ap.parse_args()
    d = tempfile.mkdtemp(prefix="nab-tls-cert-")
    with open(f"{d}/page.html", "wb") as f:
        f.write(b"x" * 40000)
    root = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    rootc = cert("Test Root", root, "Test Root", root, hashes.SHA384(), True)
    open(f"{d}/root.der", "wb").write(rootc.public_bytes(serialization.Encoding.DER))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    otherc = cert("Other Root", other, "Other Root", other, hashes.SHA256(), True)
    open(f"{d}/other.der", "wb").write(otherc.public_bytes(serialization.Encoding.DER))
    ie = ec.generate_private_key(ec.SECP384R1()); le = ec.generate_private_key(ec.SECP256R1())
    iec = cert("Test E1", ie, "Test Root", root, hashes.SHA384(), True)
    lec = cert("podcast.example.test", le, "Test E1", ie, hashes.SHA384(), False, ["podcast.example.test"])
    ir = rsa.generate_private_key(public_exponent=65537, key_size=2048); lr = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    irc = cert("Test R1", ir, "Test Root", root, hashes.SHA256(), True)
    lrc = cert("radio.example.test", lr, "Test R1", ir, hashes.SHA256(), False, ["radio.example.test"])
    R, O = f"{d}/root.der", f"{d}/other.der"
    CH, AES = "TLS_CHACHA20_POLY1305_SHA256", "TLS_AES_128_GCM_SHA256"
    results = [
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", NOW, R, True, "ECDSA chain, ChaCha20-Poly1305"),
        run(a.client, d, [lec, iec], le, AES, "podcast.example.test", NOW, R, True, "ECDSA chain, AES-128-GCM"),
        run(a.client, d, [lrc, irc], lr, CH, "radio.example.test", NOW, R, True, "RSA chain (PSS), ChaCha20-Poly1305"),
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", NOW, R, True,
            "the second connection reuses the proven chain", rounds=2),
        run(a.client, d, [lec, iec], le, CH, "evil.example.test", NOW, R, False, "another host name is refused"),
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", "20270301000000", R, False, "an expired certificate is refused"),
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", NOW, O, False, "a root it does not trust is refused"),
        # The bootloader has no clock: ">" + the build minute + "." means "not
        # expired before the build", the start unchecked (boot_https).
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", ">202611131200.", R, True,
            "boot: in date at the build"),
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", ">202001010000.", R, True,
            "boot: a certificate issued after the build passes"),
        run(a.client, d, [lec, iec], le, CH, "podcast.example.test", ">202703010000.", R, False,
            "boot: expired before the build is refused"),
        run(a.client, d, [lec], le, CH, "podcast.example.test", NOW, R, False, "a chain without its intermediate is refused"),
    ]
    sys.exit(0 if all(results) else 1)

if __name__ == "__main__":
    main()
