#!/usr/bin/env python3
"""Minimal Nabaztag (Violet protocol) server — Phase 1.

A clean, dependency-free replacement for the dead Violet/OpenJabNab servers:
  * HTTP  : serves the original bootcode (/vl/bc.jsp) + /vl/locate.jsp
  * XMPP  : accepts the rabbit's connection with the documented SASL
            "success-bypass", keeps it alive, and can push violet:packet
            commands. Everything the rabbit sends is logged (RE platform).
  * API   : a small HTTP control API for Home Assistant (ears/led/sound/raw).

Protocol references: nabaztag.com/doc (InteractionServeurs, APIV2) and the
OpenJabNab source. Packet framing: 0x7F | type | len(3, big-endian) | data | 0xFF
sent base64-encoded inside <message><packet xmlns='violet:packet'>…</packet>.
"""
import base64
import codecs
import contextlib
import functools
import hashlib
import io
import secrets
import signal
import ssl
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
import threading
import urllib.request
from collections import OrderedDict
import time
import itertools
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import adoption

# --------------------------------------------------------------------------- #
# Configuration (from /data/options.json or env)
# --------------------------------------------------------------------------- #
def _load_options():
    try:
        with open("/data/options.json", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}

_OPTS = _load_options()
SERVER_ADDRESS = os.environ.get("SERVER_ADDRESS") or _OPTS.get("server_address") or "192.0.2.10"
LOG_LEVEL = (os.environ.get("LOG_LEVEL") or _OPTS.get("log_level") or "Info").upper()
HTTP_PORT = int(os.environ.get("HTTP_PORT", _OPTS.get("http_port", 80)))
XMPP_PORT = int(os.environ.get("XMPP_PORT", _OPTS.get("xmpp_port", 5222)))
API_PORT = int(os.environ.get("API_PORT", _OPTS.get("api_port", 8080)))
# TLS 1.3 with the adoption key, for Naboot rabbits whose owner confirmed the
# colours (adoption.py). 5223 is the port the rabbit falls back to when
# locate.jsp names none, so it is the one to keep.
XMPP_TLS_PORT = int(os.environ.get("XMPP_TLS_PORT", _OPTS.get("xmpp_tls_port", 5223)))
# Where the adoption keys live. Losing this file locks every confirmed rabbit
# out until its configuration is reset (head button at power-up, /reset).
ADOPTION_KEYS_FILE = os.environ.get("ADOPTION_KEYS_FILE", "/data/adoption-keys.json")
# Nothing heard for this long from a bound rabbit's side of the wire: send it
# something. The runtime with bytecode/patch_liveness.py closes and reopens
# its session after 150 s without reading anything (ear recalibration and
# all), so the server speaks first, as Le Terrier does.
KEEPALIVE_S = float(os.environ.get("KEEPALIVE_S") or 45)


def _resolve_bootcode():
    """First bytecode that actually exists, in order of authority.

    /data wins over the bundled copy so a hand-built bytecode dropped in the
    add-on's persistent storage keeps overriding the image, which is how this
    has always worked. The copy next to this file comes next: it is the one
    committed in the repo and bundled in the image, so a fresh install — or a
    plain `python3 server.py` from a checkout — serves something instead of
    500ing (issue #2). bytecode/bootcode_hybrid.bin is last: the local output
    of bytecode/build-bytecode.sh, for whoever rebuilds the bytecode.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    explicit = os.environ.get("BOOTCODE_FILE")
    if explicit:
        return explicit
    for cand in ("/data/bootcode.hybrid",
                 os.path.join(here, "bootcode.hybrid"),
                 os.path.join(here, os.pardir, "bytecode", "bootcode_hybrid.bin")):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    return "/data/bootcode.hybrid"

BOOTCODE_FILE = _resolve_bootcode()


def _resolve_bootcode_tls():
    """The runtime for rabbits that ran the adoption exchange: it carries the
    TLS client (bytecode/patch_tls.py) and calls opcodes that only Naboot's
    firmware has, so it goes to no other rabbit -- a stock one would stop on
    the first of them. Same order of authority as the hybrid."""
    here = os.path.dirname(os.path.abspath(__file__))
    explicit = os.environ.get("BOOTCODE_TLS_FILE")
    if explicit:
        return explicit
    for cand in ("/data/bootcode.tls", os.path.join(here, "bootcode.tls")):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    return ""


BOOTCODE_TLS_FILE = _resolve_bootcode_tls()


def _resolve_bootcode_https():
    """The runtime for adopted rabbits on Naboot 1.2.0.0 and later, Le
    Terrier's baseline-https: the TLS runtime plus DHCP and DNS replies
    checked, HTTP answers bounded, and https:// streams opened by the rabbit
    itself. It calls the https opcodes, which older images lack."""
    here = os.path.dirname(os.path.abspath(__file__))
    explicit = os.environ.get("BOOTCODE_HTTPS_FILE")
    if explicit:
        return explicit
    for cand in ("/data/bootcode.https", os.path.join(here, "bootcode.https")):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    return ""


BOOTCODE_HTTPS_FILE = _resolve_bootcode_https()
# Where /api/ota stages signed .sim files. The rabbit fetches them at the
# matching /ota/<basename> URL when the server sends a FW program command.
OTA_DIR = os.environ.get("OTA_DIR", "/data/ota")
# Where /api/ota?path= may read a .sim from.
OTA_SOURCES = (OTA_DIR, "/share")
# Where Nabi should stream its mic (the hybrid bytecode's RS command). The rabbit
# routes UDP to this host:4000; we resolve the advertised server address to an IP.
MIC_UDP_PORT = int(os.environ.get("MIC_UDP_PORT", _OPTS.get("mic_udp_port", 4000)))
# Phase 3B wake word: when the mic is streaming, transcribe rolling windows and,
# if the wake word is heard, treat the rest as a command for the conversation
# agent. auto_listen starts the stream automatically once Nabi is idle.
WAKE_WORD = (os.environ.get("WAKE_WORD") or _OPTS.get("wake_word") or "nabi").lower()
WAKE_WINDOW_S = float(os.environ.get("WAKE_WINDOW_S") or _OPTS.get("wake_window_s") or 3)
# Skip STT on near-silent wake windows (energy gate). It's the peak RMS over short
# frames, so a brief "nabi" in an otherwise-quiet window still passes. 8 kHz/16-bit
# silence is well under this; speech is >1000. On decode error the gate is bypassed.
WAKE_VAD_RMS = int(os.environ.get("WAKE_VAD_RMS") or _OPTS.get("wake_vad_rms") or 300)
# Match the wake word (or a close whisper mistranscription) as a WHOLE word, so
# stray substrings like "nab" inside other words don't false-trigger.
_WAKE_PAT = re.compile(r"\b(?:" + "|".join(sorted(
    {re.escape(WAKE_WORD), "nabi", "naby", "navi", "nabie", "nabil"},
    key=len, reverse=True)) + r")\b")
# Max seconds to keep capturing the command after the wake word (the capture
# stops early once you pause — see wake_loop).
WAKE_CAPTURE_MAX_S = float(os.environ.get("WAKE_CAPTURE_MAX_S") or _OPTS.get("wake_capture_max_s") or 4.0)
AUTO_LISTEN = str(os.environ.get("AUTO_LISTEN") or _OPTS.get("auto_listen") or "").lower() in ("1", "true", "yes", "on")
# Optional: play this jingle once the rabbit comes online (a little "hello").
# Empty = none. Set to a jingle name (see /api/jingle) to enable.
CONNECT_JINGLE = (os.environ.get("CONNECT_JINGLE") or _OPTS.get("connect_jingle") or "").strip()
if CONNECT_JINGLE == "none":
    CONNECT_JINGLE = ""
# Optional short "got it" chime played after the wake word (once the mic has
# stopped), filling the gap while the agent composes its reply. "" = off.
WAKE_CHIME = (os.environ.get("WAKE_CHIME") or _OPTS.get("wake_chime") or "").strip()
if WAKE_CHIME == "none":
    WAKE_CHIME = ""
# Personality: discret|normal|vif = the add-on gives Nabi a life of its own — a
# random LED colour dance (the iconic Nabaztag "alive" show: LEDs lighting up in
# a random order/colour, belly included, then fading out), plus the odd ear
# wiggle / nose blink, at random intervals during the daytime. "off" = it just
# breathes and you wire your own behaviours in Home Assistant (custom).
PERSONALITY = (os.environ.get("PERSONALITY") or _OPTS.get("personality") or "off").strip().lower()
# Intensity → how often Nabi acts on its own (random seconds in range). off =
# none (you drive it from HA). subtle ≈ a few times/hour, auto ≈ hourly-ish,
# lively ≈ every few minutes.
_PLEVELS = {"discret": (900, 2400), "normal": (360, 1500), "vif": (180, 600)}
PERSONALITY_ACTIVE = PERSONALITY in _PLEVELS
_pmin, _pmax = _PLEVELS.get(PERSONALITY, (360, 1500))
PERSONALITY_MIN_S = int(os.environ.get("PERSONALITY_MIN_S") or _OPTS.get("personality_min_s") or _pmin)
PERSONALITY_MAX_S = int(os.environ.get("PERSONALITY_MAX_S") or _OPTS.get("personality_max_s") or _pmax)
# The control API's shared secret. Until 0.19.0 an empty api_token meant an
# open API, and the review of 2026-11-18 proved what that gave any host on the
# LAN: confirm its own adoption key for a rabbit (and hold its TLS key), forget
# a confirmed one, switch the microphone on and download what it heard. Now an
# empty option means a token generated on first start and kept in /data
# (API_TOKEN_FILE). Requests that come through Home Assistant's ingress (the
# add-on's Web UI, already authenticated by Home Assistant) need none.
API_TOKEN_OPTION = (os.environ.get("API_TOKEN") or _OPTS.get("api_token") or "").strip()
API_TOKEN_FILE = os.environ.get("API_TOKEN_FILE", "/data/api_token")
# The Supervisor's ingress proxy: the only peer on the hassio network that
# speaks for a user Home Assistant has already authenticated.
INGRESS_PEER = "172.30.32.2"
# Phase-2 voice pipeline: button push-to-talk → STT (bundled whisper.cpp) →
# optional conversation agent (Home Assistant, e.g. Claude) → TTS, all triggered
# by the rabbit's button. When voice_pipeline is off the recording is just stored.
VOICE_PIPELINE = str(os.environ.get("VOICE_PIPELINE") or _OPTS.get("voice_pipeline") or "").lower() in ("1", "true", "yes", "on")
CONVERSATION_AGENT = os.environ.get("CONVERSATION_AGENT") or _OPTS.get("conversation_agent") or ""
if CONVERSATION_AGENT == "none":
    CONVERSATION_AGENT = ""
STT_LANGUAGE = os.environ.get("STT_LANGUAGE") or _OPTS.get("stt_language") or "fr"
# Prepended to what we send the conversation agent, so it can drive the rabbit
# by embedding action tags in its reply (executed + stripped before speaking).
# Nabi's persona + action-tag instructions, prepended to what the agent hears.
# Fixed in code (not a user option) — this IS the intended character.
VOICE_PROMPT = os.environ.get("VOICE_PROMPT") or (
    "Tu es Nabi, un lapin Nabaztag (le lapin culte de Violet) : tu t'appelles Nabi "
    "et tu parles à la première personne. Théâtral, espiègle, ULTRA bref. Réponds en "
    "français en UNE phrase courte (jamais de monologue). Soigne la mise en scène : "
    "ménage des pauses dramatiques avec « … » (ex. la météo se dit « Aujourd'hui… "
    "pluie ! »). Pas de markdown ni d'emoji. Tu peux AGIR en insérant des balises "
    "(exécutées puis retirées de la parole) : [ears G D] (oreilles 0 à 16), "
    "[led ZONE R V B] (ZONE = bottom|left|middle|right|top, couleurs 0 à 255), "
    "[nose N] (nez 0,1,2). Voici ce qu'on te dit : "
)
# TTS engine: "espeak" = bundled, robotic, fully local. "piper"/"ha" = synthesize
# through ANY Home Assistant TTS entity (set by tts_entity, fetched via the
# Supervisor proxy) — Piper for a local neural voice, or point tts_entity at a
# natural cloud/HA-Cloud voice (HA Cloud, ElevenLabs, Google, Azure, OpenAI…).
TTS_ENGINE = (os.environ.get("TTS_ENGINE") or _OPTS.get("tts_engine") or "espeak").lower()
# Which HA TTS entity to speak through when tts_engine is piper/ha (e.g. tts.piper,
# tts.home_assistant_cloud, tts.elevenlabs, tts.google_cloud, tts.openai…).
TTS_ENTITY = os.environ.get("TTS_ENTITY") or _OPTS.get("tts_entity") or "tts.piper"
# Pitch-shift the TTS up by this many percent (ffmpeg, tempo preserved) for a
# younger/cuter, more playful timbre. 0 = off; ~10-18 ≈ "mignonne"; >30 = chipmunk.
VOICE_PITCH = int(os.environ.get("VOICE_PITCH") or _OPTS.get("voice_pitch") or 0)
WHISPER_BIN = os.environ.get("WHISPER_BIN", "/usr/local/bin/whisper-cli")
# STT model size — the dominant lever on voice latency. `small` is the most
# accurate; `base`/`tiny` are markedly faster on CPU at some accuracy cost.
# Since 0.19.0 no model is in the image (it was two thirds of it, ~466 MB, for
# a voice that is off by default): the chosen one is downloaded once into
# /data/models when voice or listening is on, checked against its SHA-256.
STT_MODEL = (os.environ.get("STT_MODEL") or _OPTS.get("stt_model") or "small").strip().lower()
MODELS_DIR = os.environ.get("MODELS_DIR", "/data/models")
# From a fixed revision of huggingface.co/ggerganov/whisper.cpp; `small` from
# this project's own mirror release, as the image used to bundle it.
WHISPER_MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
_MIRROR = "https://github.com/ClaraVnk/nabaztag/releases/download/mirror-2026-11"
_HF = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{WHISPER_MODEL_REVISION}"
WHISPER_MODELS = {
    "tiny": (f"{_HF}/ggml-tiny.bin",
             "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"),
    "base": (f"{_HF}/ggml-base.bin",
             "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "small": (f"{_MIRROR}/ggml-small.bin",
              "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"),
}
if STT_MODEL not in WHISPER_MODELS:
    STT_MODEL = "small"
_STT_MODEL = {"path": os.environ.get("WHISPER_MODEL"), "failed_at": 0.0}
_STT_MODEL_LOCK = threading.Lock()
# A failed download is tried again after this long, not on every press.
MODEL_RETRY_S = 600


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def whisper_model_path(wait: bool = True) -> str | None:
    """The ggml model for STT_MODEL, downloaded on first use; None while it
    is not there (being fetched by another thread, or the last try failed
    less than MODEL_RETRY_S ago)."""
    if _STT_MODEL["path"]:
        return _STT_MODEL["path"]
    name = f"ggml-{STT_MODEL}.bin"
    url, sha = WHISPER_MODELS[STT_MODEL]
    target = os.path.join(MODELS_DIR, name)
    if not _STT_MODEL_LOCK.acquire(blocking=wait):
        return None
    try:
        if _STT_MODEL["path"]:
            return _STT_MODEL["path"]
        if os.path.exists(target):
            # Checked once per start: a truncated file is fetched again
            # rather than handed to whisper.
            if _sha256_file(target) == sha:
                _STT_MODEL["path"] = target
                return target
            log.warning("STT: %s does not match its SHA-256, fetching it again", target)
        if time.time() - _STT_MODEL["failed_at"] < MODEL_RETRY_S:
            return None
        tmp = target + ".part"
        try:
            os.makedirs(MODELS_DIR, exist_ok=True)
            log.info("STT: downloading the %s model (once, into %s)", STT_MODEL, MODELS_DIR)
            h = hashlib.sha256()
            req = urllib.request.Request(url, headers={"User-Agent": "nabaztag-addon"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
                for block in iter(lambda: r.read(1 << 20), b""):
                    h.update(block)
                    out.write(block)
            if h.hexdigest() != sha:
                raise ValueError(f"SHA-256 {h.hexdigest()} is not the pinned {sha}")
            os.replace(tmp, target)
            log.info("STT: model %s ready", name)
            _STT_MODEL["path"] = target
            return target
        except Exception as exc:  # noqa -- a network, a disk, a mirror
            _STT_MODEL["failed_at"] = time.time()
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            log.warning("STT: the %s model could not be downloaded (%s); no "
                        "transcription until it is. Tried again in %d min.",
                        STT_MODEL, exc, MODEL_RETRY_S // 60)
            return None
    finally:
        _STT_MODEL_LOCK.release()


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nabaztag")

# The adoption keys (adoption.py). Read once; an unreadable file stops the
# add-on rather than being replaced by an empty one.
KEYS = adoption.KeyStore(ADOPTION_KEYS_FILE)


def _resolve_api_token() -> tuple[str, str]:
    """(the token, where it came from): the option, the file a previous
    start wrote, a new one written now, or -- /data not writable -- one for
    this process only."""
    if API_TOKEN_OPTION:
        return API_TOKEN_OPTION, "option"
    try:
        with open(API_TOKEN_FILE, encoding="ascii") as fh:
            tok = fh.read().strip()
        if re.fullmatch(r"[0-9a-f]{32}", tok):
            return tok, "file"
    except (OSError, ValueError):
        pass
    tok = secrets.token_hex(16)
    tmp = API_TOKEN_FILE + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(tok + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, API_TOKEN_FILE)
        return tok, "new"
    except OSError:
        return tok, "memory"


API_TOKEN, API_TOKEN_SOURCE = _resolve_api_token()

_MAC_RE = re.compile(r"[0-9a-f]{12}")


def mac_from_query(q: dict) -> str | None:
    """The rabbit's MAC from bc.jsp's m= or locate.jsp's sn=, twelve hex
    digits, whatever separators the build put in."""
    for k in ("m", "sn"):
        v = (q.get(k, [""])[0] or "").replace(":", "").replace("-", "").lower()
        if _MAC_RE.fullmatch(v):
            return v
    return None

# Registry of connected rabbits: mac -> XmppSession
BUNNIES: dict[str, "XmppSession"] = {}
BUNNIES_LOCK = threading.Lock()
# mac -> epoch of last disconnect; powers "offline for X" in /api/status + logs.
LAST_SEEN: dict[str, float] = {}
# The rabbit writes a " " every 8 s when idle (XmppTcpIdleTime, main.mtl:191/604).
# A bound session silent for longer than this is dead (Wi-Fi lost, unplugged, no
# FIN). 0 = never (old behaviour).
XMPP_IDLE_S = float(os.environ.get("XMPP_IDLE_S") or _OPTS.get("xmpp_idle_s") or 90)
# A pushed sound makes the rabbit bind `streaming` within a second or two; past
# this, it is ignoring what we push (_check_sound_ack).
SOUND_ACK_S = 10
# What one connection may hold, from Le Terrier (xmpp.py): the parser keeps
# everything up to the close tag of the current stanza, and one client sending
# `<a>` then megabytes filled its container. The biggest thing a rabbit sends
# is the getrunningstate reply, about 400 characters.
MAX_BUFFER = 32 * 1024
# A rabbit authenticates within seconds of connecting.
PREAUTH_TIMEOUT_S = 120
# Concurrent XMPP connections, both ports together: a household's rabbits
# and their reconnects are far below either.
MAX_CONN_PER_IP = 16
MAX_CONN_TOTAL = 64
# The ssPresence nudge (XmppSession.check_presence_nudge).
NUDGE_AFTER_S = 20
NUDGE_MAX = 3
# The resource a rabbit binds and the id of its iq come back in what we send
# it, between quotes: a name, never markup (Le Terrier _TOKEN, _JID).
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_JID = re.compile(r"[A-Za-z0-9_.:@/-]{1,128}")
_CONNS: dict[str, int] = {}
_CONN_LOCK = threading.Lock()


def _token(text: str, default: str) -> str:
    return text if _TOKEN.fullmatch(text or "") else default


def _admit(ip: str) -> bool:
    """Count a new XMPP connection in, or refuse it over the caps."""
    with _CONN_LOCK:
        if sum(_CONNS.values()) >= MAX_CONN_TOTAL or _CONNS.get(ip, 0) >= MAX_CONN_PER_IP:
            return False
        _CONNS[ip] = _CONNS.get(ip, 0) + 1
    return True


def _release(ip: str) -> None:
    with _CONN_LOCK:
        n = _CONNS.get(ip, 0) - 1
        if n > 0:
            _CONNS[ip] = n
        else:
            _CONNS.pop(ip, None)


_SID = itertools.count(1)
# Resource ids for the TLS sessions, one counter for the whole add-on, so a
# reconnect never meets an id it saw before.
_RES_IDS = itertools.count(1)

# --------------------------------------------------------------------------- #
# https:// streams opened by the rabbit itself (patch_https_play.py)
# --------------------------------------------------------------------------- #
# The rabbit carries no roots of its own: with the order to play, it is sent
# the one root the host's chain really ends at, as Le Terrier does
# (terrier/https_roots.py). Found with this container's own store, cached a
# day per host.
HTTPS_ROOT_TTL_S = 24 * 3600
_HTTPS_ROOTS: dict = {}
_HTTPS_LOCK = threading.Lock()


def _https_root(host: str, port: int = 443):
    """The DER of the root the host's chain ends at, or None."""
    key, now = (host.lower(), port), time.time()
    with _HTTPS_LOCK:
        hit = _HTTPS_ROOTS.get(key)
    if hit and now - hit[0] < HTTPS_ROOT_TTL_S:
        return hit[1]
    try:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((host, port), timeout=10) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            chain = tls.get_verified_chain()
    except (OSError, ssl.SSLError, ValueError) as exc:
        log.warning("https: no verified chain for %s:%d (%s)", host, port, exc)
        return None
    if not chain:
        return None
    root = bytes(chain[-1])
    with _HTTPS_LOCK:
        _HTTPS_ROOTS[key] = (now, root)
    return root


