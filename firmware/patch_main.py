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

open(path, "w", encoding="latin-1").write(s)
print("patched OK")
