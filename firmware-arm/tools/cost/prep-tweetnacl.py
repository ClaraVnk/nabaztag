#!/usr/bin/env python3
"""Make tweetnacl/ for run.sh: upstream TweetNaCl with the firmware's two
patches (sign_open_alias, verify_wdt) applied through apply-mods.py itself,
so the harness measures the code the firmware compiles -- minus
strip_tweetnacl, so crypto_scalarmult is still there to measure.

    python3 prep-tweetnacl.py /path/to/nabaztag-repo
"""
import importlib.util
import pathlib
import shutil
import sys

repo = pathlib.Path(sys.argv[1]).resolve()
here = pathlib.Path(__file__).resolve().parent
up = repo / "firmware-arm/upstream/tweetnacl"
out = here / "tweetnacl"
out.mkdir(exist_ok=True)
shutil.copy(up / "tweetnacl.h", out / "tweetnacl.h")
shutil.copy(up / "tweetnacl.c", out / "tweetnacl_upstream.c")

# apply-mods.py patches <root>/src/crypto/tweetnacl.c; give it that shape.
root = here / "_prep"
(root / "src/crypto").mkdir(parents=True, exist_ok=True)
shutil.copy(up / "tweetnacl.c", root / "src/crypto/tweetnacl.c")
spec = importlib.util.spec_from_file_location("am", repo / "firmware-arm/apply-mods.py")
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)
am.patch_sign_open_aliasing(root)
am.patch_verify_wdt(root)
shutil.copy(root / "src/crypto/tweetnacl.c", out / "tweetnacl.c")
shutil.rmtree(root)
print(f"[ok]   {out}/tweetnacl.c: upstream + sign_open_alias + verify_wdt")