def https_for_rabbit(url: str):
    """(the URL after its redirects, the root its chain ends at) for an
    https:// address; None when it cannot be opened or verified -- a host
    this add-on cannot verify is one the rabbit could not either."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nabaztag-addon"})
        with urllib.request.urlopen(req, timeout=10) as r:
            final = r.geturl()
    except Exception as exc:  # noqa -- somebody else's server
        log.warning("https: %s does not open (%s)", url[:80], exc)
        return None
    parts = urlparse(final)
    if parts.scheme != "https" or not parts.hostname:
        return (final, None) if parts.scheme == "http" else None
    root = _https_root(parts.hostname, parts.port or 443)
    return (final, root) if root else None
# How long a sound inside the session waits for the one before it to start.
RES_PACE_S = 40


def _ago(seconds: float) -> str:
    """Human-friendly duration: '45s', '12m03s', '1h05m'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"

# --------------------------------------------------------------------------- #
# Violet packet helpers
# --------------------------------------------------------------------------- #
# Packet framing (from OpenJabNab packet.cpp): each packet is
#   0x7F  type(1)  len(3, big-endian)  payload  0xFF
# Several can be concatenated. Sent base64'd inside the XMPP <packet> element.
def frame_packet(ptype: int, payload: bytes) -> bytes:
    n = len(payload)
    return bytes([0x7F, ptype & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + payload + bytes([0xFF])


# Packet type codes (OpenJabNab packet.h, verified): Ambient=0x04, Message=0x0A,
# Sleep=0x0B.
PKT_AMBIENT = 0x04
PKT_MESSAGE = 0x0A
PKT_SLEEP = 0x0B

# AmbientPacket services (ambientpacket.h). This drives the belly icons (weather,
# stock, mail, air quality), the ear positions and the nose blink.
SVC_DISABLE = 0x00
SVC_WEATHER = 0x01      # 0 Sun,1 Cloudy,2 Smog,3 Rain,4 Snow,5 Storm
SVC_STOCK = 0x02
SVC_PERIPH = 0x03
SVC_EAR_LEFT = 0x04    # value = ear position
SVC_EAR_RIGHT = 0x05   # value = ear position
SVC_EMAIL = 0x06
SVC_AIRQUALITY = 0x07
SVC_NOSE = 0x08        # 0 None,1 Blink,2 DoubleBlink
SVC_BOTTOMLED = 0x09
SVC_TAICHI = 0x0E
WEATHER = {"sun": 0, "cloudy": 1, "smog": 2, "rain": 3, "snow": 4, "storm": 5}


def ambient_internal(services: dict) -> bytes:
    """AmbientPacket internal data (ambientpacket.cpp): 0x7FFFFFFE header, then
    (service, value) byte pairs sorted by service id (Qt QMap order)."""
    out = bytes([0x7F, 0xFF, 0xFF, 0xFE])
    for k in sorted(services):
        out += bytes([k & 0xFF, services[k] & 0xFF])
    return out


def ambient_packet(services: dict) -> bytes:
    return frame_packet(PKT_AMBIENT, ambient_internal(services))


def pack_list(packets) -> bytes:
    """Concatenate packets like OpenJabNab Packet::GetData(list): a single leading
    0x7F, then {type, len(3), data} per packet, and one trailing 0xFF."""
    out = bytes([0x7F])
    for ptype, data in packets:
        n = len(data)
        out += bytes([ptype & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + data
    out += bytes([0xFF])
    return out


# The packet the rabbit needs as the answer to its violet:iq:sources query to
# finish booting (OpenJabNab Bunny::GetInitPacket): AmbientPacket (nose off, ears
# at 0) + SleepPacket(Wake_Up = 0).
INIT_PACKET = pack_list([
    (PKT_AMBIENT, ambient_internal({SVC_NOSE: 0, SVC_EAR_LEFT: 0, SVC_EAR_RIGHT: 0})),
    (PKT_SLEEP, bytes([0])),
])


# --------------------------------------------------------------------------- #
# MessagePacket (type 0x0A) — "programs": the rich command channel.
# --------------------------------------------------------------------------- #
# A program is plain text, one "KEY value" command per line (LF-separated; the
# bytecode strips \r and splits on the first space — main.mtl filterconfig). The
# payload is obfuscated with the rolling cipher from OpenJabNab messagepacket.cpp
# (verified against the bytecode's matching de-obfuscation in main.mtl/info.mtl).
# Commands the bytecode executes (main.mtl runEvalOneCommand):
#   ST <url>  live-stream + play audio from a URL (the local-TTS / sound path)
#   MU <url>  play a short downloaded sound resource (blocking, <=300 KB)
#   MW        wait for the current sound to finish
#   CH <url>  download + play a choreography resource (ears + the 5 RGB LEDs)
#   WT <ms>   wait, staying interactive
# Audio (ST/MU) and choreography (CH) resources are fetched by the rabbit over
# HTTP from THIS server (see the /res/ route on the boot server).
_INVERSION_TABLE = bytes([
    1, 171, 205, 183, 57, 163, 197, 239, 241, 27, 61, 167, 41, 19, 53, 223,
    225, 139, 173, 151, 25, 131, 165, 207, 209, 251, 29, 135, 9, 243, 21, 191,
    193, 107, 141, 119, 249, 99, 133, 175, 177, 219, 253, 103, 233, 211, 245, 159,
    161, 75, 109, 87, 217, 67, 101, 143, 145, 187, 221, 71, 201, 179, 213, 127,
    129, 43, 77, 55, 185, 35, 69, 111, 113, 155, 189, 39, 169, 147, 181, 95,
    97, 11, 45, 23, 153, 3, 37, 79, 81, 123, 157, 7, 137, 115, 149, 63,
    65, 235, 13, 247, 121, 227, 5, 47, 49, 91, 125, 231, 105, 83, 117, 31,
    33, 203, 237, 215, 89, 195, 229, 15, 17, 59, 93, 199, 73, 51, 85, 255,
])


def message_internal(text: bytes) -> bytes:
    """Obfuscated MessagePacket payload (OpenJabNab messagepacket.cpp
    GetInternalData): leading 0x00, then each byte rolled through the inversion
    table keyed on the previous *plaintext* byte (seed 35)."""
    out = bytearray([0x00])
    prev = 35
    for c in text:
        out.append((_INVERSION_TABLE[prev % 128] * c + 47) & 0xFF)
        prev = c
    return bytes(out)


def message_packet(program: str) -> bytes:
    """Frame a program (newline-separated 'KEY value' lines) as a type-0x0A
    violet packet."""
    if not program.endswith("\n"):
        program += "\n"
    return frame_packet(PKT_MESSAGE, message_internal(program.encode("latin-1", "replace")))


# --------------------------------------------------------------------------- #
# Choreography binary (OpenJabNab choregraphy.cpp) — ears + the 5 RGB LEDs.
# --------------------------------------------------------------------------- #
# Layout: len(4, big-endian, of the body) | body | 4-byte zero trailer, where
#   body = 0x00 0x01 <tempo/10>  then per action  <deltaTicks(1)> <action bytes>
#   motor action: 0x08 <ear 0=L/1=R> <angle/18> <dir 0=fwd/1=back>
#   led   action: 0x07 <led 0=bottom,1=left,2=middle,3=right,4=top> <r> <g> <b> 0x00 0x00
LED_NAMES = {"bottom": 0, "left": 1, "middle": 2, "right": 3, "top": 4}
EAR_NAMES = {"left": 0, "right": 1}


def build_choreography(tempo_ms: int, actions: list) -> bytes:
    """actions: list of (time_ticks, "motor"|"led", params). motor params =
    (ear, angle_deg, dir); led params = (led, r, g, b)."""
    if tempo_ms > 2550:
        t = 0xFF
    elif tempo_ms < 10:
        t = 0x01
    else:
        t = tempo_ms // 10
    body = bytearray([0x00, 0x01, t & 0xFF])
    last = 0
    for at, kind, params in sorted(actions, key=lambda a: a[0]):
        delta = min(max(at - last, 0), 255)
        body.append(delta & 0xFF)
        if kind == "motor":
            ear, angle, d = params
            body += bytes([0x08, ear & 0xFF, (angle // 18) & 0xFF, d & 0xFF])
        else:  # led
            led, r, g, b = params
            body += bytes([0x07, led & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0x00])
        last = at
    n = len(body)
    return bytes([(n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]) + bytes(body) + bytes(4)


def parse_choreography_spec(spec: str):
    """Parse the comma form 'tempo,time,order,p3,p4,p5,p6,...' (OpenJabNab
    Choregraphy::Parse): order=motor → p3=ear,p4=angle,p6=dir; order=led →
    p3=led,p4=r,p5=g,p6=b. Returns (tempo_ms, actions)."""
    parts = [s.strip() for s in spec.split(",")]
    tempo = int(parts[0])
    actions = []
    i = 1
    while i + 5 < len(parts) + 1 and i + 5 <= len(parts):
        time = int(parts[i]); order = parts[i + 1].lower()
        p3 = int(parts[i + 2]); p4 = int(parts[i + 3]); p5 = int(parts[i + 4]); p6 = int(parts[i + 5])
        if order == "motor":
            actions.append((time, "motor", (p3, p4, p6)))
        elif order == "led":
            actions.append((time, "led", (p3, p4, p5, p6)))
        else:
            raise ValueError(f"bad choreography order {order!r}")
        i += 6
    return tempo, actions


# --------------------------------------------------------------------------- #
# Resource store — the rabbit downloads audio / choreography resources over HTTP
# from this server (referenced by full URL in ST/MU/CH program commands).
# --------------------------------------------------------------------------- #
# Bounded, and it forgets. It used to be an unbounded dict that nothing ever
# removed from, so every choreography, jingle and spoken sentence stayed in
# memory until the add-on restarted. pacifox measured it in #28: ~310 bytes
# per /api/led call, ~9 MB after twenty jingles, tens of MB a day from a
# client that animates the belly once a second.
#
# Three bounds, because any one of them has a bad day. Age is the normal
# case: the rabbit fetches a resource within seconds of the program that
# names it, so two minutes is already generous. Count covers a burst too
# fast for the clock to matter. Bytes covers a few large ones — a podcast
# episode is not a jingle, and a count alone would hold gigabytes of them.
RES_MAX_AGE = 120.0
RES_MAX_ITEMS = 256
RES_MAX_BYTES = 48 << 20

RESOURCES: "OrderedDict[str, tuple[str, bytes, float]]" = OrderedDict()
RES_LOCK = threading.Lock()
RES_BYTES = 0
RES_DROPPED = 0

# Last microphone recording the rabbit uploaded (button push-to-talk → POST
# /vl/record.jsp). The bytecode records at 8 kHz and POSTs a RIFF WAV in
# IMA/DVI-ADPCM (mono, 4-bit, 256-byte blocks). This is the Phase-2 voice input.
LAST_RECORDING = {"wav": None, "pcm_wav": None, "ts": 0.0, "mode": None, "mac": None}
REC_LOCK = threading.Lock()


def ima_adpcm_to_pcm_wav(wav_adpcm: bytes) -> bytes:
    """Decode a mono IMA/DVI-ADPCM RIFF WAV (what the rabbit uploads) to a
    standard 16-bit PCM RIFF WAV, so any STT engine can read it. Pure-Python,
    no deps. Falls back to returning the input unchanged if it can't parse."""
    import struct
    try:
        if wav_adpcm[:4] != b"RIFF" or wav_adpcm[8:12] != b"WAVE":
            return wav_adpcm
        # Walk chunks to find fmt + data.
        i = 12
        fmt = None
        data = b""
        while i + 8 <= len(wav_adpcm):
            cid = wav_adpcm[i:i + 4]
            clen = struct.unpack_from("<I", wav_adpcm, i + 4)[0]
            body = wav_adpcm[i + 8:i + 8 + clen]
            if cid == b"fmt ":
                fmt = body
            elif cid == b"data":
                data = body
            i += 8 + clen + (clen & 1)
        if fmt is None or not data:
            return wav_adpcm
        channels = struct.unpack_from("<H", fmt, 2)[0]
        rate = struct.unpack_from("<I", fmt, 4)[0]
        block_align = struct.unpack_from("<H", fmt, 12)[0]
        if channels != 1:
            return wav_adpcm  # only mono (the rabbit is mono)
        step_tab = [7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,
                    60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,
                    307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,
                    1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,
                    4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,
                    13899,15289,16818,18500,20350,22385,24623,27086,29794,32767]
        idx_tab = [-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8]
        out = bytearray()
        ba = block_align or 256
        for b in range(0, len(data), ba):
            block = data[b:b + ba]
            if len(block) < 4:
                break
            pred = struct.unpack_from("<h", block, 0)[0]
            index = block[2]
            index = max(0, min(88, index))
            out += struct.pack("<h", pred)
            for nb in range(4, len(block)):
                byte = block[nb]
                for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
                    step = step_tab[index]
                    diff = step >> 3
                    if nibble & 1: diff += step >> 2
                    if nibble & 2: diff += step >> 1
                    if nibble & 4: diff += step
                    if nibble & 8: pred -= diff
                    else: pred += diff
                    pred = max(-32768, min(32767, pred))
                    index = max(0, min(88, index + idx_tab[nibble]))
                    out += struct.pack("<h", pred)
        n = len(out)
        hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
               struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
               b"data" + struct.pack("<I", n))
        return hdr + bytes(out)
    except Exception:
        return wav_adpcm


def _res_evict_locked(now: float) -> None:
    """Oldest first, until every bound is satisfied. Caller holds RES_LOCK."""
    global RES_BYTES, RES_DROPPED
    while RESOURCES:
        _, (_, content, stored_at) = next(iter(RESOURCES.items()))
        if not (now - stored_at > RES_MAX_AGE
                or len(RESOURCES) > RES_MAX_ITEMS
                or RES_BYTES > RES_MAX_BYTES):
            return
        RESOURCES.popitem(last=False)
        RES_BYTES -= len(content)
        RES_DROPPED += 1


def store_resource(content: bytes, content_type: str = "application/octet-stream") -> str:
    global RES_BYTES
    token = os.urandom(8).hex()
    now = time.time()
    with RES_LOCK:
        RESOURCES[token] = (content_type, content, now)
        RES_BYTES += len(content)
        _res_evict_locked(now)
    return token


def resource_stats() -> dict:
    """What the store is holding, so the number is visible rather than
    inferred from RSS."""
    with RES_LOCK:
        return {"items": len(RESOURCES), "bytes": RES_BYTES, "dropped": RES_DROPPED}


def resource_url(token: str) -> str:
    return f"http://{SERVER_ADDRESS}/res/{token}"


def _clean_for_tts(text: str) -> str:
    """Strip markdown/emoji so the TTS doesn't read them aloud (conversation
    agents like Claude often add *emphasis* and 🐰 emoji)."""
    text = re.sub(r"[*_`#>~|]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿️]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _audio_ctype(data: bytes) -> str:
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    return "audio/wav"


def _audio_duration_s(data: bytes) -> float:
    """Best-effort playback length in seconds, to time the mic re-arm so it
    doesn't cut a reply short."""
    try:
        if data[:4] == b"RIFF":
            import io, wave
            w = wave.open(io.BytesIO(data))
            d = w.getnframes() / float(w.getframerate() or 22050)
            w.close()
            return d
    except Exception:  # noqa
        pass
    return max(2.0, len(data) / 8000.0)  # rough estimate for MP3


def _supervisor_token() -> str:
    """The Supervisor API token, from the environment (the image has no s6,
    so there is no s6 file to fall back to)."""
    return os.environ.get("SUPERVISOR_TOKEN", "")


def fire_ha_event(etype: str, data: dict):
    """Fire a Home Assistant event via the Supervisor proxy (fire-and-forget) so
    automations can trigger on Nabi's inputs (button / RFID / ear-move)."""
    token = _supervisor_token()
    if not token:
        return

    def _post():
        try:
            req = urllib.request.Request(
                f"http://supervisor/core/api/events/{etype}",
                data=json.dumps(data).encode(), method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:  # noqa
            log.warning("HA event %s failed: %s", etype, exc)
    threading.Thread(target=_post, daemon=True).start()


def notify_ha(title: str, message: str, notification_id: str):
    """A persistent notification in Home Assistant (fire-and-forget)."""
    token = _supervisor_token()
    if not token:
        return

    def _post():
        try:
            req = urllib.request.Request(
                "http://supervisor/core/api/services/persistent_notification/create",
                data=json.dumps({"title": title, "message": message,
                                 "notification_id": notification_id}).encode(),
                method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:  # noqa
            log.warning("HA notification failed: %s", exc)
    threading.Thread(target=_post, daemon=True).start()


def synth_via_ha(text: str) -> bytes:
    """Synthesize via a Home Assistant TTS engine (e.g. the Piper add-on) using
    the Supervisor proxy; returns the audio bytes (MP3). "" on failure."""
    token = _supervisor_token()
    if not token:
        return b""
    try:
        body = json.dumps({"engine_id": TTS_ENTITY, "message": text}).encode()
        req = urllib.request.Request(
            "http://supervisor/core/api/tts_get_url", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        path = json.loads(urllib.request.urlopen(req, timeout=10).read()).get("path")
        if not path:
            return b""
        areq = urllib.request.Request("http://supervisor/core" + path,
                                      headers={"Authorization": f"Bearer {token}"})
        return urllib.request.urlopen(areq, timeout=10).read()
    except Exception as exc:  # noqa
        log.warning("Piper/HA TTS failed: %s", exc)
        return b""


def _pitch(data: bytes) -> bytes:
    """Pitch-shift up by VOICE_PITCH percent (tempo preserved, ffmpeg) for a
    cuter/younger timbre. Returns the input unchanged when off or on failure."""
    if not VOICE_PITCH or not data:
        return data
    k = 1.0 + max(0, min(VOICE_PITCH, 40)) / 100.0
    ext = "wav" if data[:4] == b"RIFF" else "mp3"
    src = dst = None
    try:
        with tempfile.NamedTemporaryFile(suffix="." + ext, delete=False) as f:
            f.write(data); src = f.name
        sr = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate", "-of", "csv=p=0", src],
            capture_output=True, text=True, timeout=10).stdout.strip()
        sr = int(sr) if sr.isdigit() else 22050
        dst = src + ".p." + ext
        af = f"asetrate={int(sr * k)},aresample={sr},atempo={1.0 / k:.4f}"
        subprocess.run(["ffmpeg", "-y", "-i", src, "-af", af, "-b:a", "96k", dst],
                       check=True, capture_output=True, timeout=20)
        with open(dst, "rb") as fh:
            return fh.read()
    except Exception as exc:  # noqa
        log.warning("pitch failed: %s", exc)
        return data
    finally:
        for p in (src, dst):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _to_rabbit_audio(data: bytes) -> bytes:
    """Re-encode speech into what the rabbit's decoder actually wants.

    A Home Assistant TTS entity hands back whatever its engine produces:
    Piper a 22050 Hz WAV, a cloud voice a 44.1 kHz stereo MP3. The VS1003 in
    the rabbit is not a media player — it wants mono, and a WAV long enough
    to be a sentence overruns the playback buffer, which is heard as silence.
    The espeak path has always downsampled for exactly that reason; this one
    sent the bytes through untouched, so a rabbit that played the built-in
    jingles perfectly stayed mute on every spoken word. Reported 2026-11-04.

    Mono MP3 at 22050 Hz, constant bit rate: the format Violet itself streamed
    to these rabbits, and a tenth of the size of the WAV. Returns the input
    unchanged if ffmpeg is unavailable or fails — mute speech is better than
    no speech.
    """
    if not data:
        return data
    src = dst = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as f:
            f.write(data); src = f.name
        dst = src + ".mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", "22050",
             "-codec:a", "libmp3lame", "-b:a", "64k", dst],
            check=True, capture_output=True, timeout=30)
        with open(dst, "rb") as fh:
            out = fh.read()
        return out or data
    except Exception as exc:  # noqa
        log.warning("could not re-encode TTS for the rabbit: %s", exc)
        return data
    finally:
        for q in (src, dst):
            if q:
                try:
                    os.unlink(q)
                except OSError:
                    pass


def synth_tts(text: str, voice: str = "fr", speed: int = 160, pitch: int = 50) -> bytes:
    """Make speech audio for the rabbit. When tts_engine is piper/ha it synthesizes
    through the chosen Home Assistant TTS entity (tts_entity) — a neural voice;
    otherwise the bundled espeak-ng (always works, fully local). Returns mono
    22050 Hz MP3 (HA TTS, re-encoded for the rabbit's decoder) or 8 kHz WAV
    (espeak); the rabbit decodes both. VOICE_PITCH > 0 shifts it up."""
    text = _clean_for_tts(text)
    if TTS_ENGINE in ("piper", "ha"):
        audio = synth_via_ha(text)
        if audio:
            return _to_rabbit_audio(_pitch(audio))
        log.warning("HA TTS (%s) returned nothing — falling back to espeak", TTS_ENTITY)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        path = tf.name
    try:
        subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(speed), "-p", str(pitch), "-w", path, text],
            check=True, capture_output=True, timeout=25,
        )
        with open(path, "rb") as fh:
            data = fh.read()
        # Downsample to 8 kHz: espeak's 22 kHz WAV gets large and the rabbit
        # truncates big WAVs; ~3 s @ 8 kHz ≈ 47 KB fits its playback buffer.
        return _pitch(_resample_wav(data, 8000))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _resample_wav(wav_bytes: bytes, target_rate: int) -> bytes:
    """Return a mono 16-bit PCM WAV at target_rate. Uses ffmpeg (already bundled)
    rather than the stdlib `audioop`, which was removed in Python 3.13."""
    src = dst = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes); src = f.name
        dst = src + ".out.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(target_rate),
             "-sample_fmt", "s16", "-f", "wav", dst],
            check=True, capture_output=True, timeout=20)
        with open(dst, "rb") as fh:
            return fh.read()
    except Exception:
        return wav_bytes
    finally:
        for p in (src, dst):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _resample_wav_16k(wav_bytes: bytes) -> bytes:
    """16 kHz mono PCM WAV (what whisper.cpp expects; the rabbit records at 8 kHz)."""
    return _resample_wav(wav_bytes, 16000)


def _wav_peak_rms(wav_bytes: bytes, frame_ms: int = 100) -> int:
    """Loudest short-frame RMS in the clip — cheap voice-activity gate. Returns a
    huge value on error so the caller never gates out audio it failed to read.
    Pure-python (no stdlib `audioop`, removed in Python 3.13); 16-bit PCM only."""
    import wave, io, array, math
    try:
        w = wave.open(io.BytesIO(wav_bytes))
        sw, rate = w.getsampwidth(), w.getframerate()
        frames = w.readframes(w.getnframes())
        w.close()
        if sw != 2:  # only 16-bit handled here — don't gate out what we can't read
            return 10 ** 9
        samples = array.array("h")  # native (little-endian on amd64) signed 16-bit
        samples.frombytes(frames[: len(frames) // 2 * 2])
        step = max(1, int(rate * frame_ms / 1000))
        peak = 0
        for i in range(0, len(samples), step):
            chunk = samples[i:i + step]
            if chunk:
                rms = int(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
                if rms > peak:
                    peak = rms
        return peak
    except Exception:
        return 10 ** 9


JINGLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")


def list_jingles() -> list:
    """Names of the bundled iconic-Nabaztag jingles (the .mid files extracted
    from the original firmware)."""
    try:
        return sorted(f[:-4] for f in os.listdir(JINGLES_DIR) if f.endswith(".mid"))
    except OSError:
        return []


def _midi_to_wav(data: bytes, rate: int = 16000) -> bytes:
    """Tiny dependency-free Standard-MIDI-File -> mono 16-bit PCM WAV synth
    (a percussive, music-box-ish tone). Lets us play the original Nabaztag
    jingles through the rabbit's normal audio path. Returns b"" on bad input."""
    import struct, wave, math, io
    if data[:4] != b"MThd":
        return b""
    div = struct.unpack(">H", data[12:14])[0] or 480
    p = data.find(b"MTrk")
    if p < 0:
        return b""
    tlen = struct.unpack(">I", data[p + 4:p + 8])[0]
    i = p + 8
    end = min(i + tlen, len(data))
    tempo = 500000
    t = 0
    status = 0
    live = {}
    events = []

    def _vlq(j):
        v = 0
        while True:
            x = data[j]; j += 1; v = (v << 7) | (x & 0x7f)
            if not (x & 0x80):
                return v, j

    def sec(tk):
        return tk * (tempo / 1e6) / div

    while i < end:
        dt, i = _vlq(i); t += dt
        x = data[i]
        if x & 0x80:
            status = x; i += 1
        ev = status & 0xf0
        if ev == 0x90:
            note = data[i]; vel = data[i + 1]; i += 2
            if vel:
                live[note] = (sec(t), vel)
            else:
                s = live.pop(note, None)
                if s:
                    events.append((s[0], sec(t) - s[0], 440 * 2 ** ((note - 69) / 12), s[1]))
        elif ev == 0x80:
            note = data[i]; i += 2
            s = live.pop(note, None)
            if s:
                events.append((s[0], sec(t) - s[0], 440 * 2 ** ((note - 69) / 12), s[1]))
        elif ev in (0xA0, 0xB0, 0xE0):
            i += 2
        elif ev in (0xC0, 0xD0):
            i += 1
        elif status == 0xFF:
            mt = data[i]; i += 1; ln, i = _vlq(i)
            if mt == 0x51:
                tempo = int.from_bytes(data[i:i + 3], "big")
            i += ln
        elif status in (0xF0, 0xF7):
            ln, i = _vlq(i); i += ln
        else:
            i += 1

    total = max((s + d for s, d, _, _ in events), default=0.3) + 0.06
    n = int(total * rate)
    buf = [0.0] * n
    for start, dur, freq, vel in events:
        s0 = int(start * rate); dur = max(dur, 0.08); ns = int(dur * rate); amp = vel / 127.0
        for k in range(ns):
            if s0 + k >= n:
                break
            tt = k / rate
            envv = math.exp(-3.2 * tt / dur) * (1 - math.exp(-tt / 0.004))
            ph = 2 * math.pi * freq * tt
            buf[s0 + k] += (math.sin(ph) + 0.5 * math.sin(2 * ph) + 0.2 * math.sin(3 * ph)) * envv * amp
    peak = max((abs(v) for v in buf), default=1.0) or 1.0
    g = 0.85 / peak
    out = io.BytesIO()
    w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v * g)) * 32767)) for v in buf))
    w.close()
    return out.getvalue()


