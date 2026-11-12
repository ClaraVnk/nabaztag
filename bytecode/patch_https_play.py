#!/usr/bin/env python3
"""The rabbit plays an https:// stream itself (Naboot 1.2.0.0, tls_certs).

2026-11-13. Until now a podcast reached the rabbit through the warren: the
warren fetched it over HTTPS and relayed it, in the clear or inside the
session (patch_live_in_session.py), so every rabbit listening was the
warren's bandwidth and processor. Clara: the warren stops relaying; that is
the point. The rabbit now opens the host itself.

The stock HTTP client (http.mtl) is kept whole: the TLS sits between it and
TCP. A request whose URL starts with https:// gets httpsH, its host, and its
connection's events go to _httpstcpevent instead of _httptcpevent:

  connected     the ClientHello (httpsStart host now roots), not the request
  bytes in      httpsIn: each decrypted record goes to httptcpread, exactly
                where the plain bytes went; then httpsOut nil, our Finished
                or an alert; and, the first time the session is ready, the
                request, sealed (httpsOut request)
  closed        httptcpread nil, as before

so the header parsing, the 302 handling, http-cap's ceilings and the audio
player (_wavcbhttp) see what they always saw.

The roots and the clock come from the warren with the order: it pushes the
one or two roots that anchor that host's chain as a session resource
(res-in-session), then sends

    ST https://host/path#r<N>@<YYYYMMDDHHMMSS>

The fragment never reaches the host (it is cut before the request): <N> is
the resource holding the roots, DER end to end; the time is the warren's,
for the certificates' dates.

One https request at a time (nab_tls's session 1). A redirect to another
https host is followed; its chain must end at one of the same roots, or the
handshake fails and the callback gets nil, which ends the stream as a 404
does.

Its own literal, "xmpp-https:0", flipped to 1 under the HMAC by a warren
that knows the rabbit runs Naboot 1.2.0.0: an older firmware has no https
opcodes, and the code that calls them never runs there.

Applied to http.mtl and main.mtl in a sources directory, after
patch_http_cap.py and patch_live_in_session.py. Idempotent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "/* https-play */"

# --- http.mtl -----------------------------------------------------------------

TYPE_RE = re.compile(r"(type Httpreq=\[[^\]]*?)\];;\n")

EVENT_ANCHOR = "fun _httptcpevent t val msg sock=\n"
EVENT_FUNS = MARKER + '''
// https-play (patch_https_play.py): an https:// request runs over nab_tls's
// session 1. The roots and the clock are set by HttpsPlay (main.mtl) before
// the request starts.
var HttpsRoots;;	// DER roots from the warren, end to end
var HttpsNow;;	// "YYYYMMDDHHMMSS", the warren's clock
const HTTPS_FAILED=3;;	// NAB_TLS_FAILED
const HTTPS_CLOSED=4;;	// NAB_TLS_CLOSED

fun _httpssend cnx s=
	if s!=nil then (writetcp cnx s 0; 0);
	0;;

// Every record nab_tls has decrypted, into the stock reader.
fun _httpsdrain cnx httpreq=
	let httpsIn nil -> p in
	if p==nil then 0
	else (httptcpread cnx p httpreq; _httpsdrain cnx httpreq);;

fun _httpstcpevent t val msg httpreq=
	if val==TCPWRITE then
	(
		set httpreq.stateH=HTTP_CONNECTED;
		set httpreq.aliveH=time_ms;
		if httpreq.indexH==0 then	// connected: the ClientHello, once
		(
			set httpreq.indexH=nil;
			let httpsStart httpreq.httpsH HttpsNow HttpsRoots -> ch in
			if ch==nil then (Secholn "https: no start"; closetcp t; call httpreq.cbH [httpreq nil]; 0)
			else (Secho "https: hello to "; Secholn httpreq.httpsH; _httpssend t ch; 0)
		);
		0
	)
	else if val==TCPCLOSE then (httptcpread t nil httpreq; 0)
	else
	(
		set httpreq.aliveH=time_ms;
		let httpsIn msg -> p in
		(
			if p!=nil then httptcpread t p httpreq;
			_httpsdrain t httpreq;
			_httpssend t httpsOut nil;	// our Finished, or the alert that ends it
			let httpsState -> s in
			if s==HTTPS_FAILED then
			(
				Secholn "https: refused";
				closetcp t;
				call httpreq.cbH [httpreq nil];
				0
			)
			else if s==2 && httpreq.outputH!=nil then
			(
				Secholn "https: ready, request sent";
				_httpssend t httpsOut httpreq.outputH;
				set httpreq.outputH=nil;
				0
			)
			else 0
		)
	);
	0;;

'''

OPEN_OLD = "\t\tset httpreq.cnxH=opentcp netip nil useparamip ip port fixarg4 #_httptcpevent httpreq;\n"
OPEN_NEW = ("\t\tset httpreq.cnxH=if httpreq.httpsH!=nil then opentcp netip nil useparamip ip port fixarg4 #_httpstcpevent httpreq\t// https-play\n"
            "\t\t\telse opentcp netip nil useparamip ip port fixarg4 #_httptcpevent httpreq;\n")

# The line ends in a French comment, whose accents depend on the file's
# encoding: matched without it.
CUT_OLD = "\tlet cuturl url ->[host port path] in"
CUT_NEW = ("\tlet !strcmp (strsub url 0 8) \"https://\" -> secure in\t// https-play\n"
           "\tlet if secure then strsub url 8 nil else url -> url in\n"
           "\tlet cuturl url ->[host port0 path] in let if secure && port0==80 then 443 else port0 -> port in")

REC_OLD = "sonH:nil redirLevelH:0] -> httpreq in"
REC_NEW = "sonH:nil redirLevelH:0 httpsH:(if secure then host else nil)] -> httpreq in"

# The proxy, when the configuration page set one, is plain HTTP: never for https.
PROXY_RE = re.compile(r"\bif confGetProxy then\b")

# --- main.mtl -----------------------------------------------------------------

PLAY_ANCHOR = "// res-in-session: `ST res:N` plays resource N, received inside the session\n"
PLAY_FUNS = MARKER + '''
// https-play: `ST https://host/path#r<N>@<YYYYMMDDHHMMSS>`. 1 when the value
// was an https URL: played by the rabbit itself when the warren flipped the
// policy, refused (silence) otherwise -- the stock client cannot read it.
var HttpsPolicy="xmpp-https:0";;
fun HttpsOn= (strget HttpsPolicy 11)==49;;

fun HttpsPlay val=
	if val==nil || (strlen val)<9 || strcmp (strsub val 0 8) "https://" then 0
	else
	(
		Secho "https: ST "; Secho val; Secho " at "; Iecholn time_ms;
		let strstr val "#r" 0 -> i in
		let if i==nil then nil else strstr val "@" i -> j in
		let if j==nil then nil else XmppResFind XmppResStore (atoi strsub val i+2 j-i-2) -> roots in
		if !HttpsOn || roots==nil || (strlen val)<j+15 then (Secholn "https: no roots or not enabled"; wavstop; 0)
		else
		(
			set HttpsRoots=strcatlist roots;
			set HttpsNow=strsub val j+1 14;
			wavstarthttp (strsub val 0 i) #_cbWavError;
			0
		);
		1
	);;

'''

ST_OLD = "\t\tif !(XmppLivePlay val) then if !(XmppResPlay val) then (let rscfilterurl val -> music in wavstarthttp music #_cbWavError; 0);\n"
ST_NEW = "\t\tif !(HttpsPlay val) then if !(XmppLivePlay val) then if !(XmppResPlay val) then (let rscfilterurl val -> music in wavstarthttp music #_cbWavError; 0);\n"


def _read(path: Path) -> tuple[str, str]:
    raw = path.read_bytes().decode("latin-1")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), nl


def _write(path: Path, text: str, nl: str) -> None:
    path.write_bytes(text.replace("\n", nl).encode("latin-1"))


def _once(text: str, old: str, what: str, path: Path) -> None:
    if text.count(old) != 1:
        sys.exit(f"FAIL {path}: {what} not found once ({text.count(old)})")


def patch_http(path: Path) -> bool:
    text, nl = _read(path)
    if MARKER in text:
        print(f"[skip] {path}: https-play already applied")
        return False
    m = TYPE_RE.search(text)
    if not m or "sizeH" not in m.group(1):
        sys.exit(f"FAIL {path}: the Httpreq type is not http-cap's -- run patch_http_cap.py first")
    for old, what in ((EVENT_ANCHOR, "_httptcpevent"), (OPEN_OLD, "the opentcp of httpsendreq"),
                      (CUT_OLD, "httprequest's cuturl"), (REC_OLD, "httprequest's record")):
        _once(text, old, what, path)
    text = text[:m.start()] + m.group(1) + " httpsH];;\n" + text[m.end():]
    text = text.replace(EVENT_ANCHOR, EVENT_FUNS + EVENT_ANCHOR, 1)
    text = text.replace(OPEN_OLD, OPEN_NEW, 1)
    text = text.replace(CUT_OLD, CUT_NEW, 1)
    text = text.replace(REC_OLD, REC_NEW, 1)
    # httprequest's two proxy tests: an https request never goes to the proxy.
    i = text.index(CUT_NEW)
    j = text.index(REC_NEW, i)
    body = PROXY_RE.sub("if confGetProxy && !secure then", text[i:j])
    if body.count("confGetProxy && !secure") < 1:
        sys.exit(f"FAIL {path}: no proxy test in httprequest")
    text = text[:i] + body + text[j:]
    _write(path, text, nl)
    print(f"[ok]   {path}: https:// requests run over nab_tls session 1")
    return True


def patch_main(path: Path) -> bool:
    text, nl = _read(path)
    if MARKER in text:
        print(f"[skip] {path}: https-play already applied")
        return False
    _once(text, PLAY_ANCHOR, "XmppResPlay's comment (patch_res_in_session.py)", path)
    _once(text, ST_OLD, "the ST command's fetch (patch_live_in_session.py)", path)
    text = text.replace(PLAY_ANCHOR, PLAY_FUNS + PLAY_ANCHOR, 1)
    text = text.replace(ST_OLD, ST_NEW, 1)
    _write(path, text, nl)
    print(f"[ok]   {path}: `ST https://...` played by the rabbit itself")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_https_play.py <bootcode sources dir>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    patch_http(src / "http.mtl")
    patch_main(src / "main.mtl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
