#!/usr/bin/env python3
"""TLS 1.3 (PSK) on the XMPP connection, in the runtime bytecode.

The rabbit's TCP/IP and its XMPP client are Metal (tcp.mtl, main.mtl), so
TLS cannot slide under the socket: it goes where the XMPP gate reads and
writes it, and nowhere else. The record layer, the keys and the adoption
key they derive from live in C, behind five opcodes the firmware adds in
mode lean-wpa3-tls (firmware-arm/apply-mods.py, step tls_psk):

    tlsStart id  -> ClientHello      tlsIn bytes -> plaintext, or nil
    tlsOut data  -> records          tlsOut nil  -> handshake output
    tlsState     -> -1 no key, 0 idle, 1 handshake, 2 ready, 3 failed, 4 closed
    tlsClose     -> close_notify

When the rabbit uses TLS: locate.jsp answers `xmpp_tls <port>` for a rabbit
whose owner confirmed its adoption key (the Terrier does, nothing older
does), and tlsState says the bootloader handed a key over (-1 says it did
not: a rabbit reflashed, or never adopted this way). Both, and the gate
opens the TLS port with the handshake first; either missing, and it opens
the plain port exactly as before. There is no fallback from a failed
handshake to plain: the gate reconnects, over TLS again. Whoever can break
the handshake can already cut the cable, and the warren decides per rabbit
what it serves.

Three seams in main.mtl, each the smallest that works:

  * XmppTcpOpen / XmppTcpReOpen: the port passes through XmppTlsSelect.
  * XmppTcpSend becomes XmppTcpSendRaw; the new XmppTcpSend encrypts first
    when TLS is on (the queueing and partial writes below it are unchanged:
    records are bytes like any other).
  * XmppTcpRead becomes XmppTcpReadPlain; the new XmppTcpRead runs the
    handshake on TCPWRITE/TCPREAD and hands the plain code decrypted
    bytes, and a TCPWRITE once the handshake is done so the stream opens
    as it always did.

Idempotent: re-running on a patched tree is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* xmpp-over-tls */"

VARS_OLD = "var XmppServerPort = 5222;;"
VARS_NEW = """var XmppServerPort = 5222;;
/* xmpp-over-tls */
var XmppTlsPort = 0;;	// from locate.jsp "xmpp_tls <port>"; 0: the warren offers none
var XmppTlsOn = 0;;	// the current socket runs TLS
var XmppTlsSock;;	// the socket whose handshake was started
proto XmppTlsSelect 1;;	// XmppTcpOpen calls it; defined after XmppTcpRead"""

LOCATE_OLD = '\t\t\tset broadcasturl=listswitchstr conf "broad";\n'
LOCATE_NEW = '''\t\t\tset broadcasturl=listswitchstr conf "broad";
\t\t\t// xmpp-over-tls: the warren names a TLS port for a rabbit whose owner
\t\t\t// confirmed its adoption key; whether this rabbit holds the key is
\t\t\t// the firmware's to say (tlsState -1: it does not), in XmppTlsSelect.
\t\t\tset XmppTlsPort = let listswitchstr conf "xmpp_tls" -> p in if p==nil then 0 else atoi p;
'''

OPEN_OLD = "(useparamip ip) port fixarg4 #XmppTcpRead xSession"
OPEN_NEW = "(useparamip ip) (XmppTlsSelect port) fixarg4 #XmppTcpRead xSession"

SEND_OLD = "fun XmppTcpSend xSession Data =\n"
SEND_NEW = "fun XmppTcpSendRaw xSession Data =\n"

READ_OLD = "fun XmppTcpRead t val msg xSession =\n"
READ_NEW = "fun XmppTcpReadPlain t val msg xSession =\n"

