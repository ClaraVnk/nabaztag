#!/usr/bin/env python3
"""A radio or a podcast comes down the TLS session, and plays as it arrives.

patch_res_in_session.py moved what a rabbit says into its session: the whole
sound, then `ST res:N`. A radio has no end and a podcast is tens of megabytes,
so those stayed on `ST http://<warren>/pod/<token>`: plain HTTP, the stock
player, and anyone on the path hearing what the house listens to.

A stream cannot be sent like a sound. The rabbit must never hold more than a
few seconds of it, the warren must never send more than the rabbit can take,
and everything else the session carries -- commands, the keepalive, the
state query -- has to keep flowing while it plays. So the rabbit pulls:

    warren -> rabbit   the program `ST live:N`
    rabbit -> warren   <live xmlns='violet:nabaztag:live' id='N' upto='B'/>
    warren -> rabbit   <res xmlns='violet:nabaztag:res' id='N' len='L' live='1'/>
                       then L raw bytes, again and again, up to byte B of
                       the stream and no further
    ...
    warren -> rabbit   <res ... id='N' len='0' live='end'/>   the stream is over
                       <res ... id='N' len='0' live='cut'/>   stop now
    rabbit -> warren   <live xmlns='violet:nabaztag:live' id='N' stop='1'/>

`ST live:N` starts the stock player (audiolib.mtl) with no HTTP request
behind it and asks for the first XMPP_LIVE_WINDOW bytes. `upto` is a
position in the stream, not a count: what the rabbit has received plus the
room its fifo has. Said twice it buys nothing more, and a request that was
lost is made good by the next one -- the rabbit repeats it after three
seconds without a byte. Each chunk is an announce in
its own record and its bytes, which XmppResSift already keeps away from the
XML parser; a chunk of the stream being played goes straight onto the
player's fifo, any other is dropped. The sound starts once XMPP_LIVE_START
bytes are in hand (the stock HTTP player waits for 80 000) and again after
the fifo ran dry. Five times a second XmppLiveTick looks at the fifo: when
XMPP_LIVE_GRANT more bytes of the window are free it moves `upto`, so what is
in the rabbit -- fifo plus what it asked for -- never exceeds XMPP_LIVE_WINDOW
(49 152 bytes: three seconds at 128 kbit/s). The warren sends nothing it was
not asked for, which is the whole of the flow control: a rabbit that plays
slowly asks slowly, and one that stopped asks for nothing.

Stopping. The warren's `cut` empties the player at once (the owner pressed
stop, or sent something else to play); `end` lets what is buffered play
out. On the rabbit's side every stop there already is -- the head button,
a lost session's stopEverythingAndResetToIdle, another sound -- goes through
wavstop, which forgets the stream; the tick notices and tells the warren
`stop`, so it closes its upstream instead of waiting for requests that
will not come. Twenty seconds with a request unanswered and the rabbit
stops by itself.

A chunk counts as hearing from the warren (sLastReadTime): while a stream
runs the warren has no reason to send its keepalive, and without this the
150 s liveness timer of patch_liveness.py would close a session that was
delivering audio the whole time.

audiolib.mtl gets two variables for this: wav_live, the stream being
played, cleared by wavstop; and wav_startsize in place of the constant
WAV_BUFFER_STARTSIZE in the player's callback, reset by wavstop, so the
HTTP player is byte for byte what it was.

Its own literal, "xmpp-live:0", flipped to 1 under the HMAC by a warren
that relays streams this way. As compiled `ST live:N` plays nothing, no
announce is taken for a stream, and every radio is the URL it always was.

Applied after patch_ota_in_session.py (build-bytecode.sh does). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* live-in-session */"

RES_MARKER = "/* res-in-session */"
OTA_MARKER = "/* ota-in-session */"

# --- audiolib.mtl ----------------------------------------------------------

AUDIO_VARS_OLD = "var wav_curplayedbytes=0;;\n"
AUDIO_VARS_NEW = ('var wav_curplayedbytes=0;;\n'
                  '/* live-in-session */\n'
                  '// See bytecode/patch_live_in_session.py.\n'
                  'var wav_live;;\t// the stream being played from the session, or nil\n'
                  'var wav_startsize=80000;;\t// bytes in the fifo before the sound (re)starts\n')
AUDIO_STOP_OLD = 'fun wavstop =\n\tAudiowln "/wavstop/";\n'
AUDIO_STOP_NEW = ('fun wavstop =\n\tAudiowln "/wavstop/";\n'
                  '\tset wav_live=nil;\n'
                  '\tset wav_startsize=WAV_BUFFER_STARTSIZE;\n')
AUDIO_CB_OLD = "(slistlen wav_fifo)>=WAV_BUFFER_STARTSIZE) then\n"
AUDIO_CB_NEW = "(slistlen wav_fifo)>=wav_startsize) then\n"

# --- main.mtl: the announce ------------------------------------------------

BEGIN_ANCHOR = "fun XmppResBegin r=\n"
SIFT_FUNS = '''/* live-in-session */
// A stream the warren relays inside the session, pulled by the rabbit.
// See bytecode/patch_live_in_session.py. The warren flips the digit to 1
// before signing when it relays radios and podcasts this way.
var XmppLivePolicy="xmpp-live:0";;
fun XmppLiveInSession= (strget XmppLivePolicy 10)==49;;
var XmppLiveOn=0;;	// the announce being read is a stream's
proto XmppLiveChunk 1;;	// defined with the player, after audiolib.mtl
proto XmppLiveMark 2;;

'''

KEEP_OLD = ("\t\tset XmppFwOn=XmppOtaInSession && (strstr r \" fw='1'\" 0)!=nil;\n"
            "\t\tset XmppResKeep=!XmppFwOn && id!=nil && XmppResLen>0 && XmppResLen<=XMPP_RES_BUDGET;\n")
KEEP_NEW = ("\t\tset XmppLiveOn=XmppLiveInSession && ((strstr r \" live='\" 0)!=nil);\n"
            "\t\tset XmppFwOn=(!XmppLiveOn) && XmppOtaInSession && (strstr r \" fw='1'\" 0)!=nil;\n"
            "\t\tset XmppResKeep=(!XmppLiveOn) && !XmppFwOn && id!=nil && XmppResLen>0 && XmppResLen<=XMPP_RES_BUDGET;\n"
            "\t\tif XmppLiveOn && (XmppResLen==0) then XmppLiveMark r id;\n")
ECHO_OLD = '\t\tSecho "res: announced "; Iecho XmppResLen; Secho " bytes at "; Iecholn time_ms;\n'
ECHO_NEW = '\t\tif !XmppLiveOn then (Secho "res: announced "; Iecho XmppResLen; Secho " bytes at "; Iecholn time_ms; 0);\n'
PART_OLD = "\t\tif XmppFwOn then XmppFwChunk r\n"
PART_NEW = ("\t\tif XmppLiveOn then XmppLiveChunk r\n"
            "\t\telse if XmppFwOn then XmppFwChunk r\n")

# --- main.mtl: the player --------------------------------------------------

PLAY_ANCHOR = "// res-in-session: `ST res:N` plays resource N, received inside the session\n"
PLAY_FUNS = '''/* live-in-session: the player */
const XMPP_LIVE_WINDOW=49152;;	// bytes in the rabbit: the fifo and what it asked for
const XMPP_LIVE_START=16384;;	// bytes in the fifo before the sound starts, or starts again
const XMPP_LIVE_GRANT=16384;;	// free room worth asking the warren for
const XMPP_LIVE_TICK=200;;	// ms between two looks at the fifo
const XMPP_LIVE_AGAIN=3000;;	// ms without a byte before a request is repeated
const XMPP_LIVE_SILENCE=20000;;	// ms a request may go unanswered
var XmppLiveId;;	// the stream the warren is asked for, or nil
var XmppLiveGot=0;;	// bytes of it received
var XmppLiveUpto=0;;	// the byte the warren was told it may send up to
var XmppLiveTime=0;;	// the last look at the fifo
var XmppLiveHeard=0;;	// the last byte, or the request it answers
var XmppLiveAsked=0;;	// the last request

fun XmppLiveSay id what=
	if (XmppGlobalSession!=nil) && XmppTlsOn && ((tlsState)==2) then
		(XmppTcpSend XmppGlobalSession strcatlist "<live xmlns='violet:nabaztag:live' id='"::(itoa id)::"' "::what::"/>"::nil; 0);
	0;;

// Tell the warren how far it may send: what was received and what the fifo
// has room for. The same figure again is a reminder, and costs nothing.
fun XmppLiveAsk upto=
	if XmppLiveUpto<=XmppLiveGot then set XmppLiveHeard=time_ms;	// nothing was owed: the wait starts here
	set XmppLiveUpto=upto;
	set XmppLiveAsked=time_ms;
	XmppLiveSay XmppLiveId strcatlist "upto='"::(itoa upto)::"'"::nil;;

// A record of the stream announced as XmppResId: onto the fifo when it is
// the one being played, dropped otherwise (a stream stopped here whose last
// chunks were already on their way).
fun XmppLiveChunk r=
	if XmppGlobalSession!=nil then set XmppGlobalSession.sLastReadTime=time;
	if (XmppResId!=nil) && (XmppResId==wav_live) && (wav_state==WAV_RUN) then
	let slistlen wav_fifo -> n in
	(
		set XmppLiveHeard=time_ms;
		set XmppLiveGot=XmppLiveGot+(strlen r);
		// never more than was asked for, whatever the warren sends
		if n<(2*XMPP_LIVE_WINDOW) then
		(
			set wav_fifo=conc wav_fifo r::nil;
			if (wav_index==nil) && ((n+(strlen r))>=XMPP_LIVE_START) then
				(Secho "live: playing at "; Iecholn time_ms; _wavstartnow; 0);
			0
		);
		0
	);
	0;;

// An announce of no bytes: the stream is over (what is buffered plays out),
// or cut (silence now).
fun XmppLiveMark r id=
	if (id!=nil) && (id==wav_live) then
	let (strstr r " live='cut'" 0)!=nil -> cut in
	(
		Secho (if cut then "live: cut at " else "live: end at "); Iecholn time_ms;
		set XmppLiveId=nil;
		if cut then (wavstop; 0)
		else if wav_state==WAV_RUN then
		(
			set wav_state=WAV_EOF;
			if wav_fifo==nil then (wavstop; 0)
			else
			(
				set wav_fifo=conc wav_fifo (wavgetzeros)::nil;
				if wav_index==nil then (_wavstartnow; 0);
				0
			)
		)
		else 0
	);
	0;;

// `ST live:N`: 1 when the value was such a name. The player starts empty
// and the first request goes out; a rabbit whose session cannot carry it
// plays nothing and the command ends.
fun XmppLivePlay val=
	if val==nil || (strlen val)<6 || strcmp (strsub val 0 5) "live:" then 0
	else let atoi strsub val 5 nil -> id in
	(
		wavstop;
		Secho "live: ST "; Secho val; Secho " at "; Iecholn time_ms;
		if XmppLiveInSession && XmppTlsOn && ((tlsState)==2) then
		(
			set wav_http_cb=nil;
			set wav_end_timeout=WAV_END_TIMEOUT;
			set wav_fifo=nil;
			set wav_state=WAV_RUN;
			set wav_index=nil;
			set wav_buffering=1;
			set wav_buffering_since=time_ms;
			set wav_lasttime=nil;
			set wav_lastnet=time_ms;
			set wav_http=nil;
			set wav_startsize=XMPP_LIVE_START;
			set wav_live=id;
			set XmppLiveId=id;
			set XmppLiveGot=0;
			set XmppLiveUpto=0;
			set XmppLiveTime=time_ms;
			XmppLiveAsk XMPP_LIVE_WINDOW;
			0
		);
		1
	);;

// Called from the main loop. A stream that wavstop ended -- the button, a
// lost session, another sound -- is reported to the warren once; one that
// plays is asked for what the fifo has room for.
fun XmppLiveTick=
	if XmppLiveId!=nil then
	(
		if wav_live!=XmppLiveId then
		(
			Secho "live: stopped here at "; Iecholn time_ms;
			XmppLiveSay XmppLiveId "stop='1'";
			set XmppLiveId=nil;
			0
		)
		else if (time_ms-XmppLiveTime)>=XMPP_LIVE_TICK then
		(
			set XmppLiveTime=time_ms;
			let XmppLiveUpto>XmppLiveGot -> owed in
			if owed && ((time_ms-XmppLiveHeard)>XMPP_LIVE_SILENCE) then
				(Secholn "live: nothing came, stopping"; wavstop; 0)
			else let XmppLiveGot+XMPP_LIVE_WINDOW-(slistlen wav_fifo) -> upto in
			if (upto-XmppLiveUpto)>=XMPP_LIVE_GRANT then (XmppLiveAsk upto; 0)
			else if owed && ((time_ms-XmppLiveHeard)>XMPP_LIVE_AGAIN) && ((time_ms-XmppLiveAsked)>XMPP_LIVE_AGAIN) then
				(Secho "live: asking again at "; Iecholn time_ms; XmppLiveAsk XmppLiveUpto; 0)
			else 0
		)
		else 0
	);
	0;;

'''

ST_OLD = "\t\tif !(XmppResPlay val) then (let rscfilterurl val -> music in wavstarthttp music #_cbWavError; 0);\n"
ST_NEW = "\t\tif !(XmppLivePlay val) then if !(XmppResPlay val) then (let rscfilterurl val -> music in wavstarthttp music #_cbWavError; 0);\n"

LOOP_OLD = "\twavtime;\n\n\tupdatevol;\n"
LOOP_NEW = "\twavtime;\n\tXmppLiveTick;\n\n\tupdatevol;\n"


def _read(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"   # Violet's sources carry 8-bit comments


def patch(path: Path) -> bool:
    audio_path = path.parent / "audiolib.mtl"
    text, encoding = _read(path)
    if MARKER in text:
        print(f"[skip] {path}: streams already come down the session")
        return False
    if RES_MARKER not in text or OTA_MARKER not in text:
        sys.exit(f"FAIL {path}: run patch_res_in_session.py and patch_ota_in_session.py first")
    if not audio_path.is_file():
        sys.exit(f"FAIL {audio_path}: missing; this is not the Violet tree")
    audio, audio_encoding = _read(audio_path)
    if MARKER in audio:
        sys.exit(f"FAIL {audio_path}: patched, and {path.name} is not -- start from a clean tree")
    for what, src, needle in (("XmppResBegin", text, BEGIN_ANCHOR),
                              ("XmppResBegin's keep test", text, KEEP_OLD),
                              ("XmppResBegin's trace", text, ECHO_OLD),
                              ("XmppResSift's append", text, PART_OLD),
                              ("XmppResPlay's comment", text, PLAY_ANCHOR),
                              ("the ST command's fetch", text, ST_OLD),
                              ("wavtime in the main loop", text, LOOP_OLD),
                              ("wav_curplayedbytes", audio, AUDIO_VARS_OLD),
                              ("wavstop", audio, AUDIO_STOP_OLD),
                              ("the player's start threshold", audio, AUDIO_CB_OLD)):
        if src.count(needle) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {src.count(needle)}")
    for name in ("_wavstartnow", "wavgetzeros"):
        if f"fun {name}=" not in audio and f"fun {name} =" not in audio:
            sys.exit(f"FAIL {audio_path}: {name} is missing; this is not the Violet audiolib")

    audio = audio.replace(AUDIO_VARS_OLD, AUDIO_VARS_NEW, 1)
    audio = audio.replace(AUDIO_STOP_OLD, AUDIO_STOP_NEW, 1)
    audio = audio.replace(AUDIO_CB_OLD, AUDIO_CB_NEW, 1)

    text = text.replace(BEGIN_ANCHOR, SIFT_FUNS + BEGIN_ANCHOR, 1)
    text = text.replace(KEEP_OLD, KEEP_NEW, 1)
    text = text.replace(ECHO_OLD, ECHO_NEW, 1)
    text = text.replace(PART_OLD, PART_NEW, 1)
    text = text.replace(PLAY_ANCHOR, PLAY_FUNS + PLAY_ANCHOR, 1)
    text = text.replace(ST_OLD, ST_NEW, 1)
    text = text.replace(LOOP_OLD, LOOP_NEW, 1)

    for s in (AUDIO_VARS_NEW, AUDIO_STOP_NEW, AUDIO_CB_NEW, SIFT_FUNS, KEEP_NEW, ECHO_NEW,
              PART_NEW, PLAY_FUNS, ST_NEW, LOOP_NEW):
        assert s.isascii(), "what we insert must be ASCII: the sources are latin-1"
    audio_path.write_bytes(audio.encode(audio_encoding))
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: `ST live:N` plays a stream pulled through the TLS session")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/main.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
