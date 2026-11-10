#!/usr/bin/env python3
"""locate.jsp over TLS, with the adoption key.

Each time the runtime (re)starts its XMPP session it first asks locate.jsp
where to connect, in plain HTTP: the request names the rabbit, the answer
names its warren and ports. Since Naboot 1.0.0.2 the bootloader fetches
bc.jsp over TLS on the warren's TLS port (boot-mods/boot_tls.mtl), where the
Terrier answers HTTP as well as XMPP; locate.jsp is served there too. This
patch has the runtime ask there, the same way.

The TLS context is free: XMPP has not started, or its socket is closed (the
session restarts from configstartRun), and XmppSessionStart begins its own
handshake with tlsStart afterwards.

When: the warren that signed this bytecode requires TLS (XmppTlsRequired,
patch_tls.py), the rabbit holds a key (tlsState >= 0), and no proxy is set.
A failed or timed-out attempt leaves LocateTlsFailed set, and the next
attempt is plain, as before; an answer over TLS clears it, so does a plain
attempt, and the one after that is TLS again. What the plain fallback loses
is privacy, not integrity: XMPP still requires TLS whatever locate.jsp says.

The runtime's Httpreq is the stock one (aliveH, not the bootloader's
startH): runconfigwait's timeout (httpinactive) and httpabort work on it
unchanged.

Applied after patch_tls.py (build-bytecode.sh does). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* locate-over-tls */"

TLS_MARKER = "/* xmpp-over-tls */"
FUNS_ANCHOR = "var gConfigStart = 0;;\n"
FUNS = '''/* locate-over-tls */
/* ------------------------------------------------------------------------
 * locate.jsp on the warren's TLS port, as the bootloader asks for bc.jsp.
 * See bytecode/patch_locate_tls.py. */
const LOCATE_TLS_DONE=3;;	// stateH once the answer, or the failure, is handed over
var LocateTlsReq;;	// the GET, waiting for the handshake
var LocateTlsFailed=0;;	// the last attempt over TLS got no answer: the next one is plain

fun LocateTlsWanted= XmppTlsRequired && (tlsState)>=0 && !confGetProxy && !LocateTlsFailed;;

// Records to write, after whatever is still waiting.
fun LocateTlsQueue httpreq s=
	if s!=nil then
	(
		if httpreq.outputH==nil then set httpreq.outputH=s
		else set httpreq.outputH=strcat (strsub httpreq.outputH httpreq.indexH (strlen httpreq.outputH)-httpreq.indexH) s;
		set httpreq.indexH=0
	);
	0;;

// Write what the stack takes now; 0 on a socket error.
fun LocateTlsPush t httpreq=
	if httpreq.outputH==nil then 1
	else let writetcp t httpreq.outputH httpreq.indexH -> i in
	if i==nil then 0
	else
	(
		if i>=strlen httpreq.outputH then (set httpreq.outputH=nil; set httpreq.indexH=0)
		else set httpreq.indexH=i;
		1
	);;

// Every decrypted record the bytes just fed complete, in order.
fun LocateTlsTake httpreq p=
	if p!=nil then
	(
		set httpreq.inputH=p::httpreq.inputH;
		LocateTlsTake httpreq tlsIn nil
	);
	0;;

// The answer to _configcbhttp, or nil, which retries (in the clear).
fun LocateTlsEnd t httpreq=
	if httpreq.stateH!=LOCATE_TLS_DONE then
	(
		set httpreq.stateH=LOCATE_TLS_DONE;
		closetcp t;
		if httpreq.inputH==nil then
		(
			Secholn "locate.jsp over TLS: no answer, next time in the clear";
			call httpreq.cbH [httpreq nil]
		)
		else
		(
			set LocateTlsFailed=0;
			Secholn "locate.jsp over TLS: answered";
			call httpreq.cbH [httpreq strcatlist rev httpreq.inputH nil]
		);
		0
	);
	0;;

fun LocateTlsEvent t val msg httpreq=
	if httpreq.stateH==LOCATE_TLS_DONE then 0
	else if val==TCPWRITE then
	(
		set httpreq.aliveH=time_ms;
		if httpreq.stateH==HTTP_REACH then
		(
			set httpreq.stateH=HTTP_CONNECTED;
			// The PSK identity XmppSessionStart uses too.
			LocateTlsQueue httpreq tlsStart (webmac netMac)
		);
		// tlsStart says nil without a key: nothing to send, give up now.
		if httpreq.outputH==nil && (tlsState)!=1 && (tlsState)!=2 then LocateTlsEnd t httpreq
		else if !LocateTlsPush t httpreq then LocateTlsEnd t httpreq
		else 0;
		0
	)
	else if val==TCPCLOSE || msg==nil || 0==strlen msg then LocateTlsEnd t httpreq
	else
	(
		set httpreq.aliveH=time_ms;
		LocateTlsTake httpreq tlsIn msg;
		LocateTlsQueue httpreq tlsOut nil;
		let tlsState -> st in
		if st==2 && LocateTlsReq!=nil then
		(
			LocateTlsQueue httpreq tlsOut LocateTlsReq;
			set LocateTlsReq=nil;
			0
		)
		else if st==3 then
		(
			// A failed handshake keeps nothing it may have decrypted.
			set httpreq.inputH=nil;
			LocateTlsEnd t httpreq
		)
		else 0;
		if httpreq.stateH!=LOCATE_TLS_DONE && !LocateTlsPush t httpreq then LocateTlsEnd t httpreq;
		0
	);;

fun LocateTlsOpen ip httpreq=
	if ip==nil then (call httpreq.cbH [httpreq nil]; 0)
	else
	(
		set httpreq.cnxH=opentcp netip nil useparamip ip XMPP_TLS_DEFAULT_PORT fixarg4 #LocateTlsEvent httpreq;
		set httpreq.stateH=HTTP_REACH;
		0
	);;

fun LocateTlsRequest url cb=
	set LocateTlsFailed=1;	// until an answer comes
	let cuturl url -> [host port path] in
	let [indexH:0 cbH:cb typeH:HTTP_NORMAL stateH:HTTP_SOLVE aliveH:time_ms sonH:nil redirLevelH:0] -> httpreq in
	(
		nil; port;
		Secho "locate.jsp over TLS: "; Secholn url;
		set LocateTlsReq=strcatlist "GET "::path::" HTTP/1.0\\13\\nUser-Agent: MTL\\13\\nHost: "::host::"\\13\\n\\13\\n"::nil;
		if isip host 0 then LocateTlsOpen host httpreq
		else (dnsreq host fixarg2 #LocateTlsOpen httpreq; 0);
		httpreq
	);;

'''
REQ_OLD = '\t\tset run=configwaitRun httprequest "GET" Secholn configurl nil #_configcbhttp HTTP_NORMAL\n'
REQ_NEW = ('\t\tset run=configwaitRun\n'
           '\t\t\tif LocateTlsWanted then LocateTlsRequest configurl #_configcbhttp\n'
           '\t\t\telse (set LocateTlsFailed=0; httprequest "GET" Secholn configurl nil #_configcbhttp HTTP_NORMAL)\n')


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: locate.jsp already goes over TLS")
        return False
    if TLS_MARKER not in text or "fun XmppTlsRequired" not in text:
        sys.exit(f"FAIL {path}: patch_tls.py has not been applied (no XmppTlsRequired)")
    for what, needle in (("gConfigStart", FUNS_ANCHOR),
                         ("runconfigstart's locate.jsp request", REQ_OLD)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(needle)}")
    text = text.replace(FUNS_ANCHOR, FUNS + FUNS_ANCHOR, 1)
    text = text.replace(REQ_OLD, REQ_NEW, 1)
    for s in (FUNS, REQ_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: locate.jsp is asked over TLS when the warren requires it")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
