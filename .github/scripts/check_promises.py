#!/usr/bin/env python3
"""Fail when the repo promises something it does not deliver.

Born from a real incident: RECOVERY.md told people to upload
`bin/minimal/firmware0.0.0.13.signed.sim`, a path that is gitignored and was
never shipped — so the documented rescue path did not exist for anyone but the
maintainer. Nothing caught it until a user did. This script runs in CI so that
class of mistake fails the pull request instead.

Checks (all stdlib, run from the repo root):

  1. Every repo path quoted in a Markdown file exists in `git ls-files`.
     Paths under build-output dirs (bin/, obj/) only warn; paths followed by
     "not committed" / "ignored" are documented as absent and are skipped.
  2. Every sha256 quoted in a Markdown file is the hash of a tracked file, or is
     listed in known_external_hashes.txt (artifacts we fetch, don't host).
  3. Every firmware-arm/releases/*.signed.sim carries a valid Ed25519 signature
     (signing/verify_sim.py) AND is described by name in releases/README.md.
  4. The bootcode checksum pinned in nabaztag-violet/Dockerfile matches the
     committed nabaztag-violet/bootcode.hybrid.
  5. nabaztag-violet/config.yaml's version is the top entry of its CHANGELOG.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)

failures: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def tracked_files() -> set[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


TRACKED = tracked_files()
MD_FILES = sorted(f for f in TRACKED if f.endswith(".md"))

# Paths that live inside the firmware build container (the nabgcc checkout),
# not in this repo — legitimately quoted by the firmware docs.
CONTAINER_PREFIXES = ("nabgcc/", "mtl/", "mtl_linux/", "sys/", "src/", "inc/",
                      "interface/", "utils/", "upstream/tweetnacl/")
ABSENT_MARKERS = ("not committed", "not versioned", "ignored by git", "gitignored",
                  "local output", "(local)", "does not exist", "is not shipped")


def is_tracked_path(p: str) -> bool:
    p = p.rstrip("/")
    return p in TRACKED or any(t.startswith(p + "/") for t in TRACKED)


# ----------------------------------------------------------------- 1. paths
TOKEN = re.compile(r"`([^`\n]+)`")
for md in MD_FILES:
    text = Path(md).read_text(encoding="utf-8", errors="replace")
    md_dir = os.path.dirname(md)
    for m in TOKEN.finditer(text):
        tok = m.group(1).strip()
        if "/" not in tok or " " in tok:
            continue
        if any(c in tok for c in "<>*{}…$|") or "XXXX" in tok:
            continue
        if tok.startswith(("http://", "https://", "/", "~", ".../")):
            continue
        last = tok.rstrip("/").rsplit("/", 1)[-1]
        if "." not in last and not tok.endswith("/"):
            continue  # a slug like RedoXyde/nabgcc or page_a/done/u, not a file
        if re.match(r"^\d+\.\d+", tok):
            continue  # an IP-prefixed URL path
        tok = re.sub(r":[A-Za-z_][A-Za-z0-9_]*$|:\d+$", "", tok)  # file:function / file:line
        if tok.count("/") == 1 and tok.endswith("/"):
            continue  # a bare directory name like `state/` — too ambiguous to be a promise
        if tok.startswith(CONTAINER_PREFIXES) or "/mtl_linux/" in tok:
            continue
        after = text[m.end(): m.end() + 160].lower()
        before = text[max(0, m.start() - 160): m.start()].lower()
        if any(k in after or k in before for k in ABSENT_MARKERS):
            continue
        cands = [os.path.normpath(os.path.join(md_dir, tok)), os.path.normpath(tok)]
        if any(is_tracked_path(c) for c in cands):
            continue
        bare = os.path.normpath(tok)
        if re.search(r"(^|/)(bin|obj)(/|$)", bare) or "bin/" in before[-80:]:
            warnings.append(f"{md}: `{tok}` is a build output (not in git) — fine if the text says so")
            continue
        fail(f"{md}: quotes `{tok}` but no such tracked file (resolved from {md_dir or '.'})")

# ----------------------------------------------------------------- 2. hashes
_hash_cache: dict[str, str] = {}


def sha256_of(path: str) -> str:
    if path not in _hash_cache:
        _hash_cache[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return _hash_cache[path]


tracked_hashes = {sha256_of(f) for f in TRACKED if os.path.isfile(f)}
external_file = Path(".github/scripts/known_external_hashes.txt")
external = set()
if external_file.exists():
    for line in external_file.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            external.add(line.split()[0].lower())

for md in MD_FILES:
    text = Path(md).read_text(encoding="utf-8", errors="replace")
    for h in set(re.findall(r"\b[0-9a-f]{64}\b", text)):
        if h in tracked_hashes or h in external:
            continue
        fail(f"{md}: sha256 {h[:12]}… matches no tracked file and is not in known_external_hashes.txt")

# ----------------------------------------------------------------- 3. releases
releases = sorted(f for f in TRACKED if f.startswith("firmware-arm/releases/") and f.endswith(".signed.sim"))
if not releases:
    fail("firmware-arm/releases/ ships no .signed.sim at all")
rel_readme = Path("firmware-arm/releases/README.md")
rel_text = rel_readme.read_text() if rel_readme.exists() else ""
for sim in releases:
    name = os.path.basename(sim)
    if name not in rel_text:
        fail(f"{sim} is shipped but firmware-arm/releases/README.md never mentions `{name}`")
    r = subprocess.run([sys.executable, "firmware-arm/signing/verify_sim.py", sim],
                       capture_output=True, text=True)
    if r.returncode != 0 or "True" not in r.stdout:
        fail(f"{sim}: signature does not verify ({(r.stdout + r.stderr).strip()[:200]})")

# ----------------------------------------------------------------- 4. bootcode pin
dockerfile = Path("nabaztag-violet/Dockerfile").read_text()
pin = re.search(r"([0-9a-f]{64})\s+/app/bootcode\.hybrid", dockerfile)
if not pin:
    fail("nabaztag-violet/Dockerfile: no sha256 pin for /app/bootcode.hybrid")
elif not Path("nabaztag-violet/bootcode.hybrid").exists():
    fail("nabaztag-violet/bootcode.hybrid is missing — a fresh add-on install would serve no bytecode (issue #2)")
elif pin.group(1) != sha256_of("nabaztag-violet/bootcode.hybrid"):
    fail("nabaztag-violet/Dockerfile pins a different sha256 than the committed bootcode.hybrid")

# ----------------------------------------------------------------- 5. version
cfg = Path("nabaztag-violet/config.yaml").read_text()
chg = Path("nabaztag-violet/CHANGELOG.md").read_text()
v_cfg = re.search(r'^version:\s*"([^"]+)"', cfg, re.M)
v_chg = re.search(r"^## (\S+)", chg, re.M)
if not (v_cfg and v_chg):
    fail("could not read the add-on version from config.yaml / CHANGELOG.md")
elif v_cfg.group(1) != v_chg.group(1):
    fail(f"add-on version {v_cfg.group(1)} (config.yaml) != top CHANGELOG entry {v_chg.group(1)}")

# ----------------------------------------------------------------- report
for w in warnings:
    print(f"warn  {w}")
for f in failures:
    print(f"FAIL  {f}")
print(f"\n{len(MD_FILES)} markdown files, {len(releases)} release(s) checked — "
      f"{len(failures)} failure(s), {len(warnings)} warning(s)")
sys.exit(1 if failures else 0)