def render_jingle(name: str, rate: int = 16000):
    """Render a bundled jingle (.mid) to WAV bytes; None if the name is unknown.
    The name is sanitised to prevent path traversal."""
    return _render_jingle(re.sub(r"[^A-Za-z0-9_]", "", name or ""), rate)


# Twelve jingles of a few tens of KB each, synthesised in pure Python: once
# per name, not on every connect and every wake word.
@functools.lru_cache(maxsize=32)
def _render_jingle(safe: str, rate: int):
    path = os.path.join(JINGLES_DIR, safe + ".mid")
    if not safe or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            return _midi_to_wav(fh.read(), rate)
    except Exception:  # noqa
        return None


def stt_transcribe(pcm_wav: bytes) -> str:
    """Transcribe a PCM WAV with the bundled whisper.cpp (resampled to 16 kHz).
    Returns "" if the binary/model is missing or nothing is recognised."""
    model = whisper_model_path(wait=False)
    if not (model and os.path.exists(WHISPER_BIN) and os.path.exists(model)):
        log.warning("STT unavailable (whisper missing: %s / %s)", WHISPER_BIN, model)
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tf.write(_resample_wav_16k(pcm_wav))
        path = tf.name
    try:
        subprocess.run(
            [WHISPER_BIN, "-m", model, "-f", path, "-l", STT_LANGUAGE,
             "-nt", "-otxt", "-of", path],
            check=True, capture_output=True, timeout=120,
        )
        with open(path + ".txt", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception as exc:  # noqa
        log.warning("STT failed: %s", exc)
        return ""
    finally:
        for ext in ("", ".txt"):
            try:
                os.unlink(path + ext)
            except OSError:
                pass


def conversation_ask(text: str) -> str:
    """Send text to a Home Assistant conversation agent (e.g. the Anthropic/Claude
    integration) via the Supervisor proxy; return the agent's reply text."""
    token = _supervisor_token()
    if not (token and CONVERSATION_AGENT):
        return ""
    body = json.dumps({"agent_id": CONVERSATION_AGENT, "text": text}).encode()
    req = urllib.request.Request(
        "http://supervisor/core/api/services/conversation/process?return_response",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
        sr = j.get("service_response") or j
        return (((sr.get("response") or {}).get("speech") or {}).get("plain") or {}).get("speech", "").strip()
    except Exception as exc:  # noqa
        log.warning("conversation agent call failed: %s", exc)
        return ""


_LED_ZONES = {"bottom": 0, "left": 1, "middle": 2, "right": 3, "top": 4,
              "ventre": 0, "gauche": 1, "milieu": 2, "droite": 3, "haut": 4}


def run_action_tags(bunny, text: str) -> str:
    """Execute [ears L R] / [led ZONE R G B] / [nose N] tags a conversation agent
    embedded in its reply, and return the text with those tags removed (to speak)."""
    def repl(m):
        parts = m.group(1).split()
        if not parts:
            return ""
        kind = parts[0].lower()
        try:
            if kind in ("ears", "oreilles") and len(parts) >= 3:
                l, r = int(parts[1]), int(parts[2])
                bunny.send_choreography(200, [(0, "motor", (0, l * 18, 0)),
                                              (0, "motor", (1, r * 18, 0))])
            elif kind == "led" and len(parts) >= 5:
                zone = _LED_ZONES.get(parts[1].lower(), 0)
                bunny.send_choreography(200, [(0, "led", (zone, int(parts[2]),
                                                          int(parts[3]), int(parts[4])))])
            elif kind in ("nose", "nez") and len(parts) >= 2:
                bunny.send_violet_packet(ambient_packet({SVC_NOSE: int(parts[1])}))
            else:
                return m.group(0)  # not an action tag — keep it
        except Exception as exc:  # noqa
            log.warning("voice: bad action tag %r: %s", m.group(0), exc)
            return ""
        log.info("voice: ran action %s", m.group(0))
        return ""
    return re.sub(r"\[([^\]]+)\]", repl, text)


def _fade(data: bytes) -> bytes:
    """Soften a segment's hard onset/cutoff with a short fade in/out (ffmpeg), so a
    [pause] silence doesn't feel abrupt. Returns the input unchanged on failure."""
    ext = "wav" if data[:4] == b"RIFF" else "mp3"
    src = dst = None
    try:
        st = max(0.0, _audio_duration_s(data) - 0.09)
        with tempfile.NamedTemporaryFile(suffix="." + ext, delete=False) as f:
            f.write(data); src = f.name
        dst = src + ".out." + ext
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-af",
             f"afade=t=in:d=0.04,afade=t=out:st={st:.2f}:d=0.09", "-b:a", "96k", dst],
            check=True, capture_output=True, timeout=15)
        with open(dst, "rb") as fh:
            return fh.read()
    except Exception as exc:  # noqa
        log.warning("fade failed: %s", exc)
        return data
    finally:
        for p in (src, dst):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _plays_a_sound(program: str) -> bool:
    """Does this program make the rabbit play audio?

    ST streams, MU and MC download a short sound. CH is a choreography and
    SV only sets the volume: neither touches the decoder's input.
    """
    return any(line.strip()[:3] in ("ST ", "MU ", "MC ")
               for line in program.splitlines())


def _speak(b, text: str) -> float:
    """Speak text on the rabbit, turning [pause] / [pause N] markers into REAL
    silences (N ms, default 600) for theatrical delivery. A single program with
    several `ST` doesn't work (the first audio's resource-switch drops the rest),
    so we orchestrate from the server: pre-synthesize every segment, then play
    each `ST` on its own and sleep its duration (+ the pauses) between them.
    Blocks for the whole utterance; callers run it in their own thread. Returns
    the total duration (for the wake cooldown)."""
    import time as _t
    has_pause = "[pause" in text
    pieces = re.split(r"\[pause(?:\s+(\d+))?\]", text)
    steps = []  # ("say", wav, dur) | ("pause", seconds)
    for idx, piece in enumerate(pieces):
        if idx % 2 == 0:
            seg = (piece or "").strip()
            if seg:
                wav = synth_tts(seg)
                if wav:
                    steps.append(("say", wav, _audio_duration_s(wav)))
        else:
            steps.append(("pause", max(100, min(int(piece) if piece else 600, 5000)) / 1000.0))
    total = 0.0
    # One hold for the whole phrase: the mic comes back after the last
    # segment, not between each of them.
    with b._hold_speaker():
        for i, st in enumerate(steps):
            if st[0] == "say":
                wav = _fade(st[1]) if has_pause else st[1]
                b.send_program(f"ST {resource_url(store_resource(wav, _audio_ctype(wav)))}")
                # don't wait after the very last segment (caller's cooldown covers it)
                if i < len(steps) - 1:
                    _t.sleep(st[2] + 0.3)
                total += st[2] + 0.3
            else:
                _t.sleep(st[1])
                total += st[1]
    return total


def handle_voice(pcm_wav: bytes, bunny=None):
    """Full voice loop for a button recording: STT → optional conversation agent
    (which can also drive the rabbit via action tags) → speak the reply, on
    the rabbit that recorded it (record.jsp finds it by its address)."""
    if bunny is None:
        return
    # Immediate "got it — thinking…" cue so the user isn't left wondering during
    # the STT + agent latency: a soft cyan belly pulse that fades back to off. It
    # gets overwritten when the reply's own LED/ear actions or speech play.
    try:
        bunny.send_choreography(120,
                                [(0, "led", (z, 0, 60, 130)) for z in range(5)]
                                + [(8, "led", (z, 0, 0, 0)) for z in range(5)])
    except Exception:  # noqa
        pass
    text = stt_transcribe(pcm_wav)
    log.info("voice: heard %r", text)
    if not text:
        return
    if CONVERSATION_AGENT:
        reply = conversation_ask((VOICE_PROMPT or "") + text)
    else:
        reply = text  # no agent → echo what was heard
    if not reply:
        reply = text
    reply = run_action_tags(bunny, reply)  # execute + strip action tags
    log.info("voice: speaking %r", reply)
    try:
        if reply.strip():
            _speak(bunny, reply)
    except Exception as exc:  # noqa
        log.warning("voice: reply playback failed: %s", exc)


