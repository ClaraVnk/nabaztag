#!/usr/bin/env python3
"""The runtimes a Terrier serves, as a release any Terrier can take (Clara,
2026-11-26: "on publie les bytecodes en release signée"; docs/FEDERATION.md
in the Terrier, step 3). Until now they sat in the warren's /data/bytecode
alone, and no other Terrier could serve a tag.

    bytecode/release-runtime.py build <version> <out dir>     # e.g. 2026.11.26
    bytecode/release-runtime.py sign  <out dir>               # on the signing machine
    bytecode/release-runtime.py check <out dir> [pubkey hex]

`build` makes baseline-https and baseline-tls with reproduce-served.sh, so a
release is byte for byte what the warren serves, and writes
runtime-<version>.json: each file's sha256 and size and the commit it was
built from. `sign` signs that manifest with the Naboot key (Ed25519 over
"terrier-runtime-1\\n" + the manifest: a rabbit, which verifies images,
never takes it for one), into runtime-<version>.json.sig. The Terrier
(releases.py, RUNTIME) checks the signature, then every file against the
manifest, before a file goes into its data/bytecode.

The sources: Violet's bootcode as OpenJabNab published it, GPL-2.0
(firmware-arm/vendor/openjabnab), and the patches in bytecode/. The release
notes point at both.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "firmware-arm" / "signing"))
import ed25519  # noqa: E402

CONTEXT = b"terrier-runtime-1\n"
KINDS = {"https": "08cf39b", "tls": "4c82037"}      # as reproduce-served.sh pins them


def _manifest(out: Path) -> Path:
    found = sorted(out.glob("runtime-*.json"))
    if len(found) != 1:
        sys.exit(f"{out}: expected one runtime-<version>.json, found {len(found)}")
    return found[0]


def build(version: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind, commit in KINDS.items():
        name = f"baseline-{kind}.hybrid"
        subprocess.run(["bash", str(HERE / "reproduce-served.sh"), kind, str(out / name)], check=True)
        data = (out / name).read_bytes()
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "commit": commit}
    m = {"format": 1, "tag": f"runtime-{version}", "files": files,
         "sources": "https://github.com/ClaraVnk/nabaztag/tree/main/bytecode"}
    (out / f"runtime-{version}.json").write_bytes(json.dumps(m, indent=1, sort_keys=True).encode() + b"\n")
    print(f"runtime-{version}: " + ", ".join(f"{n} {f['sha256'][:16]}" for n, f in files.items()))


def sign(out: Path, priv_path: Path) -> None:
    man = _manifest(out)
    priv = priv_path.read_bytes()
    if len(priv) != 32:
        sys.exit("the private seed must be 32 bytes")
    sig = ed25519.sign(priv, CONTEXT + man.read_bytes())
    Path(str(man) + ".sig").write_bytes(sig)
    print(f"signed {man.name}; public key {ed25519.secret_to_public(priv).hex()}")


def check(out: Path, pub_hex: str | None) -> None:
    man = _manifest(out)
    raw = man.read_bytes()
    if pub_hex:
        sig = Path(str(man) + ".sig").read_bytes()
        if not ed25519.verify(bytes.fromhex(pub_hex), CONTEXT + raw, sig):
            sys.exit("signature refused")
        print("signature ok")
    for name, f in json.loads(raw)["files"].items():
        data = (out / name).read_bytes()
        ok = hashlib.sha256(data).hexdigest() == f["sha256"] and len(data) == f["size"]
        print(f"{name}: {'ok' if ok else 'DIFFERS'}")
        if not ok:
            sys.exit(1)


def main() -> None:
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "build":
        build(a[1], Path(a[2]))
    elif len(a) >= 2 and a[0] == "sign":
        sign(Path(a[1]), Path(a[2]) if len(a) > 2 else Path.home() / ".nabaztag" / "signing_key.bin")
    elif len(a) >= 2 and a[0] == "check":
        check(Path(a[1]), a[2] if len(a) > 2 else None)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
