#!/usr/bin/env python3
"""Patch the Violet bootcode sources/dns.mtl to resolve ".local" names over
multicast mDNS instead of unicast DNS to the gateway. Run with the path to
sources/dns.mtl (mdnsresolve.mtl must already be copied into the same sources/).

Idempotent: re-running is a no-op. Errors loudly if the upstream anchors moved.
"""
import sys

path = sys.argv[1]
s = open(path, encoding="latin-1").read()

# 1. Pull in the resolver helpers (constants + the ".local" test).
inc = "#include dns_protos.mtl"
if inc not in s:
    raise SystemExit("ERROR: dns_protos include not found — dns.mtl layout changed?")
if "#include mdnsresolve.mtl" not in s:
    s = s.replace(inc, inc + "\n#include mdnsresolve.mtl", 1)

# 2. Route the query in dnsreq: ".local" -> mDNS multicast, else unchanged.
# The response path (cbnetdns on DNSLOCAL) is untouched: a legacy-unicast mDNS
# reply echoes our id and comes back to :1597, exactly like a normal DNS reply.
old = (
    "\t\tlet listnth netdnslist 0 -> netdns in\n"
    "\t\t\tudpsend netip DNSLOCAL netdns 53 tramedns nil;"
)
new = (
    "\t\tif mdnsr_islocal domain then\n"
    "\t\t\tudpsend netip DNSLOCAL MDNSR_IP MDNSR_PORT tramedns MDNSR_MAC\n"
    "\t\telse\n"
    "\t\t(let listnth netdnslist 0 -> netdns in\n"
    "\t\t\tudpsend netip DNSLOCAL netdns 53 tramedns nil);"
)
if "mdnsr_islocal domain" not in s:
    if old not in s:
        raise SystemExit("ERROR: dnsreq send line not matched — dns.mtl layout changed?")
    s = s.replace(old, new, 1)

open(path, "w", encoding="latin-1").write(s)
print("patched OK")
