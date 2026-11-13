#!/usr/bin/env python3
"""Make a runtime bytecode start an OTA by itself, for the emulator.

On a rabbit the OTA begins when `FW <url>` arrives over XMPP. The emulator
has no XMPP server and must not register a fake rabbit against a real one,
so this turns the first thing a connected rabbit does -- the locate.jsp
request in runconfigstart -- into `flashFromUrl <url>`. Everything after
that call is the real path: http.mtl, ota_cb, parsefirm_fw, parsesig_fw,
verifySig, flashFirmware. Run after bytecode/build-bytecode.sh's patches,
on the same sources/ directory, before preprocessing.

  usage: patch_ota_test.py <sources-dir> <url>
"""
import sys
from pathlib import Path

src = Path(sys.argv[1]) / "main.mtl"
url = sys.argv[2]
s = src.read_text(encoding="latin-1")
# The request as stock Violet writes it, and as bytecode/patch_locate_tls.py
# leaves it (a choice between the TLS port and plain HTTP).
STOCK = ('set run=configwaitRun httprequest "GET" Secholn configurl nil '
         '#_configcbhttp HTTP_NORMAL')
TLS = ('set run=configwaitRun\n'
       '\t\t\tif LocateTlsWanted then LocateTlsRequest configurl #_configcbhttp\n'
       '\t\t\telse (set LocateTlsFailed=0; httprequest "GET" Secholn configurl nil '
       '#_configcbhttp HTTP_NORMAL)')
for old in (TLS, STOCK):
    if s.count(old) == 1:
        break
else:
    sys.exit("runconfigstart's locate.jsp request is not where it was")
s = s.replace(old, f'set run=configwaitRun flashFromUrl "{url}"', 1)
src.write_text(s, encoding="latin-1")
print(f"runconfigstart now calls flashFromUrl {url}")