# --------------------------------------------------------------------------- #
# XMPP server (one thread per rabbit)
# --------------------------------------------------------------------------- #
class XmppSession(threading.Thread):
    """Handles a single rabbit XMPP connection.

    State machine following InteractionServeurs.pdf:
      stream -> features(SASL) -> auth -> challenge -> response -> SUCCESS
      -> (stream restart) -> features(bind/session) -> bind -> session -> idle
    SASL is bypassed (we just answer <success/>) per the documented trick.
    """

    def __init__(self, conn: socket.socket, addr, tls_mac: str | None = None,
                 first: bytes = b""):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buf = ""
        # The MAC a TLS handshake proved (port XMPP_TLS_PORT), or None on the
        # plain port; and whatever the session read before it was handed here.
        self.tls_mac = tls_mac
        self._first = first
        self._mac = tls_mac
        self.authed = False
        self.bound = False
        self.connected_at = None
        self.domain = SERVER_ADDRESS
        self.resource = "Boot"
        self.msg_nb = 0
        self._alive = True
        self.sid = next(_SID)
        self.tcp_at = time.time()
        self.last_rx = self.tcp_at
        self.last_tx = self.tcp_at
        self._ka = itertools.count(1)
        self.resource_at = None
        # One writer at a time: the session thread, the control API threads
        # and the timers all push on this socket, and two sendall() calls on
        # the same socket may interleave their bytes.
        self._send_lock = threading.Lock()
        # Set when a sound is pushed to an idle rabbit, cleared by the bind
        # that answers it (see _check_sound_ack).
        self._sound_at = None
        # The microphone inside the session (MIC_HEAD): the raw bytes still
        # owed, and an undecoded tail that may start an announce.
        self._mic_left = 0
        self._raw = b""
        # Sounds inside the session, one at a time (RES_PACE_S).
        self._res_played = threading.Event()
        self._res_played.set()
        self._res_queue = queue.Queue()
        self._res_worker = None
        self._pace_lock = threading.Lock()
        # One decoder for the whole stream: a character split across two
        # reads is joined, not turned into two U+FFFD.
        self._utf8 = codecs.getincrementaldecoder("utf-8")("replace")
        # The ssPresence nudge (check_presence_nudge).
        self._nudge_from = 0.0
        self._nudges = 0
        self._presence_at = 0.0
        # Counted in by xmpp_server (_admit), so released when it ends.
        self._admitted = False

    # -- who is talking ---------------------------------------------------- #
    @property
    def mac(self):
        return self._mac

    @mac.setter
    def mac(self, value):
        value = (value or "").lower() or None
        if self.tls_mac is not None:
            # The PSK proved which rabbit this is; a stanza does not get to say
            # otherwise.
            if value is not None and value != self.tls_mac:
                log.warning("rabbit %s: TLS session of %s names itself %s -- closing it",
                            self.addr[0], self.tls_mac, value)
                self._refuse()
            return
        if value is not None:
            row = KEYS.get(value)
            if row is not None and row["state"] == "confirmed":
                # Its owner confirmed its key: that rabbit talks over TLS only,
                # so whoever names it here, in the clear, is not it.
                log.warning("rabbit %s: XMPP as %s on the plain port, but its key is "
                            "confirmed -- TLS port only, closing", self.addr[0], value)
                self._refuse()
                return
        self._mac = value

    def _verified(self) -> bool:
        """Whether this session proved which rabbit it is (its TLS key). On
        the plain port anyone on the LAN can name any rabbit that has no
        confirmed key -- the Violet protocol has no credential -- so Home
        Assistant events say which kind they came from."""
        return self.tls_mac is not None and self.mac == self.tls_mac

    def rides(self, policy: str) -> bool:
        """Whether this session may carry `policy` (adoption.SESSION_POLICIES):
        it is the TLS session of the rabbit whose key opened it, and the
        bytecode its last boot was served reads that policy there."""
        if self.tls_mac is None or self.mac != self.tls_mac:
            return False
        row = KEYS.get(self.tls_mac)
        return row is not None and policy in row["served"]

    def _refuse(self):
        try:
            with self._send_lock:
                self.conn.sendall(b"<failure xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>"
                                  b"<not-authorized/></failure></stream:stream>")
        except OSError:
            pass
        self.close()

    # -- low level --------------------------------------------------------- #
    def _write(self, raw: bytes):
        """Write under the send lock, which the caller holds. Over TLS one
        record per sendall, never more than the rabbit's one-record buffer
        (max_fragment_length 2^12): that is also how the rabbit tells an
        announce from the bytes it announces."""
        self.last_tx = time.time()
        if self.tls_mac is not None:
            for i in range(0, len(raw), adoption.RECORD_BYTES):
                self.conn.sendall(raw[i:i + adoption.RECORD_BYTES])
        else:
            self.conn.sendall(raw)

    def send(self, data: str):
        try:
            with self._send_lock:
                self._write(data.encode("utf-8"))
            log.debug("-> rabbit %s: %s", self.addr[0], data[:300])
        except OSError:
            self._alive = False

    @property
    def jid(self):
        return f"{self.mac or '0000'}@{self.domain}/{self.resource}"

    # -- command push ------------------------------------------------------ #
    def _packet_stanza(self, raw: bytes) -> str:
        with self._send_lock:
            self.msg_nb += 1
            nb = self.msg_nb
        b64 = base64.b64encode(raw).decode("ascii")
        return (
            f"<message from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='ojn-{nb}'>"
            f"<packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet>"
            f"</message>"
        )

    def send_violet_packet(self, raw: bytes):
        self.send(self._packet_stanza(raw))

    def _resources_in_session(self, program: str):
        """The program with each of this add-on's /res/ resources that the
        rabbit will take from its session renamed res:N, and those
        resources, [(N, bytes)], in order: ST when its runtime reads sounds
        there (res), MU, MC and CH when it reads a program's resources there
        too (rsc). Together they fit the rabbit's store, so none is evicted
        by the next one before the program runs. Anything else -- another
        host's URL, a resource over the budget -- stays a URL."""
        keys = (({"ST"} if self.rides("res") else set())
                | ({"MU", "MC", "CH"} if self.rides("res") and self.rides("rsc") else set()))
        if not keys:
            return program, []
        prefix = resource_url("")
        lines = program.split("\n")
        sent, named, total = [], {}, 0
        for i, line in enumerate(lines):
            key, _, url = line.partition(" ")
            url = url.strip()
            if key not in keys or not url.startswith(prefix):
                continue
            if url in named:
                lines[i] = f"{key} res:{named[url]}"
                continue
            with RES_LOCK:
                entry = RESOURCES.get(url[len(prefix):])
            if entry is None:
                continue
            data = entry[1]
            if total + len(data) > adoption.RES_MAX_BYTES or len(sent) >= adoption.RES_KEEP:
                log.info("rabbit %s: resource of %d bytes does not fit its store, sent by URL",
                         self.mac, len(data))
                continue
            rid = next(_RES_IDS)
            named[url] = rid
            sent.append((rid, data))
            total += len(data)
            lines[i] = f"{key} res:{rid}"
        return "\n".join(lines), sent

    def _https_direct(self, program: str):
        """For a rabbit that opens https streams itself (Naboot 1.2.0.0 on):
        `ST https://...` becomes the URL after its redirects with
        `#r<N>@<time>`, and the root its chain ends at goes first as res:N.
        (None, []) when such a host cannot be verified: nothing is sent, as
        on Le Terrier. Any other program as it is."""
        if not self.rides("https"):
            return program, []
        lines, roots = program.split("\n"), []
        for i, line in enumerate(lines):
            if not line.startswith("ST https://"):
                continue
            got = https_for_rabbit(line[3:].strip())
            if got is None:
                log.warning("rabbit %s: %s cannot be verified, nothing sent", self.mac, line[3:83])
                return None, []
            final, root = got
            final = final.split("#", 1)[0]
            if root is None:
                lines[i] = f"ST {final}"
                continue
            rid = next(_RES_IDS)
            roots.append((rid, root))
            lines[i] = f"ST {final}#r{rid}@{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
            log.info("rabbit %s: opens %s itself over HTTPS (root %d bytes as res:%d)",
                     self.mac, final[:80], len(root), rid)
        return "\n".join(lines), roots

    def _send_with_resources(self, program: str):
        """The program, its resources first when they go down the session:
        each announced in a record of its own, its bytes, then the program,
        under one hold of the send lock so nothing comes between them."""
        program, roots = self._https_direct(program)
        if program is None:
            return
        program, sent = self._resources_in_session(program)
        sent = roots + sent
        if not sent:
            self.send_violet_packet(message_packet(program))
            return
        # The rabbit sifts a resource's bytes out of the stream as they arrive
        # but runs the program later; a second sound announced in between
        # drops the first from its store before it plays (emulator: four
        # 136 KB sentences three seconds apart, two heard). So a sound waits
        # until the rabbit has started the one before it, RES_PACE_S at most.
        plays = any(line.startswith("ST res:") for line in program.split("\n"))
        with self._pace_lock:
            idle = (self._res_played.is_set() and self._res_queue.empty()
                    and (self._res_worker is None or not self._res_worker.is_alive()))
            if idle:
                if plays:
                    self._res_played.clear()
            else:
                self._res_queue.put((program, sent, plays))
                if self._res_worker is None or not self._res_worker.is_alive():
                    self._res_worker = threading.Thread(target=self._send_paced, daemon=True)
                    self._res_worker.start()
                return
        self._send_resources(program, sent)

    def _send_paced(self):
        while self._alive:
            try:
                program, sent, plays = self._res_queue.get(timeout=5)
            except queue.Empty:
                return
            if not self._res_played.wait(RES_PACE_S):
                log.info("rabbit %s: the previous sound never started; sending the next", self.mac)
            with self._pace_lock:
                if plays:
                    self._res_played.clear()
            self._send_resources(program, sent)

    def _send_resources(self, program: str, sent):
        stanza = self._packet_stanza(message_packet(program)).encode("utf-8")
        try:
            with self._send_lock:
                for rid, data in sent:
                    self._write(f"<res xmlns='{adoption.RES_NS}' id='{rid}' "
                                f"len='{len(data)}'/>".encode("ascii"))
                    self._write(data)
                self._write(stanza)
            log.info("rabbit %s: %s inside the session", self.mac,
                     ", ".join(f"{len(d)} bytes as res:{r}" for r, d in sent))
        except OSError:
            self._alive = False

    def send_firmware(self, image: bytes, sig: bytes):
        """A firmware update inside the session (patch_ota_in_session.py):
        the announce with fw='1', the plain image, then `FW res:N <signature
        as 128 hex digits>`. The rabbit verifies the signature itself before
        it flashes, exactly as for an image fetched by URL."""
        rid = next(_RES_IDS)
        head = (f"<res xmlns='{adoption.RES_NS}' id='{rid}' len='{len(image)}' "
                f"fw='1'/>").encode("ascii")
        stanza = self._packet_stanza(message_packet(f"FW res:{rid} {sig.hex()}")).encode("utf-8")
        try:
            with self._send_lock:
                self._write(head)
                self._write(image)
                self._write(stanza)
            log.info("rabbit %s: firmware image of %d bytes inside the session as res:%d",
                     self.mac, len(image), rid)
        except OSError:
            self._alive = False

    def send_program(self, program: str):
        """Push a code-0x0A program (newline-separated 'KEY value' commands).

        A program that plays something gets the speaker to itself first: the
        rabbit is half-duplex and says nothing about it, so a sound pushed
        while the microphone streams is fetched over HTTP, answered 200 and
        never heard (#23 finding 5, #27 finding 4). The wake path has always
        done this by hand; here it happens for every sound.
        """
        if _plays_a_sound(program):
            self._free_the_speaker()
            # Only from `idle`: a rabbit already playing, or asleep, may
            # legitimately answer with no bind at all.
            if self.resource == "idle" and self._sound_at is None:
                at = self._sound_at = time.time()
                threading.Timer(SOUND_ACK_S, self._check_sound_ack, args=(at,)).start()
        self._send_with_resources(program)

    def _free_the_speaker(self):
        """Stop a capture of ours so a sound can be heard, and owe it back."""
        if not WAKE.get("streaming") or not self._owns_mic():
            return
        # Not send_program: RT plays nothing, and this must not recurse.
        self.send_violet_packet(message_packet("RT"))
        WAKE["streaming"] = False
        WAKE["rearm"] = True
        log.info("rabbit %s: mic stopped so the speaker is free", self.addr[0])

    @contextlib.contextmanager
    def _hold_speaker(self):
        """Keep the mic off across several sounds, not between each of them.

        Without it, a phrase split into segments re-arms the mic after the
        first one and the rest are refused -- the same silence, one step
        further along.
        """
        WAKE["hold"] = WAKE.get("hold", 0) + 1
        try:
            yield
        finally:
            WAKE["hold"] = max(0, WAKE.get("hold", 1) - 1)
            if WAKE["hold"] == 0:
                self._rearm_mic_if_owed()

    def _rearm_mic_if_owed(self):
        """Put the microphone back, if we took it for a sound.

        Called when the rabbit rebinds `idle`, which is the rabbit saying the
        sound is over -- a better signal than any duration we could guess,
        and the one pacifox measured as reliable across 125 sounds (#23).
        """
        if WAKE.get("hold"):
            return
        if not WAKE.get("rearm") or not self._owns_mic():
            return
        WAKE["rearm"] = False
        if WAKE.get("on"):
            self._start_listen()

    def send_audio(self, url: str):
        """Stream + play audio from a URL (ST). The rabbit fetches it over HTTP,
        so it can be a /res/ URL on this server (HA-generated TTS, a sound…)."""
        self.send_program(f"ST {url}")

    def send_choreography(self, tempo_ms: int, actions: list):
        """Store a choreography resource and tell the rabbit to play it (CH)."""
        token = store_resource(build_choreography(tempo_ms, actions))
        self.send_program(f"CH {resource_url(token)}")

    def _play_connect_jingle(self, then_listen: bool = False,
                             release_hold: bool = False):
        """Play the configured greeting jingle once the rabbit is operational.

        `then_listen` does not start a timer. The rabbit is half-duplex and
        silent about it -- play_start() returns without a word while a
        capture is running -- so an `RS` that lands between the `ST` and the
        end of the sound makes the jingle vanish. A duration is a guess; the
        rabbit rebinding `idle` is the rabbit saying it has finished, so the
        mic is owed back from there (_rearm_mic_if_owed).
        """
        if then_listen:
            WAKE["rearm"] = True
        try:
            wav = render_jingle(CONNECT_JINGLE)
            if wav:
                self.send_program(f"ST {resource_url(store_resource(wav, _audio_ctype(wav)))}")
                log.info("rabbit %s: played connect jingle %r", self.addr[0], CONNECT_JINGLE)
        except Exception as exc:  # noqa
            log.warning("connect jingle failed: %s", exc)
        finally:
            # Always, including when the render or the send failed: a hold
            # that leaks is a rabbit whose microphone never comes back.
            if release_hold:
                WAKE["hold"] = max(0, WAKE.get("hold", 1) - 1)

    def send_state_query(self):
        """Ask the rabbit to report its internal state via the bytecode's
        `getrunningstate` ad-hoc command. The reply (sState/gSleepState/run…)
        arrives as a normal incoming stanza and is logged."""
        with self._send_lock:
            self.msg_nb += 1
            nb = self.msg_nb
        self.send(
            f"<iq type='set' from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='state-{nb}'>"
            f"<command xmlns='http://jabber.org/protocol/commands' node='getrunningstate' "
            f"action='execute'/></iq>"
        )

    def _start_listen(self):
        """Begin streaming the mic for the wake word (auto_listen).

        Deferred while a sound is playing, because that is the other half of
        the half-duplex problem: hal/audio.c's rec_start() refuses while
        play_state is set, and says nothing, so an `RS` sent mid-sound leaves
        us believing the mic is on when it is not. The rabbit's resource says
        when it is playing -- it rebinds `streaming` for the sound and `idle`
        when it is done -- so the wait is a fact rather than a delay.
        """
        WAKE["on"] = True
        self._take_mic()
        if self.resource and self.resource != "idle":
            WAKE["rearm"] = True
            log.info("auto-listen: %s is playing something, mic deferred to "
                     "its next idle", self.mac)
            _save_state()
            return
        WAKE["streaming"] = True
        WAKE["cooldown"] = 0.0
        with MIC_LOCK:
            # A new RS, a new flow: the first datagram names its sender.
            MIC_STREAM["sender"] = None
        log.info("auto-listen: starting mic stream on %s", self.mac)
        # Inside the TLS session when its runtime streams there, encrypted
        # with everything else; over UDP on the LAN otherwise.
        self.send_program("RS session" if self.rides("mic")
                          else f"RS {_server_ip()} {MIC_UDP_PORT}")
        _save_state()

    def _owns_mic(self) -> bool:
        """The microphone pipeline is one stream for the whole add-on, so it
        belongs to one rabbit at a time: the last one sent `RS`. Before
        0.19.0 any rabbit's sound stopped it, any rabbit's idle re-armed it,
        and two rabbits streaming mixed into one buffer."""
        return WAKE.get("mac") in (None, self.mac)

    def _may_take_mic(self) -> bool:
        """On connect, listen only if no other connected rabbit is the one
        listening: a second rabbit coming online does not take the mic from
        the first (/api/mic?mac= moves it deliberately)."""
        owner = WAKE.get("mac")
        if owner in (None, self.mac):
            return True
        with BUNNIES_LOCK:
            return owner not in BUNNIES

    def _take_mic(self):
        """Make this rabbit the one that listens; the previous one, if it was
        streaming, is told to stop -- a rabbit left recording plays nothing."""
        prev = WAKE.get("mac")
        if prev and prev != self.mac:
            with BUNNIES_LOCK:
                other = BUNNIES.get(prev)
            if other is not None and WAKE.get("streaming"):
                other.send_violet_packet(message_packet("RT"))
                log.info("mic: moved from %s to %s", prev, self.mac)
            WAKE["streaming"] = False
        WAKE["mac"] = self.mac
        with MIC_LOCK:
            if MIC_STREAM.get("expect") != self.addr[0]:
                MIC_STREAM["adpcm"] = bytearray()
            MIC_STREAM["expect"] = self.addr[0]

    def _should_clear_mic(self) -> bool:
        """Whether to send the `RT` that clears a stale mic stream.

        This used to run on every session. It was written for a stream left
        by a previous *server* lifetime, which is right after a restart — but
        since 0.15.7 the rabbit reconnects instead of rebooting, so "every
        session" became "every reconnect", and anything that had started the
        mic on purpose was silently stopped. pacifox reported it in #28 and
        measured the duplicate `RS` it causes.

        Once per MAC per process is what that issue suggests, and #23
        finding 6 is why that is not enough on its own:
        `stopEverythingAndResetToIdle` leaves `g_stream_on` set, so a stream
        running when the link died is still running when the rabbit comes
        back, and this `RT` is what rescues it.

        "Is this stream ours" was the test, and it is not enough either: a
        reconnect breaks the tie between what we believe and what the
        bytecode is doing. Measured in the emulator, scenario
        i23-lost-rt.txt: start the mic over the API, drop the session, and
        the `RT` that would stop it is lost with the session. The rabbit
        comes back still recording, and from there hal/audio.c's
        play_start() returns without a word on every sound — because
        rec_state is set. 64 KB of jingle downloaded, HTTP 200, nothing
        heard, no error anywhere. It does not end until something sends an
        `RT` or the rabbit is unplugged.

        So: clear, and let the re-arm below put the mic back where the user
        asked for it. #28's complaint was that a deliberate mic was silently
        stopped; it is the *silently* that was the bug, and the re-arm is what
        fixes it.

        The one exception is Le Terrier's test, which is the right one where
        it applies: do not clear a capture that is *in flight*. There, every
        capture is push-to-talk — bounded, with a known lifetime — so the
        question can always be asked. Here most listening is a steady state
        with no end in sight, and for that the re-arm is the answer. The
        bounded case does exist, though: the seconds after the wake word,
        while the command is being collected. Cancelling that from under its
        owner is #28 all over again, so it is the one stream left alone.

        What is *not* the test, and it is a tempting one: whether datagrams
        are arriving. They arrive in both cases. A rabbit left streaming by a
        dropped link keeps sending exactly like one that is streaming on
        purpose — that is the whole of #23's finding 6 — so the receiver
        cannot tell an orphan from a wanted stream. Only server-side intent
        can, which is what `capturing` records.
        """
        return not WAKE.get("capturing")

    def _stop_listen_safely(self):
        """Idempotent safety net called on every fresh XMPP bind: tell the
        rabbit to stop any orphan mic stream left over from a previous
        server lifetime (an add-on restart while listen was on leaves the
        bytecode still streaming UDP into the void, then back into the
        new receiver as a Larsen). Sending RT is harmless if the mic was
        already off."""
        try:
            self.send_program("RT")
            # We have just told the rabbit to stop, so stop believing it is
            # streaming. Without this the next sound sends a second, pointless
            # RT on its way past _free_the_speaker.
            if self._owns_mic():
                WAKE["streaming"] = False
        except Exception:
            pass

    # -- handshake --------------------------------------------------------- #
    def _stream_header(self, features: str):
        self.send(
            "<?xml version='1.0'?>"
            f"<stream:stream xmlns='jabber:client' "
            f"xmlns:stream='http://etherx.jabber.org/streams' "
            f"id='nab-{os.urandom(4).hex()}' from='{self.domain}' "
            f"version='1.0' xml:lang='en'>" + features
        )

    def _features_sasl(self):
        return (
            "<stream:features>"
            "<mechanisms xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>"
            "<mechanism>DIGEST-MD5</mechanism><mechanism>PLAIN</mechanism>"
            "</mechanisms>"
            "<register xmlns='http://violet.net/features/violet-register'/>"
            "</stream:features>"
        )

    def _features_bind(self):
        return (
            "<stream:features>"
            "<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><required/></bind>"
            "<session xmlns='urn:ietf:params:xml:ns:xmpp-session'/>"
            "</stream:features>"
        )

    def _handle_stanza(self, frag: str):
        f = frag.strip()
        if not f:
            return
        if "id='ka-" in f or 'id="ka-' in f:
            log.debug("<- rabbit %s: %s", self.addr[0], f[:400])
            return
        log.info("<- rabbit %s: %s", self.addr[0], f[:400])

        if "violet:iq:register" in f and "<iq" in f:
            # A rabbit with no stored credentials registers before it
            # authenticates, and this server advertises
            # <register xmlns='http://violet.net/features/violet-register'/>
            # in its stream features — so it was *offering* a flow it did not
            # serve. A rabbit that took the offer sent its query, got nothing
            # back and hung up, which is every rabbit whose configuration page
            # has been reset and every rabbit that was never adopted by Violet.
            #
            # Three exchanges, taken from OpenJabNab's plugin_auth.cpp (steps 1
            # and 100) rather than guessed:
            #
            #   rabbit  <iq type='get' id='1'><query xmlns='violet:iq:register'/></iq>
            #   us      ...<query><instructions>…</instructions><username/><password/></query>
            #   rabbit  <iq type='set' id='2'><query ...><username>MAC</username>
            #                                  <password>…</password></query></iq>
            #   us      the same content back, as a result
            #
            # The password is logged and not stored, deliberately: the DIGEST
            # handler below accepts any response, so keeping it would imply a
            # check that does not happen. If that handler ever starts checking,
            # this is where the credential has to be kept.
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            creds = re.search(
                r"<username>([0-9a-fA-F]*)</username>\s*<password>([0-9a-fA-F]*)</password>", f)
            if creds:
                self.mac = creds.group(1).lower() or self.mac
                log.info("rabbit %s: registered as %s (password %d hex chars, "
                         "not stored — the SASL handler accepts any response)",
                         self.addr[0], self.mac, len(creds.group(2)))
                # Rebuilt from the two hex fields rather than echoed: what we
                # send back is never markup the sender chose.
                inner = (f"<query xmlns='violet:iq:register'><username>{creds.group(1)}"
                         f"</username><password>{creds.group(2)}</password></query>")
                self.send(f"<iq type='result' id='{iq_id}' from='{self.domain}'>{inner}</iq>")
            else:
                log.info("rabbit %s: asked to register — sending the form",
                         self.addr[0])
                self.send(
                    f"<iq type='result' id='{iq_id}' from='{self.domain}'>"
                    "<query xmlns='violet:iq:register'>"
                    "<instructions>Choose a username and password to register "
                    "with this server</instructions>"
                    "<username/><password/></query></iq>"
                )
            return

        if f.startswith("<auth"):
            # DIGEST-MD5: send one challenge, then we'll accept any response.
            challenge = base64.b64encode(
                f'realm="{self.domain}",nonce="{os.urandom(6).hex()}",'
                'qop="auth",charset=utf-8,algorithm=md5-sess'.encode()
            ).decode()
            # capture username (the MAC) if present
            m = re.search(r'username="?([0-9a-fA-F]{8,})"?', f)
            if m:
                self.mac = m.group(1).lower()
            self.send(f"<challenge xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>{challenge}</challenge>")
            return

        if f.startswith("<response"):
            m = re.search(r'username="?([0-9a-fA-F]{8,})"?', f)
            if not m:
                # DIGEST-MD5 puts the username inside the base64 response.
                b64 = re.search(r">\s*([A-Za-z0-9+/=]+)\s*</response>", f)
                with contextlib.suppress(ValueError):
                    if b64:
                        m = re.search(r'username="?([0-9a-fA-F]{12})"?',
                                      base64.b64decode(b64.group(1)).decode("latin-1"))
            if m:
                self.mac = m.group(1).lower()
            if not self._alive:
                return
            # success-bypass: tell the rabbit it authenticated
            self.authed = True
            self.send("<success xmlns='urn:ietf:params:xml:ns:xmpp-sasl'/>")
            return

        if "urn:ietf:params:xml:ns:xmpp-bind" in f and "<iq" in f and "<unbind" not in f:
            # resource bind (note: <unbind> shares this namespace — exclude it so
            # it falls through to the unbind handler and doesn't reset the resource)
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            res = re.search(r"<resource>\s*([^<]*?)\s*</resource>", f)
            if res and res.group(1).strip():
                # Echoed back between quotes in every stanza we send it.
                self.resource = _token(res.group(1).strip(), self.resource)
            if self.resource == "streaming":
                self._res_played.set()        # a sound has started: the next may come
            frm = re.search(r"from='([0-9a-fA-F]{8,})@", f) or re.search(r'from="([0-9a-fA-F]{8,})@', f)
            if frm:
                self.mac = frm.group(1).lower()
            self.send(
                f"<iq type='result' id='{iq_id}'>"
                f"<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><jid>{self.jid}</jid></bind></iq>"
            )
            was_bound = self.bound
            self._register()
            self._sound_at = None
            self._nudge_from, self._nudges = time.time(), 0
            if was_bound and self.resource == "idle":
                # A rebind after a sound is not followed by a session (real
                # rabbit, 2026-10-24, every sound), so the sound being over
                # has to be read here and not in the session branch.
                self._rearm_mic_if_owed()
            return

        if "urn:ietf:params:xml:ns:xmpp-session" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            self.bound = True
            self.send(f"<iq type='result' id='{iq_id}'/>")
            log.info("rabbit %s (%s) is now bound and idle — ready for commands", self.addr[0], self.mac)
            if self.resource == "idle":
                # The rabbit is back on `idle`, which means whatever it was
                # playing is over. If we stopped its capture to let that
                # sound through, this is the moment to give it back.
                self._rearm_mic_if_owed()
                threading.Timer(2.5, self.send_state_query).start()
                # The rabbit toggles between resources ('idle' / 'streaming') on
                # the same XMPP stream, so this branch fires every ~10s. Gate the
                # one-shot greet/init on a per-session flag — a real reboot opens
                # a fresh TCP (new instance) and re-arms it.
                if not getattr(self, "_session_inited", False):
                    self._session_inited = True
                    # Belt-and-suspenders: clear any stale mic stream from a
                    # previous server lifetime before we decide what state to
                    # restore. The rabbit ignores RT if it isn't streaming.
                    if self._should_clear_mic():
                        threading.Timer(1.5, self._stop_listen_safely).start()
                    hw = volume_for(self.mac)
                    if hw is not None:
                        threading.Timer(3.0, lambda: self.send_program(f"SV {hw}")).start()
                    # AUTO_LISTEN is the *default* on a fresh install; once
                    # state.json explicitly records the listen flag, runtime
                    # toggling (/api/mic?on=0|1) wins. This stops the rabbit
                    # from re-arming the mic on every reconnect after a user
                    # turned it off (the Larsen loop from 2026-05-29).
                    want_listen = ((WAKE["on"] if _STATE_LOADED["has_listen"] else AUTO_LISTEN)
                                   and self._may_take_mic())
                    if CONNECT_JINGLE:
                        # Nothing may arm the mic between the clearing RT at
                        # 1.5 s and this jingle at 3.5 s, or the jingle has to
                        # take the speaker back off it. The hold is released
                        # once the jingle has been pushed, and the mic comes
                        # back when the rabbit says the sound is over.
                        WAKE["hold"] = WAKE.get("hold", 0) + 1
                        threading.Timer(
                            3.5,
                            lambda: self._play_connect_jingle(
                                then_listen=want_listen, release_hold=True),
                        ).start()
                    elif want_listen:
                        threading.Timer(4.0, self._start_listen).start()
            return

        if "violet:iq:sources" in f and "<iq" in f:
            # The rabbit asks for its config; answering with the init packet lets
            # it finish booting (otherwise it stays stuck in the boot resource).
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            frm = re.search(r"from='([^']*)'", f) or re.search(r'from="([^"]*)"', f)
            to = re.search(r"to='([^']*)'", f) or re.search(r'to="([^"]*)"', f)
            # Echoed between quotes: a JID, never markup (Le Terrier _JID).
            frm = frm.group(1) if frm and _JID.fullmatch(frm.group(1)) else self.jid
            to = (to.group(1) if to and _JID.fullmatch(to.group(1))
                  else f"net.violet.platform@{self.domain}/sources")
            b64 = base64.b64encode(INIT_PACKET).decode("ascii")
            self.send(
                f"<iq from='{to}' to='{frm}' type='result' id='{iq_id}'>"
                f"<query xmlns='violet:iq:sources'><packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet></query></iq>"
            )
            log.info("rabbit %s: answered violet:iq:sources (init packet) — boot should complete", self.addr[0])
            return

        if "<unbind" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            self.send(f"<iq type='result' id='{iq_id}'/>")
            self.bound = True
            log.info("rabbit %s: boot unbind done — now operational, ready for commands", self.addr[0])
            return

        if f.startswith("<iq") and "ping" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = _token(iq_id.group(1) if iq_id else "", "1")
            self.send(f"<iq type='result' id='{iq_id}'/>")
            return

        if f.startswith("<presence"):
            # CRITICAL: after binding its operational resource the rabbit sends a
            # presence and then sits in the ssPresence state WAITING to receive a
            # presence back from the server; only then does it move to ssFree —
            # the one state in which it acts on pushed iq/message commands. Without
            # this reply it stays stuck and silently ignores everything we push.
            self._presence_at = time.time()
            self.send(f"<presence from='{self.domain}' to='{self.jid}'/>")
            log.info("rabbit %s: presence <-> presence sent (rabbit should now go free)", self.addr[0])
            return

        if "violet:nabaztag:button" in f:
            # The rabbit sends <button><clic>N</clic></button> on head-button
            # presses (1=click, 2=double; long/double-long are push-to-talk record).
            mc = re.search(r"<clic>\s*(\d+)\s*</clic>", f)
            clic = int(mc.group(1)) if mc else 0
            action = {1: "click", 2: "double_click", 3: "long_click",
                      4: "double_long_click"}.get(clic, "unknown")
            log.info("rabbit %s: button clic=%d (%s)", self.addr[0], clic, action)
            fire_ha_event("nabaztag_event",
                          {"mac": self.mac, "type": "button", "clic": clic, "action": action,
                           "verified": self._verified()})
            return

        if "violet:nabaztag:rfid" in f:
            # A tag on the reader, reported inside the session instead of by
            # plain rfid.jsp (patch_rfid_xmpp.py; seal() tells a confirmed
            # rabbit to). The rabbit is the one whose key opened this TLS
            # session -- never a MAC read from the stanza. No runtime sends
            # this on the plain port, so nothing is fired there.
            mt = re.search(r"<tag>\s*([0-9A-Fa-f]{2,32})\s*</tag>", f)
            if self.tls_mac is None or self.mac != self.tls_mac:
                log.warning("rabbit %s: rfid stanza outside its TLS session, ignored", self.addr[0])
            elif mt:
                log.info("rabbit %s: RFID tag=%s inside the session", self.mac, mt.group(1))
                fire_ha_event("nabaztag_event",
                              {"mac": self.mac, "type": "rfid", "tag": mt.group(1),
                               "verified": True})
            return

        if "violet:nabaztag:ears" in f:
            # The rabbit sends <ears><left>L</left><right>R</right></ears> when the
            # ears are turned by hand (positions ~0..16).
            ml = re.search(r"<left>\s*(\d+)\s*</left>", f)
            mr = re.search(r"<right>\s*(\d+)\s*</right>", f)
            left = int(ml.group(1)) if ml else None
            right = int(mr.group(1)) if mr else None
            log.info("rabbit %s: ears moved left=%s right=%s", self.addr[0], left, right)
            fire_ha_event("nabaztag_event",
                          {"mac": self.mac, "type": "ears", "left": left, "right": right,
                           "verified": self._verified()})
            return
        # Anything else (other packets/events) -> logged above.

    def _check_sound_ack(self, at: float):
        """A sound pushed to an idle rabbit that never made it bind.

        Playing anything makes the rabbit bind `streaming`, then `idle` when
        it is done. Seen once on a real rabbit (2026-10-23): after a sound it
        bound `idle` and then applied nothing it was sent for 32 minutes,
        while still answering keepalives and the state query, until the
        add-on restarted. Dropping the stream makes it reconnect, which is
        what recovered it.

        0.16.1 read that as a bind left without its session and dropped the
        stream on that. But a rebind after a sound is never followed by a
        session -- every sound on 2026-10-24 was, and the rabbit kept playing
        the next ones -- so it dropped a healthy rabbit after each sound. The
        test here is the one that matters: we asked for a sound, nothing
        happened.
        """
        if not self._alive or self._sound_at != at:
            return
        self._sound_at = None
        log.warning("rabbit %s (mac=%s): sound pushed but no bind after %.0f s - "
                    "dropping the stream so it reconnects",
                    self.addr[0], self.mac, SOUND_ACK_S)
        self.close()

    def close(self):
        self._alive = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _register(self):
        if self.mac:
            self.resource_at = time.time()
            with BUNNIES_LOCK:
                prev = BUNNIES.get(self.mac)
                if prev is not None and prev is not self and prev.sid > self.sid:
                    stale = True            # an older socket of a rabbit that has already reconnected
                else:
                    stale = False
                    BUNNIES[self.mac] = self
                    if self._verified():
                        TLS_ANCHORS[self.mac] = self.addr[0]
                gone = LAST_SEEN.pop(self.mac, None) if not stale else None
            if stale:
                log.warning("mac=%s: bind on stale session #%d (current #%d) - closing it", self.mac, self.sid, prev.sid)
                self.close()
                return
            if prev is not None and prev is not self:
                log.info("mac=%s: new session #%d replaces #%d - closing the old socket", self.mac, self.sid, prev.sid)
                prev.close()
            if self.connected_at is None:
                self.connected_at = self.tcp_at
            if gone:
                log.info("registered bunny mac=%s from %s — back online after %s offline",
                         self.mac, self.addr[0], _ago(time.time() - gone))
            else:
                log.info("registered bunny mac=%s from %s", self.mac, self.addr[0])

    def keepalive(self):
        """A whole stanza the rabbit answers, never whitespace: Violet's
        parser drops every stanza that follows a blank left in its buffer."""
        self.send(f"<iq type='set' from='net.openjabnab.platform@{self.domain}/services' "
                  f"to='{self.mac}@{self.domain}/{self.resource}' id='ka-{next(self._ka)}'>"
                  f"<command xmlns='http://jabber.org/protocol/commands' node='getrunningstate' "
                  f"action='execute'/></iq>")

    def _keepalive_due(self, now: float):
        if KEEPALIVE_S and self.bound and self.mac and self._alive and now - self.last_tx >= KEEPALIVE_S:
            self.keepalive()

    def check_presence_nudge(self, now: float | None = None):
        """Unblock a rabbit stuck in ssPresence (Le Terrier, 2026-10-30).

        After a bind the bytecode waits to RECEIVE a presence before it acts
        on anything pushed, and we only ever sent one in answer to its own.
        One presence lost on Wi-Fi and the rabbit stays bound and deaf until
        it is unplugged: _check_sound_ack recovers that for a sound, nothing
        did for a choreography, a LED or an ambient packet. A bind with no
        presence after it gets an unsolicited one, at most NUDGE_MAX times.
        A healthy rabbit sends its presence within milliseconds and never
        sees one."""
        now = time.time() if now is None else now
        if not (self._alive and self.bound and self.mac and self._nudge_from):
            return
        if self._presence_at > self._nudge_from or self._nudges >= NUDGE_MAX:
            return
        if now - self._nudge_from < NUDGE_AFTER_S * (self._nudges + 1):
            return
        self._nudges += 1
        self.send(f"<presence from='{self.domain}' to='{self.jid}'/>")
        log.info("rabbit %s: no presence %ds into %s - sent one (nudge %d/%d)",
                 self.mac, round(now - self._nudge_from), self.resource,
                 self._nudges, NUDGE_MAX)

    def run(self):
        log.info("XMPP connection from %s%s", self.addr[0],
                 f" over TLS ({self.tls_mac})" if self.tls_mac else "")
        self.conn.settimeout(15)
        try:
            if self._first:
                self._take(self._first)
                self._first = b""
            while self._alive:
                try:
                    chunk = self.conn.recv(4096)
                except socket.timeout:
                    now = time.time()
                    if XMPP_IDLE_S and self.bound and now - self.last_rx > XMPP_IDLE_S:
                        log.warning("rabbit %s (mac=%s): nothing received for %.0f s - session dropped",
                                    self.addr[0], self.mac, now - self.last_rx)
                        break
                    if not (self.authed or self.bound) and now - self.tcp_at > PREAUTH_TIMEOUT_S:
                        # A rabbit authenticates within seconds; a socket that
                        # has not is holding a thread and a place for nothing.
                        log.warning("XMPP %s: not authenticated after %.0f s - closed",
                                    self.addr[0], now - self.tcp_at)
                        break
                    self._keepalive_due(now)
                    self.check_presence_nudge(now)
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                self.last_rx = time.time()
                self._take(chunk)
                # Here too, not only on a timeout: the rabbit writes a blank
                # of its own every few seconds, so its socket is never quiet
                # long enough for one, and the first keepalive went out after
                # 159 s -- past the 150 s after which the runtime gives up.
                self._keepalive_due(self.last_rx)
                self.check_presence_nudge(self.last_rx)
                if not (self.authed or self.bound) and self.last_rx - self.tcp_at > PREAUTH_TIMEOUT_S:
                    log.warning("XMPP %s: still not authenticated after %.0f s - closed",
                                self.addr[0], self.last_rx - self.tcp_at)
                    break
        finally:
            if self._admitted:
                _release(self.addr[0])
            if self.mac:
                with BUNNIES_LOCK:
                    if BUNNIES.get(self.mac) is self:
                        BUNNIES.pop(self.mac, None)
                        LAST_SEEN[self.mac] = time.time()
            try:
                self.conn.close()
            except OSError:
                pass
            up = _ago(time.time() - self.connected_at) if self.connected_at else "?"
            log.info("XMPP disconnect %s (mac=%s) — was online %s", self.addr[0], self.mac, up)

    def _take(self, chunk: bytes):
        """What the socket gave: the microphone's raw bytes to mic_feed, the
        rest to the XML parser. Only a session that streams its microphone
        here (rides "mic") is searched; every other one is all XML."""
        if not self._mic_left and not self._raw and not self.rides("mic"):
            self._feed(self._utf8.decode(chunk))
            return
        head = adoption.MIC_HEAD
        data, xml = self._raw + chunk, bytearray()
        self._raw = b""
        while data:
            if self._mic_left:
                part, data = data[:self._mic_left], data[self._mic_left:]
                self._mic_left -= len(part)
                got = mic_feed(part, self.addr[0])
                if got and got[0] % 50 == 1:
                    log.info("rabbit %s: mic inside the session, %d chunks, %d bytes",
                             self.mac, *got)
                continue
            i = data.find(head)
            if i == -1:
                # Keep a tail that may be the beginning of an announce.
                keep = next((k for k in range(min(len(head) - 1, len(data)), 0, -1)
                             if head.startswith(data[-k:])), 0)
                xml += data[:len(data) - keep]
                self._raw = data[len(data) - keep:]
                break
            xml += data[:i]
            end = data.find(b"'/>", i)
            if end == -1:
                if len(data) - i > len(head) + 16:
                    xml += data[i:]            # not an announce after all
                else:
                    self._raw = data[i:]
                break
            n = data[i + len(head):end]
            if not n.isdigit() or int(n) > adoption.MIC_MAX_CHUNK:
                log.warning("rabbit %s: a malformed microphone announce, closing", self.mac)
                self._alive = False
                return
            self._mic_left = int(n)
            data = data[end + 3:]
        if xml:
            self._feed(self._utf8.decode(bytes(xml)))

    def _feed(self, data: str):
        """Incremental XMPP parser: extracts complete top-level stanzas.
        Handles the (never-closed) <stream:stream> opener and restarts, the
        <?xml?> declaration, self-closing tags and full <tag>..</tag> stanzas."""
        self.buf += data
        while self._alive:
            self.buf = self.buf.lstrip()
            if not self.buf:
                break
            if self.buf.startswith("<?xml"):
                e = self.buf.find("?>")
                if e == -1:
                    break
                self.buf = self.buf[e + 2:]
                continue
            if self.buf.startswith("</stream:stream"):
                self._alive = False
                break
            if self.buf.startswith("<stream:stream"):
                e = self.buf.find(">")
                if e == -1:
                    break
                self.buf = self.buf[e + 1:]
                log.info("<- rabbit %s: stream open/restart (authed=%s)", self.addr[0], self.authed)
                self._on_stream_open()
                continue
            m = re.match(r"<([a-zA-Z0-9:_-]+)", self.buf)
            if not m:
                nxt = self.buf.find("<", 1)
                self.buf = "" if nxt == -1 else self.buf[nxt:]
                continue
            tag = m.group(1)
            ot = self.buf.find(">")
            if ot == -1:
                break
            if self.buf[:ot + 1].rstrip().endswith("/>"):
                stanza = self.buf[:ot + 1]
                self.buf = self.buf[ot + 1:]
                self._handle_stanza(stanza)
                continue
            close = "</%s>" % tag
            ci = self.buf.find(close, ot + 1)
            if ci == -1:
                break  # incomplete; wait for more
            stanza = self.buf[:ci + len(close)]
            self.buf = self.buf[ci + len(close):]
            self._handle_stanza(stanza)
        if len(self.buf) > MAX_BUFFER:
            log.warning("XMPP %s (mac=%s): %d characters without a complete stanza, "
                        "closing", self.addr[0], self.mac, len(self.buf))
            self.buf = ""
            self.close()

    def _on_stream_open(self):
        self._stream_header(self._features_bind() if self.authed else self._features_sasl())