# Inserted before XmppTcpIdle, after the renamed XmppTcpRead.
IDLE_ANCHOR = "/*\n * Idle pour le TCP\n"
TLS_FUNS = '''/* xmpp-over-tls ----------------------------------------------------------
 * TLS 1.3 with the adoption key, between the socket and the XMPP gate.
 * See firmware/patch_tls.py. */

// The port to open: the warren's TLS port when it offered one and this
// rabbit holds a key, the one asked for otherwise.
fun XmppTlsSelect port=
	set XmppTlsSock=nil;
	if XmppTlsPort>0 && (tlsState)>=0 then (set XmppTlsOn=1; XmppTlsPort)
	else (set XmppTlsOn=0; port);;

// Encrypt, then queue as before. Nothing may be sent before the handshake
// is over; nil here closes the stream, which reconnects.
fun XmppTcpSend xSession Data=
	if !XmppTlsOn then XmppTcpSendRaw xSession Data
	else if (tlsState)!=2 then nil
	else let tlsOut Data -> rec in
	if rec==nil then nil else XmppTcpSendRaw xSession rec;;

// Every decrypted record the bytes just fed complete, in order.
fun XmppTlsDrain data=
	let tlsIn data -> p in
	if p==nil then nil
	else p::XmppTlsDrain nil;;

// Handshake output and alerts waiting to be written.
fun XmppTlsFlush xSession=
	let tlsOut nil -> rec in
	if rec!=nil then XmppTcpSendRaw xSession rec else 0;;

fun XmppTcpRead t val msg xSession =
	if !XmppTlsOn || xSession.sGate.pTcpV==nil || xSession.sGate.pTcpV.xSocket!=t then XmppTcpReadPlain t val msg xSession
	else if val==TCPWRITE then
	(
		set xSession.sLastIOTime = time;	// the gate's 2 s connect timeout counts from here
		if XmppTlsSock!=t then
		(
			// The socket just opened: ClientHello first, the stream later.
			set XmppTlsSock=t;
			let tlsStart (webmac netMac) -> hello in
			if hello==nil then XmppSessionIsDisconnected xSession
			else XmppTcpSendRaw xSession hello;
			0
		)
		else if (tlsState)==2 then XmppTcpReadPlain t val msg xSession
		else (XmppTcpActuallySendData xSession; 0);
		0
	)
	else if val==TCPREAD then
	(
		set xSession.sLastIOTime = time;
		let strcatlist XmppTlsDrain msg -> plain in
		(
			XmppTlsFlush xSession;
			let tlsState -> st in
			if st==2 then
			(
				// The first time through here with the handshake done, the
				// gate still waits for its socket: give it the TCPWRITE it
				// would have had, and it opens the stream as always.
				if xSession.sGate.pStatus==pOpening then XmppTcpReadPlain t TCPWRITE nil xSession;
				if plain!=nil && (strlen plain)>0 then XmppTcpReadPlain t TCPREAD plain xSession;
				0
			)
			else if st==1 then 0
			else (XmppGateStreamClosed xSession pTcp; 0)
		);
		0
	)
	else XmppTcpReadPlain t val msg xSession;;

'''


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")      # main.mtl carries 8-bit comments
        encoding = "latin-1"

    if MARKER in text:
        print(f"[skip] {path}: XMPP already runs over TLS when it can")
        return False
    for what, needle, count in (("XmppServerPort", VARS_OLD, 1),
                                ("the locate.jsp reader", LOCATE_OLD, 1),
                                ("opentcp in XmppTcpOpen/ReOpen", OPEN_OLD, 2),
                                ("XmppTcpSend", SEND_OLD, 1),
                                ("XmppTcpRead", READ_OLD, 1),
                                ("XmppTcpIdle's comment", IDLE_ANCHOR, 1)):
        if text.count(needle) != count:
            sys.exit(f"FAIL {path}: expected {count} of {what}, found {text.count(needle)}")
    for name in ("XmppSessionIsDisconnected", "XmppGateStreamClosed", "XmppTcpActuallySendData"):
        if f"fun {name}" not in text and f"proto {name}" not in text:
            sys.exit(f"FAIL {path}: {name} is missing; this is not the Violet main.mtl")

    text = text.replace(VARS_OLD, VARS_NEW, 1)
    text = text.replace(LOCATE_OLD, LOCATE_NEW, 1)
    text = text.replace(OPEN_OLD, OPEN_NEW)
    text = text.replace(SEND_OLD, SEND_NEW, 1)
    text = text.replace(READ_OLD, READ_NEW, 1)
    text = text.replace(IDLE_ANCHOR, TLS_FUNS + IDLE_ANCHOR, 1)

    for s in (VARS_NEW, LOCATE_NEW, TLS_FUNS):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: XMPP over TLS 1.3 when the rabbit has a key and the warren offers it")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
