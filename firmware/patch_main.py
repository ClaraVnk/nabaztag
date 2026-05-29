#!/usr/bin/env python3
"""Patch the Violet bootcode main.mtl to add the mic-stream module + RS/RT
program commands. Idempotent-ish (errors if markers are missing). Run with the
path to sources/main.mtl."""
import sys

path = sys.argv[1]
s = open(path, encoding="latin-1").read()

inc = "#include reclib.mtl"
if inc not in s:
    raise SystemExit("ERROR: reclib include not found")
if "#include micstream.mtl" not in s:
    s = s.replace(inc, inc + "\n#include micstream.mtl", 1)

ic = 'else if !strcmp key "IC" then (eval_IC_msg val;0)'
if ic not in s:
    raise SystemExit("ERROR: IC command not found")
if '!strcmp key "RS"' not in s:
    rs = (
        'else if !strcmp key "RS" then (let strstr val " " 0 -> sp in '
        '(if sp!=nil then startmicstream (useparamip (strsub val 0 sp)) (atoi (strsub val (sp+1) nil)) '
        'else startmicstream (useparamip val) 4000);0)\n'
        '\t\telse if !strcmp key "RT" then (stopmicstream;0)'
    )
    s = s.replace(ic, ic + "\n\t\t" + rs, 1)

# Volume command: SV <v> -> sndVol(v). v is the raw VS1003 attenuation
# (0 = loudest, ~254 = silent); the server maps a friendly 0-100 to it.
if '!strcmp key "SV"' not in s:
    rt = 'else if !strcmp key "RT" then (stopmicstream;0)'
    if rt not in s:
        raise SystemExit("ERROR: RT command not found (apply the RS/RT patch first)")
    s = s.replace(rt, rt + '\n\t\telse if !strcmp key "SV" then (sndVol (atoi val);0)', 1)

# OTA: FW <url> -> downloads a signed .sim, verifies the Ed25519 sig with
# the embedded pubkey, then calls flashFirmware. Requires the verifySig
# opcode added in our custom firmware (firmware-arm fork).
if "#include fwota.mtl" not in s:
    s = s.replace(inc, inc + "\n#include fwota.mtl", 1)
if '!strcmp key "FW"' not in s:
    sv = 'else if !strcmp key "SV" then (sndVol (atoi val);0)'
    if sv not in s:
        raise SystemExit("ERROR: SV command not found (apply SV patch first)")
    s = s.replace(sv, sv + '\n\t\telse if !strcmp key "FW" then (flashFromUrl val;0)', 1)

open(path, "w", encoding="latin-1").write(s)
print("patched OK")