def xmpp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", XMPP_PORT))
    srv.listen(8)
    log.info("XMPP listening on 0.0.0.0:%d (domain advertised: %s)", XMPP_PORT, SERVER_ADDRESS)
    while True:
        try:
            conn, addr = srv.accept()
            if not _admit(addr[0]):
                log.warning("XMPP %s refused: connection cap reached", addr[0])
                conn.close()
                continue
            sess = XmppSession(conn, addr)
            sess._admitted = True
            try:
                sess.start()
            except BaseException:
                _release(addr[0])
                raise
        except Exception as exc:  # noqa  (EMFILE, thread limit...) must not kill the listener
            log.error("XMPP accept failed: %s", exc)
            time.sleep(1)


# TLS 1.3 with the adoption key (adoption.tls_context); None when this Python
# cannot do an external PSK, and then no TLS port is offered to anybody.
try:
    TLS_CTX, _PSK_SEEN = adoption.tls_context(KEYS)
except RuntimeError as _exc:
    TLS_CTX, _PSK_SEEN = None, None
    log.warning("no TLS for adopted rabbits: %s", _exc)


class _Prefixed(io.RawIOBase):
    """The bytes already read off the socket, then the socket."""

    def __init__(self, first: bytes, conn):
        self._first, self._conn = first, conn

    def readable(self):
        return True

    def readinto(self, b):
        if self._first:
            n = min(len(b), len(self._first))
            b[:n] = self._first[:n]
            self._first = self._first[n:]
            return n
        return self._conn.recv_into(b)


class _Records(io.RawIOBase):
    """Writes in small slices, one TLS record each."""

    def __init__(self, conn):
        self._conn = conn

    def writable(self):
        return True

    def write(self, b):
        mv = memoryview(b)
        for i in range(0, len(mv), adoption.HTTP_RECORD_BYTES):
            self._conn.sendall(mv[i:i + adoption.HTTP_RECORD_BYTES])
        return len(mv)


def _serve_http_over_tls(conn, first: bytes, addr, mac: str):
    """One HTTP request inside a TLS session: Naboot's bootloader (1.0.0.2
    and later) asks for bc.jsp in here once its key is confirmed, proved by
    the same PSK. The handler trusts the PSK's MAC over the query's."""

    class _OverTls(BootHandler):
        tls_mac = mac

        def setup(self):
            self.connection = self.request
            self.rfile = io.BufferedReader(_Prefixed(first, conn))
            self.wfile = _Records(conn)

        def address_string(self):
            return f"tls:{addr[0]}"

    _OverTls(conn, addr, None)


