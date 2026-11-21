#!/usr/bin/env python3
"""make-ota.py -- Terrier OS as an update the rabbit's page can send.

    python3 karotz/lapinlibre/make-ota.py <key dir | terrier-os-*.img.xz> <out dir>
        [--priv <32-byte Ed25519 seed>]          # sign the manifest
    python3 karotz/lapinlibre/make-ota.py --check <out dir>/terrier-os-<v>.ota --pub <signing_pubkey.h>

The package is the key's files as they are (the install reads them from
/mnt/usbkey; the client binds the package there, see
firmware-arm/emulator/karotz/ota, OTA_MODE=package) and a manifest:

    terrier-os-manifest 1
    version v0.2.0-50-ga60c9e8f14c5
    build 02d7ab2873a9ff99
    file <sha256> <size> <path>        one per file, sorted by path

manifest.sig is the Ed25519 signature of the manifest's bytes, in hex, by the
Karotz client key (karotz/src/signing_pubkey.h): the key the client already
trusts for its own updates. A manifest cannot be taken for a client image
(those are ELF files carrying TK_IMAGE_MARK), nor the other way round. The
client checks the signature on the manifest alone, a few hundred bytes, then
each file's sha256 as it downloads it: the 24 MB package never has to be in
the Karotz's 64 MB of RAM at once, which signing the whole of it would need.

Writes <out>/terrier-os-<version>.ota/ (the files, manifest, manifest.sig
when signed) and <out>/terrier-os-<version>.ota.tar, which the release
carries and the Terrier unpacks. --check does what the client does.

The signing key is used only on explicit request (--priv); without it the
manifest is left unsigned, for a proof with a throwaway key.
"""
import argparse
import hashlib
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "firmware-arm" / "signing"))
import ed25519  # noqa: E402

MAGIC = "terrier-os-manifest 1"
# The key's own files; autorun.sig is the factory boot's, which an update
# does not go through.
SKIP = {"autorun.sig", "install-log.txt"}


def key_from_image(img: Path, dest: Path) -> Path:
    """The key's files out of a released .img.xz (one FAT32 partition at
    1 MiB, as make-img.sh writes it), with mtools."""
    raw = dest / "t.img"
    with lzma.open(img) as src, open(raw, "wb") as out:
        shutil.copyfileobj(src, out)
    key = dest / "key"
    key.mkdir()
    subprocess.run(["mcopy", "-s", "-i", f"{raw}@@1M", "::/*", str(key)], check=True)
    raw.unlink()
    return key


def conf(key: Path) -> dict:
    out = {}
    for line in (key / "install.conf").read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(key: Path) -> str:
    c = conf(key)
    version, build = c.get("BUILD_VERSION", ""), c.get("BUILD_ID", "")
    if not version or not build:
        sys.exit(f"{key}/install.conf has no BUILD_VERSION or BUILD_ID")
    if (key / "build_id").read_text().strip() != build:
        sys.exit("build_id and install.conf's BUILD_ID differ")
    lines = [MAGIC, f"version {version}", f"build {build}"]
    for p in sorted(q for q in key.rglob("*") if q.is_file()):
        rel = p.relative_to(key).as_posix()
        if rel in SKIP or rel.startswith("._"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", rel) or ".." in rel.split("/"):
            sys.exit(f"{rel}: a path the client would refuse")
        lines.append(f"file {sha256(p)} {p.stat().st_size} {rel}")
    return "\n".join(lines) + "\n"


def make(src: Path, out: Path, priv: Path | None) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        key = key_from_image(src, Path(tmp)) if src.is_file() else src
        text = manifest(key)
        version = text.splitlines()[1].split(" ", 1)[1]
        pkg = out / f"terrier-os-{version}.ota"
        if pkg.exists():
            shutil.rmtree(pkg)
        pkg.mkdir(parents=True)
        for line in text.splitlines()[3:]:
            rel = line.split(" ", 3)[3]
            (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(key / rel, pkg / rel)
        (pkg / "manifest").write_text(text)
        if priv:
            seed = priv.read_bytes()[:32]
            (pkg / "manifest.sig").write_text(ed25519.sign(seed, text.encode()).hex() + "\n")
    tar = out / f"{pkg.name}.tar"
    with tarfile.open(tar, "w") as t:
        t.add(pkg, arcname=pkg.name)
    print(f"{pkg} ({len(text.splitlines()) - 3} files{', signed' if priv else ', NOT signed'})")
    print(f"{tar} ({tar.stat().st_size} bytes)")
    return pkg


def read_pub(h: Path) -> bytes:
    body = h.read_text().split("{", 1)[1].split("}", 1)[0]
    b = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body))
    if len(b) != 32:
        sys.exit(f"{h}: not a 32-byte public key")
    return b


def check(pkg: Path, pub: Path) -> int:
    """What the client does: the signature on the manifest, then every
    file's size and sha256, and nothing on disk the manifest does not name."""
    text = (pkg / "manifest").read_bytes()
    sig = bytes.fromhex((pkg / "manifest.sig").read_text().strip())
    if not ed25519.verify(read_pub(pub), text, sig):
        print("FAIL the manifest's signature")
        return 1
    lines = text.decode().splitlines()
    if lines[0] != MAGIC:
        print("FAIL not a Terrier OS manifest")
        return 1
    bad = 0
    named = set()
    for line in lines[3:]:
        _, digest, size, rel = line.split(" ", 3)
        named.add(rel)
        p = pkg / rel
        if not p.is_file() or p.stat().st_size != int(size) or sha256(p) != digest:
            print(f"FAIL {rel}")
            bad += 1
    extra = [q.relative_to(pkg).as_posix() for q in pkg.rglob("*")
             if q.is_file() and q.relative_to(pkg).as_posix() not in named | {"manifest", "manifest.sig"}]
    for e in extra:
        print(f"FAIL {e}: not in the manifest")
    print(f"{lines[1]}, {lines[2]}: {len(named)} files, {'ok' if not bad and not extra else 'REFUSED'}")
    return 1 if bad or extra else 0


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("src", nargs="?")
    a.add_argument("out", nargs="?")
    a.add_argument("--priv", type=Path)
    a.add_argument("--check", type=Path)
    a.add_argument("--pub", type=Path)
    o = a.parse_args()
    if o.check:
        if not o.pub:
            a.error("--check needs --pub (the Terrier's terrier/karotz_signing_pubkey.h)")
        sys.exit(check(o.check, o.pub))
    if not o.src or not o.out:
        a.error("give the key dir or image, and the out dir")
    make(Path(o.src), Path(o.out), o.priv)


if __name__ == "__main__":
    os.umask(0o022)
    main()
