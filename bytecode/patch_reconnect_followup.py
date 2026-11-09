#!/usr/bin/env python3
"""Follow-up to patch_reconnect.py, by pacifox (issue #23).

Taken as written rather than paraphrased, and credited: this is pacifox's
work, from https://github.com/ClaraVnk/nabaztag/issues/23. Run it after
patch_main.py, patch_dns.py and patch_reconnect.py on a stock Violet bootcode
tree:

    patch_reconnect_followup.py <bootcode>/sources [fix ...]

Five fixes. Only one of them is proven, and build-bytecode.sh applies that one
alone -- not because the others are wrong, but because they are not yet
measured and this bytecode is what a working rabbit runs:

  softreboot  PROVEN. With the server's host absent -- nothing answering ARP,
              the HA VM off -- the stock image prints "call to locate.jsp
              failed, rebooting" and soft-reboots every ten seconds, for ever:
              356 times in an hour of the emulator. Each soft reboot leaves
              the RT2501 asleep, so the FIRST one strands the rabbit until
              somebody unplugs it. With this fix: "locate.jsp: no xmpp_domain,
              retrying", and zero resets. Measured in firmware-arm/emulator,
              scenarios/vm/i23-arp.txt.
  tcpfix      NOT REACHED. Frees a socket whose SYN never left because ARP
              never resolved. The reading of the code looks right, but with
              softreboot applied so the reboot no longer hides it, four hours
              and 31 665 failed ARP attempts produced no ?OM Error. Correct,
              not urgent on that evidence.
  reco2       NOT REACHED. The reconnect deadline moves out of `run`, and the
              stop/ear-reset runs once per outage. Needs the double
              XmppSessionIsDisconnected -- </stream:stream> then TCPCLOSE --
              which the emulator does not yet produce.
  wavstall    NOT REACHED. Needs a sound starting while the mic records.
  micoff      Changes listening behaviour; pacifox marks it optional himself.

Every anchor must match exactly once; re-running is a no-op (marker check).
"""
import re
import sys

P = {}


def add(name, f, old, new, marker=None):
    P.setdefault(name, []).append((f, old, new, marker or new))


# ---- reco2: the reconnect deadline no longer lives in `run`; one stop/reset per outage;
#      one DNS request per attempt; clean per-attempt state; TCP only (no BOSH on this server)
add("reco2", "main.mtl", "const XmppSessionReadTimeout = 60;;",
    "const XmppSessionReadTimeout = 60;;\n"
    "var gReconnectAt=nil;;\t// reconnect deadline (time_ms), independent of `run`\n"
    "var gOffline=0;;\t\t// 1 between losing the session and reopening it")
add("reco2", "main.mtl", "(sClosed -> // la session est fermee...\n",
    "(sClosed -> // la session est fermee...\n"
    "\t\t\t\t\t\tif gReconnectAt!=nil && (time_ms-gReconnectAt)>0 then ( set gReconnectAt=nil; XmppGateReconnect xSession 0 );\n")
add("reco2", "main.mtl",
    "\t\t\t\t|(sClosed -> \n\t\t\t\t\tif time_ms > waitTime then (\n\t\t\t\t\t\tXmppGateReconnect xSession 0\n\t\t\t\t\t);\n\t\t\t\t\t0)\n",
    "\t\t\t\t|(sClosed -> 0)\t// reconnect is driven by XmppSessionIdle (gReconnectAt)\n")
add("reco2", "main.mtl", "@@ISDISC@@",
    "fun XmppSessionIsDisconnected xSession=\n"
    "\t// first call of an outage only: stop everything (one ear reset) and show the wait\n"
    "\tif !gOffline then\n"
    "\t(\n"
    "\t\tset gOffline=1;\n"
    "\t\tstopEverythingAndResetToIdle;\n"
    "\t\tsetRunState (xmppReconnectRun [0 xSession (if gSleepState then sleepRun else idleRun)])\n"
    "\t);\n"
    "\t// one deadline per outage (not re-drawn on every call); keep the 5 s + 0-30 s anti-stampede\n"
    "\tif gReconnectAt==nil then set gReconnectAt = time_ms + 5000 + 1000 * (random XMPP_MAX_NB_SECS_BEFORE_TRYING_TO_RECONNECT);\n"
    "\tset xSession.sStatus = sClosed;\n"
    "\tset xSession.sGate.pStatus = pClose;\n"
    "0;;\n", "set gOffline=1;")
add("reco2", "main.mtl", "0)|(sOpened -> set xSession.sNextRestart = -1;",
    "0)|(sOpened -> set xSession.sNextRestart = -1; set gOffline=0;")
add("reco2", "main.mtl",
    "fun XmppGateReconnect xSession displayLeds =\n\tXw \"XmppGateReconnect\";\n",
    "fun XmppGateReconnect xSession displayLeds =\n\tXw \"XmppGateReconnect\";\n"
    "\tset xSession.sDataToSend = [nil 0 0];\t// nothing from the old socket before the new <stream:stream>\n"
    "\tset xSession.sGate.pActualMode = 1;\t// TCP only: the add-on has no BOSH endpoint (/http-bind)\n"
    "\tset xSession.sGate.pMode = pTcp;\n"
    "\tset xSession.sGate.pBoshV = nil;\n")
add("reco2", "main.mtl", "set xSession.sGate.pTry = xSession.sGate.pTry - 1;",
    "set xSession.sGate.pTry = xSession.sGate.pTry - 1;\n\tif xSession.sGate.pTry < 0 then set xSession.sGate.pTry = 0;")