def _tls_client(conn: socket.socket, addr):
    conn.settimeout(10)
    try:
        conn = TLS_CTX.wrap_socket(conn, server_side=True)
    except (ssl.SSLError, OSError) as exc:
        log.warning("TLS handshake failed from %s: %s", addr[0], exc)
        with contextlib.suppress(OSError):
            conn.close()
        return
    mac = getattr(_PSK_SEEN, "mac", None)
    log.info("TLS %s with %s, PSK identity %s", conn.version(), addr[0], mac)
    # One listener, two protocols: the first bytes decide.
    try:
        first = conn.recv(4096)
    except socket.timeout:
        first = b""            # a slow XMPP client: the session waits for it
    except OSError:
        with contextlib.suppress(OSError):
            conn.close()
        return
    if first[:4] in (b"GET ", b"HEAD"):
        log.info("HTTP over TLS from %s, PSK identity %s", addr[0], mac)
        try:
            _serve_http_over_tls(conn, first, addr, mac or "")
        except Exception as exc:  # noqa
            log.warning("HTTP over TLS from %s failed: %s", addr[0], exc)
        finally:
            with contextlib.suppress(OSError):
                conn.close()
        return
    XmppSession(conn, addr, tls_mac=mac, first=first).run()


def xmpp_tls_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", XMPP_TLS_PORT))
    srv.listen(8)
    log.info("XMPP over TLS 1.3 PSK listening on 0.0.0.0:%d", XMPP_TLS_PORT)
    def _counted(conn, addr):
        try:
            _tls_client(conn, addr)
        finally:
            _release(addr[0])

    while True:
        try:
            conn, addr = srv.accept()
            if not _admit(addr[0]):
                log.warning("TLS %s refused: connection cap reached", addr[0])
                conn.close()
                continue
            try:
                threading.Thread(target=_counted, args=(conn, addr), daemon=True).start()
            except BaseException:
                _release(addr[0])
                raise
        except Exception as exc:  # noqa
            log.error("TLS accept failed: %s", exc)
            time.sleep(1)


# --------------------------------------------------------------------------- #
# HTTP boot server (the rabbit) — /vl/bc.jsp, /vl/locate.jsp
# --------------------------------------------------------------------------- #
# Where each confirmed rabbit's TLS session last came from: the address its
# plain HTTP requests are believed from (Le Terrier's anchor), in memory.
TLS_ANCHORS: dict[str, str] = {}


def _session_of(mac: str):
    with BUNNIES_LOCK:
        return BUNNIES.get(mac)


def vouch(mac: str, ip: str, tls_mac: str | None = None, live: bool = False) -> bool:
    """Whether an HTTP request from `ip` may act for `mac` (Le Terrier's
    XmppServer.vouch).

    The rabbit's HTTP requests carry no credential, so a rabbit without a
    confirmed key cannot be told from a forger and the answer stays yes. A
    confirmed rabbit proved itself on the TLS port: its HTTP side effects
    are honoured over TLS, or from the address its TLS session uses.
    `live`: that session must also be up."""
    if tls_mac is not None:
        return tls_mac == mac
    row = KEYS.get(mac)
    if row is None or row["state"] != "confirmed":
        return True
    sess = _session_of(mac)
    tls_up = sess is not None and sess.tls_mac == mac
    if live and not tls_up:
        return False
    anchor = sess.addr[0] if tls_up else TLS_ANCHORS.get(mac)
    return anchor is not None and anchor == ip


def drop_stale_session(mac: str, ip: str, tls_mac: str | None) -> None:
    """A rabbit that fetches its bytecode has restarted its interpreter, so
    a session still held for it is a corpse (Le Terrier _drop_stale_session,
    0019db…bd28 on 2026-11-06): the socket stays open and the iq half keeps
    answering keepalives while nothing pushed into it runs.

    Only when the request comes from where that session connects from, or
    over TLS from the rabbit itself: a bc.jsp names a MAC printed under the
    rabbit, and on Le Terrier (Nabazprout, 2026-11-11) one GET from
    anywhere closing a session left the rabbit ears up until unplugged. A
    rabbit back on another address loses only the seconds until its new
    session binds and replaces the old one."""
    sess = _session_of(mac)
    if sess is None:
        return
    if tls_mac != mac and sess.addr[0] != ip:
        log.warning("rabbit %s: bytecode asked for from %s while its session connects "
                    "from %s -- session kept", mac, ip, sess.addr[0])
        return
    log.info("rabbit %s: fetched its bytecode while a session was still bound (%s) "
             "-- that session is stale, closing it", mac, sess.resource)
    sess.close()


# What a rabbit in the BOSH fallback is answered: the end of its stream (Le
# Terrier protocol.py, BOSH_TERMINATE). After three failed XMPP connects --
# every add-on restart -- the 2006 bytecode posts to /http-bind and leaves
# that mode only on a failure; the empty 200 it got here kept it there,
# deaf, until unplugged (Le Terrier: 2 h 10 min on 2026-11-10).
BOSH_TERMINATE = (b"<body type='terminate' condition='remote-connection-failed' "
                  b"xmlns='http://jabber.org/protocol/httpbind'></stream:stream></body>")
BOSH_NOTE_EVERY_S = 300
_BOSH_NOTED: dict[str, float] = {}
# A push-to-talk press is 8 kHz 4-bit ADPCM: a few tens of KB.
RECORD_MAX_BYTES = 512 * 1024
# Key exchanges: new pending keys one address may create in an hour, and
# pending keys at all. A LAN host walking MACs got one key (and one Home
# Assistant notification) each, without bound.
KX_PER_IP_PER_HOUR = 4
KX_MAX_PENDING = 16
_KX_SEEN: dict[str, list[float]] = {}
_KX_LOCK = threading.Lock()


def _kx_allowed(ip: str) -> bool:
    now = time.time()
    with _KX_LOCK:
        recent = [t for t in _KX_SEEN.get(ip, ()) if now - t < 3600]
        _KX_SEEN[ip] = recent
        return len(recent) < KX_PER_IP_PER_HOUR


def _kx_count(ip: str) -> None:
    with _KX_LOCK:
        _KX_SEEN.setdefault(ip, []).append(time.time())


class BootHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet/0.1"
    # The MAC a TLS session proved, for a request that came in over :5223;
    # None for plain HTTP.
    tls_mac = None
    # A client that stops sending does not hold a thread forever.
    timeout = 30

    def _ip(self) -> str:
        return self.client_address[0]

    def _empty(self, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_bosh(self) -> None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= 65536:
            self.rfile.read(n)
        else:
            self.close_connection = True
        ip, now = self._ip(), time.time()
        if now - _BOSH_NOTED.get(ip, 0) >= BOSH_NOTE_EVERY_S:
            _BOSH_NOTED[ip] = now
            log.warning("bosh: %s is posting to /http-bind -- its XMPP connection "
                        "failed three times; sent it back to XMPP", ip)
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(BOSH_TERMINATE)))
        self.end_headers()
        self.wfile.write(BOSH_TERMINATE)

    def _serve_kx(self, q: dict) -> None:
        """The adoption exchange: the rabbit's X25519 public key in, ours out,
        as 64 hex digits. Our private key lives for this request only."""
        mac = mac_from_query(q)
        pub = (q.get("p") or [""])[0].strip().lower()
        if mac is None or not re.fullmatch(r"[0-9a-f]{64}", pub):
            self.send_error(400, "kx.jsp wants m=<mac> and p=<64 hex digits>")
            return
        row = KEYS.get(mac)
        if row is not None and row["state"] in ("pending", "confirmed"):
            # A rabbit keeps its key from its first exchange on: a second
            # offer is somebody else, or this rabbit after its configuration
            # was wiped -- in which case the owner forgets the old key on the
            # adoption page first, deliberately.
            log.warning("kx: %s already has a %s key; a new exchange from %s was refused",
                        mac, row["state"], self.address_string())
            self.send_error(409, f"this rabbit already has a {row['state']} key")
            return
        if not _kx_allowed(self._ip()):
            log.warning("kx: %s asked for a new key for %s, past %d new keys this hour "
                        "-- refused", self._ip(), mac, KX_PER_IP_PER_HOUR)
            self.send_error(429, "too many key exchanges from this address")
            return
        if sum(1 for r in KEYS.listing() if r["state"] == "pending") >= KX_MAX_PENDING:
            log.warning("kx: %d keys already wait for an answer on the adoption page; "
                        "%s refused until some are confirmed, rejected or forgotten",
                        KX_MAX_PENDING, mac)
            self.send_error(429, "too many keys waiting for their owner")
            return
        rabbit_pub = bytes.fromhex(pub)
        priv, ours = adoption.keypair()
        try:
            key, colors = adoption.derive(priv, rabbit_pub, rabbit_pub, ours)
        except adoption.LowOrderPoint:
            log.warning("kx: %s sent a low-order public key, refused", mac)
            self.send_error(400, "unusable public key")
            return
        if not KEYS.offer(mac, key, colors):
            self.send_error(409, "this rabbit already has a key")
            return
        _kx_count(self._ip())
        log.info("kx: %s agreed a key; its belly shows %s -- confirm on the adoption page",
                 mac, adoption.describe_colors(colors))
        notify_ha("Nabaztag: confirm the adoption",
                  f"The rabbit {mac} shows five colours on its belly: "
                  f"{adoption.describe_colors(colors)}. If they match, confirm them on the "
                  f"add-on's adoption page: Settings, Add-ons, Nabaztag Terrier, "
                  f"Open Web UI.", f"nabaztag_adoption_{mac}")
        body = ours.hex().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        if path.endswith("/kx.jsp"):
            return self._serve_kx(parse_qs(p.query))
        if path.endswith("/bc.jsp"):
            q = parse_qs(p.query)
            mac = mac_from_query(q)
            if self.tls_mac is not None and mac != self.tls_mac:
                log.warning("bytecode over TLS: %s proved %s but asked for %s -- refused",
                            self.address_string(), self.tls_mac, mac)
                self.send_error(403, "not the rabbit this session proved")
                return
            row = KEYS.get(mac) if mac else None
            fw = (q.get("v") or [""])[0]
            # A rabbit with a key on record ran the exchange, so its bootloader
            # is Naboot's and has the TLS opcodes: it gets the TLS runtime, or
            # from Naboot 1.2.0.0 on the https one. Any other rabbit gets the
            # hybrid, which a stock firmware can run.
            if row is not None and BOOTCODE_HTTPS_FILE and adoption.has_https(fw):
                path_bc = BOOTCODE_HTTPS_FILE
            elif row is not None and BOOTCODE_TLS_FILE:
                path_bc = BOOTCODE_TLS_FILE
            else:
                path_bc = BOOTCODE_FILE
            try:
                with open(path_bc, "rb") as fh:
                    data = fh.read()
            except OSError:
                self.send_error(500, "bootcode missing")
                return
            confirmed = row is not None and row["state"] == "confirmed"
            if mac and vouch(mac, self._ip(), self.tls_mac):
                drop_stale_session(mac, self._ip(), self.tls_mac)
                if row is not None:
                    KEYS.note_served(mac, adoption.session_policies(data, fw)
                                     if confirmed else ())
            elif confirmed and not row["served"]:
                # Nothing recorded yet (its first boot after the owner said
                # yes, the add-on restarted in between): better than nothing.
                KEYS.note_served(mac, adoption.session_policies(data, fw))
            elif confirmed:
                # Its owner confirmed its key, and the request comes neither
                # over TLS nor from where its TLS session does. The bytecode
                # is sealed for that rabbit and useless to anyone else, so it
                # is served; but what its session may carry is not rewritten,
                # and no session is dropped (review of 2026-11-18, S5: one
                # plain GET used to switch its sounds, mic and https policies).
                log.warning("bytecode for %s asked for from %s, which is not where it "
                            "connects from -- served, nothing recorded", mac, self._ip())
            data = adoption.seal(data, row, fw)
            log.info("serving bootcode %s (%d bytes%s) to %s [%s]",
                     os.path.basename(path_bc), len(data),
                     ", TLS required, signed" if row and row["state"] == "confirmed" else "",
                     self.address_string(), p.query)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/res/"):
            token = path[len("/res/"):]
            with RES_LOCK:
                entry = RESOURCES.get(token)
            if entry is None:
                self.send_error(404, "no such resource")
                return
            ctype, data = entry[0], entry[1]
            log.info("serving resource %s (%d bytes, %s) to %s", token, len(data), ctype, self.address_string())
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/ota/"):
            # Signed .sim staged by /api/ota; the rabbit fetches it when it
            # receives the `FW <url>` program command. Plain path traversal
            # is blocked by basename() — we only serve files directly under
            # /data/ota/.
            name = os.path.basename(path[len("/ota/"):])
            full = os.path.join(OTA_DIR, name)
            if not name or not os.path.isfile(full):
                self.send_error(404, "no such firmware")
                return
            with open(full, "rb") as fh:
                data = fh.read()
            log.info("OTA: serving %s (%d bytes) to %s", name, len(data), self.address_string())
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.endswith("/locate.jsp"):
            # Exact format expected by the bytecode (from OpenJabNab's locate
            # plugin): LF line endings, and xmpp_domain MUST include the port —
            # without ":<port>" the rabbit can't open the XMPP session.
            body = (f"ping {SERVER_ADDRESS}\n"
                    f"broad {SERVER_ADDRESS}\n"
                    f"xmpp_domain {SERVER_ADDRESS}:{XMPP_PORT}\n")
            # The TLS port, to a rabbit whose owner confirmed its key. Its
            # bytecode opens it only when the firmware also holds the key;
            # stock bytecodes ignore the line.
            mac = mac_from_query(parse_qs(p.query))
            row = KEYS.get(mac) if mac else None
            if TLS_CTX is not None and row is not None and row["state"] == "confirmed":
                body += f"xmpp_tls {XMPP_TLS_PORT}\n"
            body = body.encode()
            log.info("locate -> %s for %s [%s]", SERVER_ADDRESS, self.address_string(), p.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.endswith("/rfid.jsp"):
            # An RFID/Ztamp tag was shown to the rabbit. In normal mode it reports
            # this over HTTP (not XMPP): GET /vl/rfid.jsp?sn=<mac>&t=<tag>. We fire
            # an HA event and reply 200/empty (a safe no-op; the body would be run
            # as a program — a hook to play a per-tag choreography later).
            #
            # The request carries no credential (Le Terrier _serve_rfid). A
            # confirmed rabbit served the runtime that reports tags inside its
            # TLS session never sends this, so one in its name is ignored; any
            # other confirmed rabbit's counts only while its TLS session is up
            # and from that session's address. Rabbits without a confirmed key
            # cannot be told from a forger and are served as before.
            qs = parse_qs(p.query)
            mac = mac_from_query({"sn": qs.get("sn", [""])})
            tag = qs.get("t", [""])[0]
            if not mac or not re.fullmatch(r"[0-9A-Fa-f]{2,32}", tag):
                log.info("rfid.jsp from %s without a MAC and a hex tag, ignored", self._ip())
                return self._empty()
            row = KEYS.get(mac)
            confirmed = row is not None and row["state"] == "confirmed"
            log.info("rabbit RFID tag=%s from %s (mac=%s)", tag, self._ip(), mac)
            if confirmed and "rfid" in row["served"] and self.tls_mac != mac:
                log.warning("rfid: %s reports tags inside its session, plain rfid.jsp "
                            "from %s ignored", mac, self._ip())
            elif not vouch(mac, self._ip(), self.tls_mac, live=True):
                log.warning("rfid: %s is not where %s connects from -- tag ignored",
                            self._ip(), mac)
            else:
                fire_ha_event("nabaztag_event", {"mac": mac, "type": "rfid", "tag": tag,
                                                 "verified": confirmed})
            return self._empty()
        # Catch-all for other /vl/* (ping/broad polls) — 200 + log for RE.
        log.info("unhandled boot GET %s from %s", self.path, self.address_string())
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _recording_rabbit(self, q: dict):
        """The connected rabbit a record.jsp comes from: the one named by
        sn= if it connects from this address, else the one that does. None
        when no rabbit's session connects from here -- then it is not a
        rabbit, and its audio does not reach the conversation agent (review
        of 2026-11-18, S2)."""
        ip = self._ip()
        mac = mac_from_query({"sn": q.get("sn", [""])})
        with BUNNIES_LOCK:
            sess = BUNNIES.get(mac) if mac else None
            if sess is not None and sess.addr[0] == ip:
                return sess
            return next((s for s in BUNNIES.values() if s.addr[0] == ip), None)

    def do_POST(self):
        p = urlparse(self.path)
        if p.path.endswith("/http-bind"):
            return self._serve_bosh()
        if p.path.endswith("/record.jsp"):
            # Push-to-talk: holding the rabbit's head button records the mic and
            # POSTs a RIFF WAV (IMA-ADPCM, 8 kHz mono) here. Store both the raw
            # upload and a decoded PCM WAV (for STT). This is Phase-2 voice input.
            q = parse_qs(p.query)
            bunny = self._recording_rabbit(q)
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = -1
            if bunny is None or not 0 <= length <= RECORD_MAX_BYTES:
                log.warning("record.jsp from %s refused (%s)", self._ip(),
                            "no rabbit connects from there" if bunny is None
                            else f"Content-Length {length}")
                self.close_connection = True
                return self._empty(403 if bunny is None else 413)
            data = self.rfile.read(length) if length else b""
            mode = q.get("m", [""])[0]
            pcm = ima_adpcm_to_pcm_wav(data)
            with REC_LOCK:
                # In memory only (/api/lastrecording): it was also rewritten to
                # /data on every press, wearing a Pi's card for a file nothing read.
                LAST_RECORDING.update(wav=data, pcm_wav=pcm, ts=time.time(), mode=mode,
                                      mac=bunny.mac)
            log.info("RECORDING received: %d bytes ADPCM -> %d bytes PCM (mode=%s) from %s",
                     len(data), len(pcm), mode, self.address_string())
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            # Run the voice loop off the request thread so the rabbit's POST
            # returns immediately (STT + the agent can take a few seconds).
            if VOICE_PIPELINE:
                threading.Thread(target=handle_voice, args=(pcm, bunny), daemon=True).start()
            return
        log.info("unhandled boot POST %s from %s", self.path, self.address_string())
        # Its body is not read: the connection is not reused.
        self.close_connection = True
        self._empty()


def http_boot_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), BootHandler)
    log.info("HTTP boot server on 0.0.0.0:%d (bootcode=%s)", HTTP_PORT, BOOTCODE_FILE)
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
# Control API for Home Assistant
# --------------------------------------------------------------------------- #
# The owner's page for the adoption check: the five colours the add-on
# computed, drawn as the belly shows them, and yes / no. Static; the data
# comes from /api/adoption. In the Web UI (ingress) it needs nothing; on port
# 8099 open it as /adoption?token=<the API token>.
ADOPTION_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nabaztag adoption</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1c1c1c;--card:#fff;--mute:#666;--line:#ddd;--ok:#1e7d32;--no:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--card:#1f1f1f;--mute:#aaa;--line:#333}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}
.mac{font-family:ui-monospace,monospace;font-size:15px}
.state{font-size:13px;color:var(--mute)}
.belly{display:grid;grid-template-columns:repeat(3,56px);grid-template-rows:repeat(3,56px);gap:8px;margin:16px 0}
.led{border-radius:50%;border:2px solid var(--line)}
.led span{display:block;text-align:center;font-size:11px;margin-top:60px;color:var(--mute)}
button{font:inherit;padding:10px 16px;border-radius:8px;border:0;margin:4px 8px 4px 0;cursor:pointer;color:#fff}
.yes{background:var(--ok)}.no{background:var(--no)}.forget{background:#555}
p.mute{color:var(--mute);font-size:14px}
</style></head><body><main>
<h1 id=t></h1><p class=mute id=lead></p><div id=list></div><p class=mute id=err></p>
</main><script>
const fr=(navigator.language||'').startsWith('fr');
const T=fr?{t:'Adoption des lapins',lead:"Un lapin sous Naboot affiche cinq couleurs sur son ventre après l'échange de clé. Compare-les avec celles-ci. Si elles sont identiques, confirme : le lapin passera en TLS à son prochain démarrage (débranche-le puis rebranche-le). Si elles diffèrent, refuse.",
none:"Aucun lapin n'a fait d'échange de clé. Il faut un firmware Naboot 1.0.0.0 ou plus récent.",ask:'Le ventre montre-t-il ces couleurs ?',yes:'Oui, identiques',no:'Non, elles diffèrent',forget:'Oublier cette clé',
forgetq:"Oublier la clé ? Si elle était confirmée, le lapin refusera tout bytecode jusqu'à la réinitialisation de sa configuration (bouton de la tête maintenu au branchement, puis /reset).",
st:{pending:'en attente de ta réponse',confirmed:'confirmée : TLS',rejected:'refusée'},leds:['bas','gauche','milieu','droite','haut'],notls:"Cette version de Python ne sait pas faire de TLS à clé prépartagée : pas de TLS.",noruntime:"Aucun runtime TLS fourni : le lapin restera en clair.",auth:"Ouvre cette page depuis Home Assistant (Paramètres, Modules complémentaires, Nabaztag Terrier, Ouvrir l'interface web), ou ajoute ?token=<le jeton du journal du module>."}
:{t:'Rabbit adoption',lead:'A rabbit on Naboot shows five colours on its belly after the key exchange. Compare them with these. If they are the same, confirm: the rabbit moves to TLS at its next boot (unplug it and plug it back in). If they differ, reject.',
none:'No rabbit has run the key exchange. It takes Naboot 1.0.0.0 or later.',ask:'Does the belly show these colours?',yes:'Yes, the same',no:'No, they differ',forget:'Forget this key',
forgetq:'Forget the key? If it was confirmed, the rabbit refuses every bytecode until its configuration is reset (head button held while plugging it in, then /reset).',
st:{pending:'waiting for your answer',confirmed:'confirmed: TLS',rejected:'rejected'},leds:['bottom','left','middle','right','top'],notls:'This Python cannot do pre-shared-key TLS: no TLS.',noruntime:'No TLS runtime bundled: the rabbit stays in the clear.',auth:'Open this page from Home Assistant (Settings, Add-ons, Nabaztag Terrier, Open Web UI), or add ?token=<the token from the add-on log>.'};
document.getElementById('t').textContent=T.t;document.getElementById('lead').textContent=T.lead;document.title=T.t;
const tok=new URLSearchParams(location.search).get('token')||'';
const H=tok?{'X-API-Token':tok}:{};
const POS={4:[1,2],1:[2,1],2:[2,2],3:[2,3],0:[3,2]};
function el(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
async function act(a,k){if(a==='forget'&&!confirm(T.forgetq))return;
 const r=await fetch('api/adoption/'+a,{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),body:JSON.stringify({mac:k.mac,fingerprint:k.fingerprint})});
 if(!r.ok){document.getElementById('err').textContent=(await r.json()).error||r.status}load()}
async function load(){const r=await fetch('api/adoption',{headers:H});if(!r.ok){document.getElementById('err').textContent=T.auth;return}const d=await r.json();const L=document.getElementById('list');L.replaceChildren();
 const err=[];if(!d.tls)err.push(T.notls);if(!d.tls_runtime)err.push(T.noruntime);document.getElementById('err').textContent=err.join(' ');
 if(!d.keys.length){L.append(el('p','mute',T.none));return}
 for(const k of d.keys){const c=el('div','card');c.append(el('div','mac',k.mac.match(/../g).join(':')),el('div','state',T.st[k.state]||k.state));
  const b=el('div','belly');k.rgb.forEach((rgb,i)=>{const l=el('div','led');l.style.background=rgb;l.style.gridRow=POS[i][0];l.style.gridColumn=POS[i][1];l.title=T.leds[i];b.append(l)});c.append(b);
  if(k.state==='pending'){c.append(el('p',null,T.ask));const y=el('button','yes',T.yes);y.onclick=()=>act('confirm',k);const n=el('button','no',T.no);n.onclick=()=>act('reject',k);c.append(y,n)}
  const f=el('button','forget',T.forget);f.onclick=()=>act('forget',k);c.append(f);L.append(c)}}
load();
</script></body></html>
"""


_TOKEN_IN_URL = re.compile(r"([?&]token=)[^&\s]*")
# Bodies the API reads: an audio file for /api/play, a .sim for /api/ota.
API_MAX_BODY = 16 << 20


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet-api/0.1"
    timeout = 30

    def log_message(self, fmt, *args):
        # The request line carries ?token= when a rest_command puts it there.
        log.debug("API %s - %s", self.address_string(),
                  _TOKEN_IN_URL.sub(r"\1…", fmt % args))

    def _from_ingress(self) -> bool:
        return self.client_address[0] == INGRESS_PEER

    def _authorized(self, q) -> bool:
        if self._from_ingress():
            return True
        got = self.headers.get("X-API-Token") or q.get("token", [""])[0]
        return bool(got) and hmac.compare_digest(got.encode(), API_TOKEN.encode())

    def _adoption_page(self):
        body = ADOPTION_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bunny(self, q):
        mac = (q.get("mac", [None])[0] or "").replace(":", "").replace("-", "").lower()
        with BUNNIES_LOCK:
            if mac and mac in BUNNIES:
                return BUNNIES[mac]
            if not mac and len(BUNNIES) == 1:
                return next(iter(BUNNIES.values()))
        return None

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        path = p.path.rstrip("/")

        # The adoption page is static (its data comes from /api/adoption), and
        # it is what the Web UI opens through ingress.
        if path == "/adoption" or (path == "" and self._from_ingress()):
            return self._adoption_page()

        if not self._authorized(q):
            self.close_connection = True
            return self._json(401, {"error": "unauthorized: send the add-on's API token as "
                                             "the X-API-Token header (it is in the add-on's "
                                             "log), or use its Web UI"})
        # A page on another site can make the browser send a GET -- an <img>
        # at /api/mic?on=1 -- but not read the token; a browser that says the
        # request is cross-site is refused even so.
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self._json(403, {"error": "cross-site request"})

        if path in ("", "/api", "/api/status"):
            now = time.time()
            with BUNNIES_LOCK:
                conn = {m: {"addr": s.addr[0], "bound": s.bound, "resource": s.resource,
                            "session": s.sid, "alive": s._alive,
                            "last_rx_ago": round(now - s.last_rx, 1),
                            "resource_since": s.resource_at,
                            "connected_since": s.connected_at,
                            "uptime": _ago(now - s.connected_at) if s.connected_at else None}
                        for m, s in BUNNIES.items()}
                seen = {m: {"epoch": ts, "offline_for": _ago(now - ts)}
                        for m, ts in LAST_SEEN.items() if m not in conn}
            with REC_LOCK:
                rec = {"ts": LAST_RECORDING["ts"], "mode": LAST_RECORDING["mode"],
                       "mac": LAST_RECORDING["mac"],
                       "pcm_bytes": len(LAST_RECORDING["pcm_wav"] or b"")}
            with MIC_LOCK:
                mic = {"packets": MIC_STREAM["packets"], "bytes": len(MIC_STREAM["adpcm"]),
                       "ts": MIC_STREAM["ts"], "src": MIC_STREAM["src"],
                       "mac": WAKE.get("mac")}
            return self._json(200, {"server_address": SERVER_ADDRESS,
                                    "online": bool(conn), "bunnies": conn,
                                    "last_seen": seen,
                                    "last_recording": rec, "mic_stream": mic})

        if path == "/api/adoption":
            return self._json(200, {"keys": KEYS.listing(), "tls": TLS_CTX is not None,
                                    "tls_port": XMPP_TLS_PORT,
                                    "tls_runtime": bool(BOOTCODE_TLS_FILE),
                                    "https_runtime": bool(BOOTCODE_HTTPS_FILE)})

        if path in ("/api/adoption/confirm", "/api/adoption/reject", "/api/adoption/forget"):
            # A decision that hands the rabbit to whoever holds the key: POST
            # with a JSON body only, which a page on another site cannot send
            # without the browser asking this server first (and it never says
            # yes). The fingerprint ties the answer to the colours shown.
            if self.command != "POST" or not (self.headers.get("Content-Type") or "").startswith(
                    "application/json"):
                return self._json(405, {"error": "POST a JSON body"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                req = json.loads(self.rfile.read(n) or b"{}")
                mac = str(req.get("mac") or "").replace(":", "").lower()
                fp = str(req.get("fingerprint") or "")
            except (ValueError, AttributeError):
                return self._json(400, {"error": "bad JSON"})
            if not _MAC_RE.fullmatch(mac):
                return self._json(400, {"error": "mac: twelve hex digits"})
            action = path.rsplit("/", 1)[1]
            if action == "forget":
                ok = KEYS.forget(mac)
            else:
                if not fp:
                    return self._json(400, {"error": "fingerprint required"})
                ok = KEYS.decide(mac, action == "confirm", fp)
            if not ok:
                return self._json(409, {"error": "no pending key with that fingerprint"
                                        if action != "forget" else "no key for that rabbit"})
            log.info("adoption: %s %s by the owner", mac,
                     {"confirm": "confirmed", "reject": "rejected", "forget": "forgotten"}[action])
            if action == "confirm":
                log.info("adoption: %s gets the signed TLS runtime at its next boot; "
                         "unplug it and plug it back in", mac)
            return self._json(200, {"ok": True, "keys": KEYS.listing()})

        if path == "/api/micwav":
            # Debug: the live mic-stream buffer decoded to a PCM WAV.
            with MIC_LOCK:
                raw = bytes(MIC_STREAM["adpcm"])
            wav = adpcm_stream_to_pcm_wav(raw)
            if not wav:
                return self._json(404, {"error": "no mic audio buffered"})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
            return

        if path == "/api/lastrecording":
            # Fetch the last push-to-talk recording. ?format=pcm (default,
            # 16-bit PCM WAV for STT) or ?format=adpcm (the raw upload).
            want_adpcm = q.get("format", ["pcm"])[0] == "adpcm"
            with REC_LOCK:
                wav = LAST_RECORDING["wav"] if want_adpcm else LAST_RECORDING["pcm_wav"]
            if not wav:
                return self._json(404, {"error": "no recording yet"})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
            return

        if path == "/api/jingle" and not q.get("name"):
            # List the bundled jingles (works without a connected rabbit).
            return self._json(200, {"jingles": list_jingles()})

        body = b""
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= API_MAX_BODY:
                self.close_connection = True
                return self._json(413, {"error": f"body over {API_MAX_BODY} bytes"})
            if length:
                body = self.rfile.read(length)

        if path == "/api/ota":
            # Stage a SIGNED .sim and (optionally) tell the rabbit to fetch
            # + flash it. Bunny is only required when ?trigger=1.
            #
            # Input:
            #   - multipart upload (Content-Type: multipart/form-data; the
            #     field name is "sim"), OR
            #   - ?path=/server-side/path/to.signed.sim (file already on host).
            #
            # Query:
            #   ?name=basename.sim   override staged filename
            #   ?trigger=1           push 'FW <url>' to the rabbit (requires
            #                        a connected bunny + custom firmware that
            #                        understands the FW command).
            ctype = self.headers.get("Content-Type", "")

            def _one(name, default=None):
                return q.get(name, [default])[0]

            blob = b""
            if "multipart/form-data" in ctype and body:
                # Crude multipart parser — we only support a single field.
                try:
                    boundary = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip()
                    sep = ("--" + boundary).encode()
                    for part in body.split(sep):
                        if b'name="sim"' not in part:
                            continue
                        h_end = part.find(b"\r\n\r\n")
                        if h_end < 0:
                            continue
                        blob = part[h_end + 4 :].rstrip(b"\r\n-")
                        break
                except (IndexError, ValueError):
                    blob = b""
            else:
                # Only from Home Assistant's /share or the add-on's own OTA
                # folder: any readable file with the two markers used to be
                # copied out and served on port 80 to anyone.
                p_in = _one("path")
                real = os.path.realpath(p_in) if p_in else ""
                if real and not any(real.startswith(os.path.realpath(d) + os.sep)
                                    for d in OTA_SOURCES):
                    return self._json(400, {"error": "path: a file under /share or "
                                                     f"{OTA_DIR} only"})
                if real and os.path.isfile(real):
                    with open(real, "rb") as fh:
                        blob = fh.read()
            if not blob:
                return self._json(400, {"error": "no .sim payload (use multipart 'sim' field or ?path=)"})
            if b"-violet-" not in blob or b"-sig-" not in blob:
                return self._json(400, {"error": "not a signed .sim (missing -violet- / -sig- markers)"})
            name = _one("name", "firmware.signed.sim") or ""
            if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,100}\.sim", name):
                return self._json(400, {"error": "name: letters, digits, . _ - and .sim"})
            os.makedirs(OTA_DIR, exist_ok=True)
            target = os.path.join(OTA_DIR, name)
            with open(target, "wb") as fh:
                fh.write(blob)
            staged_url = f"http://{SERVER_ADDRESS}/ota/{name}"
            log.info("OTA: staged %s (%d bytes) → %s", name, len(blob), staged_url)
            triggered = False
            warn = None
            if _one("trigger") in ("1", "true", "yes"):
                b = self._bunny(q)
                if b is None:
                    warn = "trigger requested but no connected bunny — staged only"
                else:
                    image = None
                    if b.rides("res") and b.rides("ota"):
                        # Down its TLS session, as the plain image and its
                        # signature; nobody on the LAN sees or swaps it.
                        try:
                            image, sig = adoption.sim_image(blob)
                        except ValueError as exc:
                            log.warning("OTA: %s not sent inside the session: %s", name, exc)
                    if image is not None:
                        b.send_firmware(image, sig)
                        log.info("OTA: pushed %s to %s inside its session", name, b.mac)
                    else:
                        b.send_program(f"FW {staged_url}")
                        log.info("OTA: pushed FW %s to %s", staged_url, b.mac)
                    triggered = True
            return self._json(200, {
                "ok": True,
                "staged": target,
                "url": staged_url,
                "bytes": len(blob),
                "triggered": triggered,
                **({"warn": warn} if warn else {}),
            })

        b = self._bunny(q)
        if b is None:
            return self._json(404, {"error": "no connected bunny (give ?mac=)"})

        def _one(name, default=None):
            return q.get(name, [default])[0]

        try:
            if path == "/api/raw":
                b.send_violet_packet(base64.b64decode(_one("b64", "")))
            elif path == "/api/program":
                # ?text=ST%20http://... — newline OR '|' separated commands.
                b.send_program(_one("text", "").replace("|", "\n"))
            elif path == "/api/say":
                # Local TTS: ?text=... (&voice=fr&speed=160&pitch=50). Speaks on
                # the rabbit via espeak-ng → WAV → stream. ?wait=1 blocks (MW).
                text = _one("text", "")
                if not text:
                    return self._json(400, {"error": "give ?text="})
                if "[pause" in text:
                    # honor [pause]/[pause N] markers as real silences (threaded:
                    # _speak blocks for the whole phrase)
                    threading.Thread(target=_speak, args=(b, text), daemon=True).start()
                else:
                    wav = synth_tts(text, _one("voice", "fr"),
                                    int(_one("speed", "160")), int(_one("pitch", "50")))
                    url = resource_url(store_resource(wav, _audio_ctype(wav)))
                    prog = f"ST {url}"
                    if _one("wait") in ("1", "true", "yes"):
                        prog += "\nMW"
                    b.send_program(prog)
            elif path == "/api/play":
                # Audio: ?url=<mp3/wav> streamed via ST, OR POST the audio body
                # (stored + served from /res/ and streamed). ?wait=1 blocks (MW).
                url = _one("url")
                if not url and body:
                    ctype = self.headers.get("Content-Type", "audio/mpeg")
                    url = resource_url(store_resource(body, ctype))
                if not url:
                    return self._json(400, {"error": "give ?url= or POST audio body"})
                prog = f"ST {url}"
                if _one("wait") in ("1", "true", "yes"):
                    prog += "\nMW"
                b.send_program(prog)
            elif path == "/api/jingle":
                # Play an original Nabaztag jingle: ?name=<jingle> (see ?name=list
                # or GET /api/jingle). Rendered from the firmware-extracted MIDI.
                wav = render_jingle(_one("name", ""))
                if wav is None:
                    return self._json(404, {"error": "unknown jingle",
                                            "available": list_jingles()})
                url = resource_url(store_resource(wav, _audio_ctype(wav)))
                prog = f"ST {url}"
                if _one("wait") in ("1", "true", "yes"):
                    prog += "\nMW"
                b.send_program(prog)
            elif path == "/api/volume":
                # ?level=0..100 (100 = loudest). Mapped to the VS1003 inverted
                # attenuation (0 = loud .. higher = quiet) and sent as the bytecode
                # SV command. Stored so it's re-applied on reconnect (the rabbit
                # resets its volume on reboot).
                lvl = max(0, min(100, int(_one("level", "80"))))
                # Per rabbit: each one's own, re-applied when it reconnects.
                hw = round((100 - lvl) * 1.2)
                VOLUME["by_mac"][b.mac] = hw
                b.send_program(f"SV {hw}")
                _save_state()
            elif path == "/api/ears":
                # Real ear positioning via a choreography motor action.
                # ?left=&right= positions (~0..16); ?dir=0|1 rotation direction.
                both = _one("angle", "0")
                left = int(_one("left", both)); right = int(_one("right", both))
                d = int(_one("dir", "0"))
                b.send_choreography(200, [
                    (0, "motor", (EAR_NAMES["left"], left * 18, d)),
                    (0, "motor", (EAR_NAMES["right"], right * 18, d)),
                ])
            elif path == "/api/earwiggle":
                # The AmbientPacket "ears" effect: a wiggle back to home + a beep
                # (the original "your friend moved their ears" feature).
                b.send_violet_packet(ambient_packet({SVC_EAR_LEFT: int(_one("left", "1")),
                                                      SVC_EAR_RIGHT: int(_one("right", "1"))}))
            elif path == "/api/led":
                # Full RGB on one of the 5 LEDs via a choreography.
                # ?led=bottom|left|middle|right|top &r=&g=&b=
                led = _one("led", "bottom").lower()
                # A name the hardware does not have is a mistake, not a
                # request for LED 0. `led=center` and `led=nose` are natural
                # guesses — the names are bottom|left|middle|right|top — and
                # silently lighting the belly instead taught at least one
                # person that the API was broken. Reported in #28.
                if led in LED_NAMES:
                    lid = LED_NAMES[led]
                elif led.isdigit() and 0 <= int(led) <= 4:
                    lid = int(led)
                else:
                    self.send_error(
                        400, "unknown led %r — use %s, or 0-4"
                        % (led, "|".join(sorted(LED_NAMES))))
                    return
                r = int(_one("r", "0")); g = int(_one("g", "0")); bl = int(_one("b", "0"))
                b.send_choreography(200, [(0, "led", (lid, r, g, bl))])
            elif path == "/api/choreography":
                # ?spec=tempo,time,order,p3,p4,p5,p6,... (OpenJabNab comma form).
                tempo, actions = parse_choreography_spec(_one("spec", ""))
                b.send_choreography(tempo, actions)
            elif path == "/api/weather":
                # ?v=sun|cloudy|smog|rain|snow|storm (the belly icons)
                v = _one("v", "sun").lower()
                wv = WEATHER.get(v, int(v) if v.isdigit() else 0)
                b.send_violet_packet(ambient_packet({SVC_WEATHER: wv}))
            elif path == "/api/nose":
                # ?v=0 none / 1 blink / 2 double-blink
                b.send_violet_packet(ambient_packet({SVC_NOSE: int(_one("v", "1"))}))
            elif path == "/api/bottomled":
                # Bottom belly LED via AmbientPacket (palette index, no fetch).
                b.send_violet_packet(ambient_packet({SVC_BOTTOMLED: int(_one("v", "0"))}))
            elif path == "/api/ambient":
                # generic AmbientPacket: ?svc=<id>&val=<v> (repeatable)
                svcs = {int(s): int(v) for s, v in zip(q.get("svc", []), q.get("val", []))}
                b.send_violet_packet(ambient_packet(svcs or {SVC_NOSE: 1}))
            elif path == "/api/sleep":
                on = 1 if _one("on", "1") in ("1", "true", "yes") else 0
                b.send_violet_packet(frame_packet(PKT_SLEEP, bytes([on])))
            elif path == "/api/state":
                # Ask the rabbit to report its XMPP/run state (reply is logged).
                b.send_state_query()
            elif path == "/api/mic":
                # Start/stop the mic stream + wake word (hybrid bytecode only).
                if _one("on", "1") in ("1", "true", "yes"):
                    with MIC_LOCK:
                        MIC_STREAM["adpcm"] = bytearray()
                        MIC_STREAM["packets"] = 0
                    # Through _start_listen, not its own RS: that is where the
                    # mic waits for a sound to finish instead of being refused
                    # by it in silence.
                    b._start_listen()
                else:
                    owner = _mic_owner()
                    WAKE["on"] = False
                    WAKE["streaming"] = False
                    WAKE["rearm"] = False
                    b.send_program("RT")
                    if owner is not None and owner is not b:
                        owner.send_program("RT")      # the one actually listening
                    _save_state()
            elif path == "/api/personality":
                # Trigger one personality action now (test / manual). Threaded so
                # the call returns immediately.
                threading.Thread(target=_personality_action, args=(b,), daemon=True).start()
            else:
                return self._json(404, {"error": "unknown endpoint"})
        except Exception as exc:  # noqa
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"ok": True, "mac": b.mac})


def api_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", API_PORT), ApiHandler)
    log.info("Control API on 0.0.0.0:%d", API_PORT)
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
# UDP microphone-stream receiver (Phase 3) — the hybrid bytecode streams 8 kHz
# IMA-ADPCM as "snd"-prefixed datagrams here after an RS command.
# --------------------------------------------------------------------------- #
# "expect": the address of the rabbit that was sent `RS` (_take_mic); audio
# from anywhere else is dropped. "sender": the (address, port) the current
# UDP flow came from, the first datagram after `RS` deciding.
MIC_STREAM = {"adpcm": bytearray(), "packets": 0, "ts": 0.0, "src": None,
              "expect": None, "sender": None}
MIC_LOCK = threading.Lock()
# What the buffer may hold while nothing drains it: the wake loop does not
# while it transcribes (up to 120 s), asks the agent (60 s) or speaks. 1 MiB
# of 4-bit 8 kHz ADPCM is over four minutes (Le Terrier mic.py, MAX_BUFFER).
MIC_MAX_BUFFER = 1 << 20


_HOST_IP_CACHE = {"ip": None}


def _host_lan_ip():
    """The HA host's primary LAN IP — the address the rabbit must actually reach
    for UDP mic / audio (the add-on's ports are published on the host). When
    `server_address` is a `.local` name (mDNS) the bridge container can't
    gethostbyname it, so ask the Supervisor for the host's default interface."""
    if _HOST_IP_CACHE["ip"]:
        return _HOST_IP_CACHE["ip"]
    tok = _supervisor_token()
    if not tok:
        return None
    try:
        req = urllib.request.Request("http://supervisor/network/info",
                                     headers={"Authorization": f"Bearer {tok}"})
        itfs = json.loads(urllib.request.urlopen(req, timeout=5).read().decode()) \
            .get("data", {}).get("interfaces", [])
        # prefer the primary/enabled interface, else any with an IPv4
        for want_primary in (True, False):
            for itf in itfs:
                if want_primary and not itf.get("primary"):
                    continue
                addrs = (itf.get("ipv4") or {}).get("address") or []
                if addrs:
                    _HOST_IP_CACHE["ip"] = addrs[0].split("/")[0]
                    return _HOST_IP_CACHE["ip"]
    except Exception as exc:  # noqa
        log.warning("host IP lookup failed: %s", exc)
    return None


def _server_ip():
    try:
        ip = socket.gethostbyname(SERVER_ADDRESS)
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    # a `.local` (or otherwise unresolvable) server_address: hand the rabbit the
    # host's LAN IP it can actually reach, not the unresolved name.
    return _host_lan_ip() or SERVER_ADDRESS


def udp_mic_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", MIC_UDP_PORT))
    except OSError as exc:
        log.warning("UDP mic: cannot bind :%d (%s)", MIC_UDP_PORT, exc)
        return
    log.info("UDP mic receiver on 0.0.0.0:%d", MIC_UDP_PORT)
    last_log = 0.0
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except OSError:
            continue
        if data[:3] != b"snd":
            continue
        got = mic_feed(data[3:], addr[0], sender=addr[:2])
        now = time.time()
        if got and now - last_log > 2:
            log.info("UDP mic: %d snd datagrams, %d bytes (from %s)", *got, addr[0])
            last_log = now


def mic_feed(data: bytes, src: str, sender=None):
    """IMA-ADPCM from the rabbit's microphone, by UDP or inside its TLS
    session: (packets, bytes) so far, or None when it was dropped.

    Only from the rabbit that was sent `RS`, and over UDP only from the
    first (address, port) after it: the port is open to the LAN and the
    magic is three bytes, so anybody could otherwise speak a wake word and
    a command into the conversation agent (review of 2026-11-18, S3; Le
    Terrier mic.py keeps the first sender the same way)."""
    if not WAKE["on"]:
        # Belt + suspenders: even if the rabbit didn't honor our RT for
        # some reason, the server-side wake pipeline (STT, hallucination
        # filter) stays idle when listening is off. Don't accumulate
        # bytes we'd just throw away — and don't log them as ongoing
        # mic activity either.
        return None
    with MIC_LOCK:
        if src != MIC_STREAM["expect"]:
            return None
        if sender is not None:
            first = MIC_STREAM["sender"] = MIC_STREAM["sender"] or tuple(sender)
            if first != tuple(sender):
                return None
        if len(MIC_STREAM["adpcm"]) < MIC_MAX_BUFFER:
            MIC_STREAM["adpcm"] += data
        MIC_STREAM["packets"] += 1
        MIC_STREAM["ts"] = time.time()
        MIC_STREAM["src"] = src
        return MIC_STREAM["packets"], len(MIC_STREAM["adpcm"])


# --------------------------------------------------------------------------- #
# Phase 3B — wake word on the mic stream: transcribe rolling windows; when the
# wake word ("nabi") is heard, send the rest to the conversation agent.
# --------------------------------------------------------------------------- #
# "rearm": we stopped the capture to let a sound through and owe it back once
# the rabbit is idle again. "hold": a caller is playing several sounds in a row
# and does not want the mic back between them.
WAKE = {"on": False, "cooldown": 0.0, "streaming": False, "rearm": False, "hold": 0,
        # True only while the wake path is collecting one command: a bounded
        # operation, as opposed to "streaming", which is a steady state.
        "capturing": False,
        # The rabbit the microphone belongs to (XmppSession._take_mic).
        "mac": None}
# Last volume (raw VS1003 attenuation, 0=loud..higher=quiet) — re-applied on
# reconnect since the rabbit resets it on reboot. None = never set (leave default).
# Per MAC since 0.19.0; "hw" is the one value state.json held before, still
# the default for a rabbit that was never given its own.
VOLUME = {"hw": None, "by_mac": {}}


def volume_for(mac):
    return VOLUME["by_mac"].get(mac, VOLUME["hw"])

# Persist passive-listening + volume across add-on restarts (otherwise toggling
# the listen switch / setting the volume is lost on every restart).
_STATE_FILE = os.environ.get("STATE_FILE", "/data/state.json")


def _save_state():
    """state.json, atomically, and only when it changed: it was rewritten on
    every mic re-arm, after every sound (SD/eMMC wear on Pi-class hosts), and
    in place, so a stop in the middle of a write lost it."""
    state = {"listen": bool(WAKE["on"]), "volume_hw": VOLUME["hw"],
             "volume_by_mac": dict(VOLUME["by_mac"])}
    with _STATE_SAVE_LOCK:
        if state != _STATE_SAVED.get("last"):
            tmp = _STATE_FILE + ".tmp"
            try:
                with open(tmp, "w") as fh:
                    json.dump(state, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, _STATE_FILE)
                _STATE_SAVED["last"] = state
            except OSError as exc:
                log.warning("state save failed: %s", exc)
    # The listen flag is now recorded, whatever the file on disk said when
    # this process started. Without this, a mic turned on through
    # /api/mic?on=1 is not re-armed after a reconnect, because the re-arm
    # reads _STATE_LOADED["has_listen"] and that only became true by loading
    # a file. Measured in the emulator: the rabbit comes back still
    # recording, nothing re-arms it and nothing clears it.
    _STATE_LOADED["has_listen"] = True


_STATE_SAVED: dict = {}
_STATE_SAVE_LOCK = threading.Lock()
_STATE_LOADED = {"yes": False, "has_listen": False}


def _load_state():
    try:
        with open(_STATE_FILE) as fh:
            s = json.load(fh)
        _STATE_LOADED["yes"] = True
        if "listen" in s:
            _STATE_LOADED["has_listen"] = True
            WAKE["on"] = bool(s["listen"])
        if isinstance(s.get("volume_hw"), int):
            VOLUME["hw"] = s["volume_hw"]
        if isinstance(s.get("volume_by_mac"), dict):
            VOLUME["by_mac"] = {m: v for m, v in s["volume_by_mac"].items()
                                if isinstance(m, str) and isinstance(v, int)}
        log.info("state loaded: listen=%s volume_hw=%s", WAKE["on"], VOLUME["hw"])
    except (OSError, ValueError):
        pass


_load_state()


def adpcm_stream_to_pcm_wav(raw: bytes) -> bytes:
    """Wrap the raw 256-byte-block IMA-ADPCM stream from the rabbit in a WAV
    header, then decode to 16-bit PCM."""
    import struct
    raw = raw[:(len(raw) // 256) * 256]
    if not raw:
        return b""
    fmt = struct.pack("<HHIIHH", 0x11, 1, 8000, 4055, 256, 4) + struct.pack("<HH", 2, 505)
    wav = (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(raw)) + b"WAVE" +
           b"fmt " + struct.pack("<I", len(fmt)) + fmt +
           b"data" + struct.pack("<I", len(raw)) + raw)
    return ima_adpcm_to_pcm_wav(wav)


def _strip_wake(text: str) -> str:
    t = text
    for w in ("hey nabi", "ok nabi", "eh nabi", "nabi", "navi"):
        t = t.replace(w, " ")
    return re.sub(r"\s+", " ", t).strip(" ,.!?…")


def wake_loop():
    """Consume mic-stream windows; on the wake word, send the rest to the agent.
    The rabbit is half-duplex (it can't record and play at once), so before
    speaking a reply we stop the mic (RT), then re-arm it (RS) after a cooldown."""
    while True:
        time.sleep(WAKE_WINDOW_S)
        now = time.time()
        b = _mic_owner()
        # Re-arm the mic once the reply cooldown has elapsed. Through
        # _start_listen, and not while a sound is being set up (`hold`) or one
        # is owed back (`rearm`): this loop used to send its own RS, which is
        # how a re-arm could land between the clearing RT and the connect
        # jingle and cost four mic programs where one does -- the duplicate RS
        # pacifox measured in #28.
        if (WAKE["on"] and not WAKE["streaming"] and now >= WAKE["cooldown"]
                and b is not None
                and not WAKE.get("hold") and not WAKE.get("rearm")):
            with MIC_LOCK:
                MIC_STREAM["adpcm"] = bytearray()
            b._start_listen()
        if not WAKE["on"] or not WAKE["streaming"] or now < WAKE["cooldown"]:
            with MIC_LOCK:
                MIC_STREAM["adpcm"] = bytearray()
            continue
        with MIC_LOCK:
            chunk = bytes(MIC_STREAM["adpcm"])
            MIC_STREAM["adpcm"] = bytearray()
        if len(chunk) < 4000:  # < ~0.5 s of audio
            continue
        pcm_wav = adpcm_stream_to_pcm_wav(chunk)
        if _wav_peak_rms(pcm_wav) < WAKE_VAD_RMS:  # near silence — skip STT (CPU)
            continue
        try:
            text = stt_transcribe(pcm_wav).lower()
        except Exception as exc:  # noqa
            log.warning("wake: STT error %s", exc)
            continue
        if not text:
            continue
        log.info("wake: heard %r", text)
        if not _WAKE_PAT.search(text):
            continue
        # Wake heard — capture the rest of the command, but stop early once the
        # user pauses (~0.8 s of quiet) instead of always waiting a fixed time:
        # snappier for short commands, still safe (up to WAKE_CAPTURE_MAX_S) for
        # long ones. The buffer was just swapped, so it now collects only the
        # command continuation.
        quiet = 0
        WAKE["capturing"] = True
        deadline = time.time() + WAKE_CAPTURE_MAX_S
        while time.time() < deadline:
            time.sleep(0.4)
            with MIC_LOCK:
                recent = bytes(MIC_STREAM["adpcm"][-1600:])
            if recent and _wav_peak_rms(adpcm_stream_to_pcm_wav(recent)) >= WAKE_VAD_RMS:
                quiet = 0
            else:
                quiet += 1
                if quiet >= 2:
                    break
        WAKE["capturing"] = False
        with MIC_LOCK:
            tail = bytes(MIC_STREAM["adpcm"])
            MIC_STREAM["adpcm"] = bytearray()
        if tail:
            try:
                text = stt_transcribe(adpcm_stream_to_pcm_wav(chunk + tail)).lower() or text
            except Exception:  # noqa
                pass
        cmd = _strip_wake(text)
        log.info("wake: TRIGGERED — command %r", cmd)
        if b is None:
            continue
        # Half-duplex: stop the mic so the speaker is free; Claude's latency is the
        # natural gap before playback. Mic re-arms after the cooldown.
        b.send_program("RT")
        WAKE["streaming"] = False
        WAKE["cooldown"] = now + 15
        # "Got it" chime in the now-silent gap before the reply (mic already off).
        if WAKE_CHIME:
            try:
                cw = render_jingle(WAKE_CHIME)
                if cw:
                    b.send_program(f"ST {resource_url(store_resource(cw, _audio_ctype(cw)))}")
            except Exception as exc:  # noqa
                log.warning("wake: chime failed %s", exc)
        reply = conversation_ask((VOICE_PROMPT or "") + cmd) if (CONVERSATION_AGENT and cmd) else (cmd or "Oui ?")
        reply = run_action_tags(b, reply or "Oui ?")
        log.info("wake: speaking %r", reply)
        dur = 4.0
        if reply.strip():
            try:
                dur = _speak(b, reply) or 4.0
            except Exception as exc:  # noqa
                log.warning("wake: reply failed %s", exc)
        # Re-arm only after the reply has surely finished (it plays AFTER the
        # rabbit fetches it + does the streaming-resource dance — add margin).
        WAKE["cooldown"] = time.time() + dur + 6


# --------------------------------------------------------------------------- #
def _mic_owner():
    """The connected rabbit the microphone belongs to; before any has been
    given it, the first one (auto_listen on a fresh start)."""
    with BUNNIES_LOCK:
        if WAKE.get("mac"):
            return BUNNIES.get(WAKE["mac"])
        return next(iter(BUNNIES.values()), None)


# Vivid palette for the autonomous LED colour dance (the iconic Nabaztag
# "sign of life": LEDs lighting up in a random order, in random colours).
_LED_PALETTE = [(255, 120, 0), (0, 160, 255), (0, 200, 90), (220, 0, 150),
                (255, 210, 0), (150, 0, 255), (255, 0, 60), (0, 230, 200),
                (255, 255, 255), (255, 60, 0)]


def _led_color_dance(b):
    """One random LED colour show: light several of the 5 LEDs (belly included)
    one after another, in a random order with random vivid colours, hold a beat,
    then fade them all back out — so between shows the rabbit is calm, not stuck
    on a colour. This is THE autonomous 'alive' animation."""
    import random
    ids = list(LED_NAMES.values())
    random.shuffle(ids)
    ids = ids[:random.randint(3, len(ids))]      # how many LEDs join in
    actions, t = [], 0
    for lid in ids:                               # light them one by one
        r, g, bl = random.choice(_LED_PALETTE)
        actions.append((t, "led", (lid, r, g, bl)))
        t += 1
    for lid in ids:                               # …then all off together
        actions.append((t + 2, "led", (lid, 0, 0, 0)))
    b.send_choreography(300, actions)             # ~ (t+2)*0.3 s of colour


def _personality_action(b):
    """One random 'sign of life': mostly a LED colour dance (random order/colour
    across all 5 LEDs, belly included), sometimes an ample ear wiggle. Each LED
    show fades out, so it never leaves a stuck colour. Sleeps between sub-steps →
    run in a thread. (No autonomous nose blink — by preference.)"""
    import random, time as _t
    try:
        if random.random() < 0.75:
            _led_color_dance(b)
        else:
            # An AMPLE, clearly visible wiggle: opposite extremes, swap, then
            # settle to neutral (8). Small random moves were invisible.
            p, q = random.choice([(2, 14), (14, 2), (1, 12), (15, 4), (4, 15)])
            b.send_choreography(200, [(0, "motor", (EAR_NAMES["left"], p * 18, 0)),
                                      (0, "motor", (EAR_NAMES["right"], q * 18, 1))])
            _t.sleep(0.9)
            b.send_choreography(200, [(0, "motor", (EAR_NAMES["left"], q * 18, 1)),
                                      (0, "motor", (EAR_NAMES["right"], p * 18, 0))])
            _t.sleep(0.9)
            b.send_choreography(200, [(0, "motor", (EAR_NAMES["left"], 8 * 18, 0)),
                                      (0, "motor", (EAR_NAMES["right"], 8 * 18, 1))])
    except Exception as exc:  # noqa
        log.warning("personality: action failed %s", exc)


def personality_loop():
    """personality=auto: a gentle random behaviour every few minutes, but only
    while awake (daytime) and never on top of a voice exchange."""
    import random
    log.info("personality: auto on (every %d-%d s, daytime)", PERSONALITY_MIN_S, PERSONALITY_MAX_S)
    while True:
        time.sleep(random.randint(PERSONALITY_MIN_S, PERSONALITY_MAX_S))
        with BUNNIES_LOCK:
            rabbits = list(BUNNIES.values())
        if not rabbits:
            continue
        b = random.choice(rabbits)            # each rabbit its turn, not always the first
        h = time.localtime().tm_hour
        if h < 8 or h >= 22:                       # quiet at night
            continue
        if time.time() < WAKE.get("cooldown", 0.0):  # don't interrupt a voice reply
            continue
        log.info("personality: tick")
        _personality_action(b)


# --------------------------------------------------------------------------- #
def _on_sigterm(signum, frame):
    """Python runs as PID 1 here (init: false), and PID 1 ignores a signal
    it has no handler for: every stop and update waited out the Supervisor's
    timeout and ended in SIGKILL. Close the sessions so the rabbits see the
    end of the stream, then go."""
    log.info("stopping (signal %d): closing %d session(s)", signum, len(BUNNIES))
    with BUNNIES_LOCK:
        sessions = list(BUNNIES.values())
    for s in sessions:
        with contextlib.suppress(Exception):
            s.close()
    _save_state()
    os._exit(0)


def _announce_api_token():
    if API_TOKEN_SOURCE == "option":
        log.info("control API: token from the api_token option")
        return
    log.warning("control API token: %s -- send it as the X-API-Token header (Home "
                "Assistant rest_command: headers). %s", API_TOKEN,
                {"file": f"Kept in {API_TOKEN_FILE}.",
                 "new": f"Generated now, kept in {API_TOKEN_FILE}.",
                 "memory": f"{API_TOKEN_FILE} is not writable: a new one at each start."
                 }[API_TOKEN_SOURCE])
    if API_TOKEN_SOURCE == "new":
        notify_ha("Nabaztag: the control API now needs a token",
                  "Since 0.19.0 the add-on's control API (port 8099) answers only "
                  "requests that carry its token. One was generated: it is in the "
                  "add-on's log (Settings, Add-ons, Nabaztag Terrier, Log). Add it to "
                  "your rest_commands as the X-API-Token header, or set your own in the "
                  "api_token option. The adoption page is in the add-on's Web UI.",
                  "nabaztag_api_token")


def main():
    log.info("nabaztag-violet starting | server_address=%s http=%d xmpp=%d api=%d",
             SERVER_ADDRESS, HTTP_PORT, XMPP_PORT, API_PORT)
    signal.signal(signal.SIGTERM, _on_sigterm)
    _announce_api_token()
    if VOICE_PIPELINE or AUTO_LISTEN or WAKE["on"]:
        # The model is no longer in the image: fetch it now rather than on
        # the first press, so a voice user upgrading finds it ready.
        threading.Thread(target=whisper_model_path, daemon=True).start()
    if not os.path.exists(BOOTCODE_FILE):
        log.warning("bootcode file not found at %s — /vl/bc.jsp will 500. The "
                    "add-on image bundles one; outside it, set BOOTCODE_FILE to "
                    "bytecode/bootcode_hybrid.bin from the repo (see DOCS.md, "
                    "'Bytecodes')", BOOTCODE_FILE)
    threading.Thread(target=xmpp_server, daemon=True).start()
    if TLS_CTX is not None:
        threading.Thread(target=xmpp_tls_server, daemon=True).start()
    if BOOTCODE_TLS_FILE:
        log.info("adopted rabbits get %s", BOOTCODE_TLS_FILE)
    if BOOTCODE_HTTPS_FILE:
        log.info("adopted rabbits on Naboot 1.2.0.0 and later get %s", BOOTCODE_HTTPS_FILE)
    else:
        log.warning("no TLS runtime bundled: adopted rabbits get the hybrid, "
                    "which never opens the TLS port")
    threading.Thread(target=api_server, daemon=True).start()
    threading.Thread(target=udp_mic_server, daemon=True).start()
    threading.Thread(target=wake_loop, daemon=True).start()
    if PERSONALITY_ACTIVE:
        threading.Thread(target=personality_loop, daemon=True).start()
    http_boot_server()  # blocks


if __name__ == "__main__":
    main()
