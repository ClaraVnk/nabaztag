#!/usr/bin/env python3
"""Run apply-mods.py over every mode on a staged copy of the vendored tree,
with no compiler: does each step list still apply, and what does it leave?

    firmware-arm/tools/mods-matrix.py                 # every naboot-* mode
    firmware-arm/tools/mods-matrix.py --all           # every mode
    firmware-arm/tools/mods-matrix.py naboot-1.2.1.0  # one or more modes
    firmware-arm/tools/mods-matrix.py --out before.txt
    git show main:firmware-arm/apply-mods.py > /tmp/apply-main.py
    firmware-arm/tools/mods-matrix.py --all --apply /tmp/apply-main.py --out before.txt

For each mode: stage the tree as the Dockerfile does (vendor/nabgcc, the
crypto beside it in sae-crypto/, keys/, pages/, boot-mods/, TweetNaCl and
nab_sig in place), run apply-mods.py with the build inputs pinned
(NAB_BUILD_MARK=1, NAB_BOOT_FLOOR=202601010000), and print one line:

    mode  OK|FAIL  tree=<sha256 of every file's bytes>  flat=<the same, CRLF read as LF>  crlf=<files that end lines with CRLF>

Two runs whose `flat` agree and whose `tree` differ changed only line
endings. That is the check the CRLF sweep of 2026-11-18 was proven with
(docs/SECURITY-FINDINGS-2026-11.md, C-1), beside a Nab.bin comparison on the
dev VM; a mode that FAILs says which anchor broke. Exit status 1 when any
mode fails.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# --apply names another copy of apply-mods.py (a `git show REV:... > file`):
# the before of a before/after.
APPLY = HERE / "apply-mods.py"

# What the Dockerfile COPYs in before apply-mods runs, in its words.
CRYPTO = ("p256.c p256.h sha256.c sha256.h cmac.c cmac.h sae.c sae.h nab_kx.c nab_kx.h "
          "nab_kx_vm.c nab_tls.c nab_tls.h nab_tls_vm.c nab_rng.c nab_rng.h nab_pki.h "
          "nab_bn.c nab_rsa.c nab_ecdsa.c nab_x509.c nab_sha384.c chachapoly.c "
          "chachapoly.h nab_https_vm.c").split()


def modes() -> list[str]:
    src = APPLY.read_text()
    m = re.search(r'choices=\(([^)]*)\)', src)
    return re.findall(r'"([^"]+)"', m.group(1))


def stage(build: Path) -> Path:
    root = build / "nabgcc"
    shutil.copytree(HERE / "vendor/nabgcc", root, symlinks=True)
    (root / "src/crypto").mkdir(exist_ok=True)
    shutil.copy(HERE / "upstream/tweetnacl/tweetnacl.c", root / "src/crypto/tweetnacl.c")
    shutil.copy(HERE / "crypto/nab_sig.c", root / "src/crypto/nab_sig.c")
    shutil.copy(HERE / "upstream/tweetnacl/tweetnacl.h", root / "inc/tweetnacl.h")
    shutil.copy(HERE / "crypto/nab_sig.h", root / "inc/nab_sig.h")
    shutil.copy(HERE / "crypto/aes128.c", root / "src/net/aes128.c")
    shutil.copy(HERE / "crypto/aes128.h", root / "inc/net/aes128.h")
    (build / "sae-crypto").mkdir()
    for f in CRYPTO:
        shutil.copy(HERE / "crypto" / f, build / "sae-crypto" / f)
    shutil.copy(HERE / "keys/signing_pubkey.h", root / "inc/signing_pubkey.h")
    shutil.copytree(HERE / "keys", build / "keys")
    shutil.copytree(HERE / "pages", build / "pages")
    shutil.copytree(HERE / "boot-mods", build / "boot-mods")
    return root


def digest(root: Path) -> tuple[str, str, int]:
    raw, flat, crlf = hashlib.sha256(), hashlib.sha256(), 0
    for p in sorted(x for x in root.rglob("*") if x.is_file() and not x.is_symlink()):
        rel = str(p.relative_to(root)).encode()
        b = p.read_bytes()
        raw.update(rel); raw.update(b)
        flat.update(rel); flat.update(b.replace(b"\r\n", b"\n"))
        crlf += b"\r\n" in b
    return raw.hexdigest()[:16], flat.hexdigest()[:16], crlf


def run(mode: str, keep: Path | None) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix=f"mods-{mode}-") as tmp:
        build = Path(tmp) / "build"
        build.mkdir()
        root = stage(build)
        env = dict(os.environ, NAB_BUILD_MARK="1", NAB_BOOT_FLOOR="202601010000",
                   NAB_BOOT_ANCHOR=str(build / "keys/boot_anchor.der"))
        env.pop("NAB_SIM", None)
        r = subprocess.run([sys.executable, str(APPLY), "--mode", mode, str(root)],
                           env=env, capture_output=True, text=True)
        ok = r.returncode == 0 and "--- all steps OK ---" in r.stdout
        if keep is not None:
            (keep / f"{mode}.log").write_text(r.stdout + r.stderr)
            if (keep / mode).exists():
                shutil.rmtree(keep / mode)
            shutil.copytree(root, keep / mode, symlinks=True)
        if not ok:
            why = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["?"])[-1]
            return False, f"{mode}  FAIL  {why}"
        tree, flat, crlf = digest(root)
        return True, f"{mode}  OK  tree={tree}  flat={flat}  crlf={crlf}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*")
    ap.add_argument("--all", action="store_true", help="every mode, not only naboot-*")
    ap.add_argument("--out", help="also write the lines to this file")
    ap.add_argument("--keep", help="keep each patched tree and log under this directory")
    ap.add_argument("--apply", help="another apply-mods.py to run (default: the repo's)")
    a = ap.parse_args()
    global APPLY
    home = None
    if a.apply:
        # Some steps read pages/, boot-mods/ and crypto/ beside the script, so
        # the other copy runs from a home where those are links to ours.
        home = tempfile.TemporaryDirectory(prefix="mods-apply-")
        for d in ("crypto", "pages", "boot-mods", "keys", "upstream"):
            os.symlink(HERE / d, Path(home.name) / d)
        shutil.copy(a.apply, Path(home.name) / "apply-mods.py")
        APPLY = Path(home.name) / "apply-mods.py"
    todo = a.modes or [m for m in modes() if a.all or m.startswith("naboot-")]
    keep = Path(a.keep) if a.keep else None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
    lines, bad = [], 0
    for m in todo:
        ok, line = run(m, keep)
        print(line, flush=True)
        lines.append(line)
        bad += not ok
    if a.out:
        Path(a.out).write_text("\n".join(lines) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