add("reco2", "main.mtl", "\twavstop;\n\tstopStream;\n\tearStartReset;\n\tset gItState = 0;",
    "\twavstop;\n\tstopStream;\n\tif recording then recstop;\t// a button recording would otherwise run (and grow lrec) forever\n"
    "\tstopmicstream;\t// the add-on re-sends RS after the new session if listening is on\n"
    "\tearStartReset;\n\tset gItState = 0;", "\tstopmicstream;\t// the add-on")
add("reco2", "main.mtl", "fun XmppTcpOpen ip port xSession =\n",
    "// one DNS request per attempt: no endless DNS chain (nor stacked chains) while the name does not resolve\n"
    "fun XmppTcpOpenIfIp ip port xSession =\n\tif ip != nil then XmppTcpOpen ip port xSession else 0;;\n\n"
    "fun XmppTcpOpen ip port xSession =\n", "fun XmppTcpOpenIfIp ip port xSession =")
add("reco2", "main.mtl",
    "\t\tdnsreq xSession.sDomain fixarg2 fixarg3 #XmppTcpOpen xSession XmppServerPort;\n",
    "\t\tdnsreq xSession.sDomain fixarg2 fixarg3 #XmppTcpOpenIfIp xSession XmppServerPort;\n")

# ---- tcpfix: a socket whose ARP never resolved (SYN never sent) is freed instead of living forever
add("tcpfix", "tcp.mtl", "fun closetcp t=\n\tif t.stateT!=STEST then 0\n",
    "fun closetcp t=\n\tif t.stateT==STSYN then (remtcp t; 0)\n\telse if t.stateT!=STEST then 0\n")
add("tcpfix", "tcp.mtl", "\t\telse set t.retryT=0\n\t);\n\t0;;",
    "\t\telse set t.retryT=0\n\t)\n"
    "\telse if t.stateT==STSYN then\n\t(\n"
    "\t\t// SYN never sent (no ARP answer): free the socket after ~10 s and report TCPCLOSE\n"
    "\t\tset t.retryT = 1 + (if t.retryT==nil then 0 else t.retryT);\n"
    "\t\tif t.retryT>10 then\n\t\t(\n\t\t\tremtcp t;\n\t\t\tcall t.cbT [t TCPCLOSE nil];\n\t\t\tnil\n\t\t)\n\t);\n\t0;;")

# ---- softreboot: no `reboot` left on the locate.jsp and code-9 paths
add("softreboot", "main.mtl", "\t\t\t\t\t// reboot\n\t\t\t\t\treboot 0x0407FE58 0x13fb6754;\n",
    "\t\t\t\t\t// was: reboot (a soft reboot leaves the radio asleep) -> gentle reset\n\t\t\t\t\tstopEverythingAndResetToIdle;\n")
add("softreboot", "main.mtl",
    "\t\t\t\t\tSecholn \"call to locate.jsp failed, rebooting\";\n\t\t\t\t\treboot 0x0407FE58 0x13fb6754;\n",
    "\t\t\t\t\tSecholn \"locate.jsp: no xmpp_domain, retrying\";\n")
add("softreboot", "main.mtl", "\t\tset run = xmppLoginRun;",
    "\t\tset run = if XmppGlobalSession != nil then xmppLoginRun else configstartRun;")
add("softreboot", "main.mtl",
    "\t\tSecholn \"call to locate.jsp failed, rebooting\";\n\t\treboot 0x0407FE58 0x13fb6754;\n",
    "\t\tSecholn \"call to locate.jsp failed, retrying\";\n\t\tset run=configstartRun;\n")
add("softreboot", "main.mtl",
    "if netState==RT2501_S_CONNECTED && wavrunning==0 then (",
    "if netState==RT2501_S_CONNECTED && wavrunning==0 && (gConfigStart==0 || (time_ms-gConfigStart)>3000) then (")

# ---- wavstall: a sound the C side refused to start (it was recording) no longer blocks forever
add("wavstall", "audiolib.mtl",
    "\t\tif (wav_http_cb != nil) then call wav_http_cb []\n\t);\n\t0;;",
    "\t\tif (wav_http_cb != nil) then call wav_http_cb []\n\t);\n"
    "\t// playback never started (play_start refuses while recording): wav_buffering stays 1 and\n"
    "\t// wavrunning never returns 0 -> give up after WAV_NET_TIMEOUT instead of blocking the program\n"
    "\tif (wav_state==WAV_EOF && wav_buffering && (time_ms - wav_buffering_since > WAV_NET_TIMEOUT)) then wavstop;\n"
    "\t0;;")

# ---- micoff (optional, changes listening behaviour): stop the mic stream before any playback
add("micoff", "audiolib.mtl", "fun _wavstartnow =\n",
    "fun _wavstartnow =\n\tif g_stream_on then stopmicstream;\t// speaker and mic never together (VS1003)\n")


def main():
    d = sys.argv[1].rstrip("/")
    names = sys.argv[2:] or ["reco2", "tcpfix", "softreboot", "wavstall"]
    cache = {}
    for n in names:
        for (f, old, new, marker) in P[n]:
            s = cache.setdefault(f, open(d + "/" + f, encoding="latin-1", newline="").read())
            if marker in s:
                print("[%s] %s: already applied" % (n, f))
                continue
            if old == "@@ISDISC@@":
                m = re.compile(r"fun XmppSessionIsDisconnected xSession=\n.*?\n0;;\n", re.S)
                assert len(m.findall(s)) == 1, "ISDISC"
                s = m.sub(lambda _: new, s, count=1)
            else:
                assert s.count(old) == 1, "[%s] %s: anchor found %d times\n%s" % (n, f, s.count(old), old)
                s = s.replace(old, new, 1)
            cache[f] = s
            print("[%s] %s: ok" % (n, f))
    for f, s in cache.items():
        open(d + "/" + f, "w", encoding="latin-1", newline="").write(s)


if __name__ == "__main__":
    main()
