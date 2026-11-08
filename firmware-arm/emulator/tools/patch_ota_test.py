#!/usr/bin/env python3
"""Make a runtime bytecode start an OTA by itself, for the emulator.

On a rabbit the OTA begins when `FW <url>` arrives over XMPP. The emulator
has no XMPP server and must not register a fake rabbit against a real one,
so this turns the first thing a connected rabbit does -- the locate.jsp
request in runconfigstart -- into `flashFromUrl <url>`. Everything after
that call is the real path: http.mtl, ota_cb, parsefirm_fw, parsesig_fw,
verifySig, flashFirmware. Run after firmware/build-bytecode.sh's patches,
on the same sources/ directory, before preprocessing.

  usage: patch_ota_test.py <sources-dir> <url>
"""
import sys
from pathlib import Path

src = Path(sys.argv[1]) / "main.mtl"
url = sys.argv[2]
s = src.read_text(encoding="latin-1")
old = ('set run=configwaitRun httprequest "GET" Secholn configurl nil '
       '#_configcbhttp HTTP_NORMAL')
if old not in s:
    sys.exit("runconfigstart's locate.jsp request is not where it was")
s = s.replace(old, f'set run=configwaitRun flashFromUrl "{url}"', 1)
src.write_text(s, encoding="latin-1")
print(f"runconfigstart now calls flashFromUrl {url}")
