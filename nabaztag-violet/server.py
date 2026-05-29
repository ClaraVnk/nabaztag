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
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

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
# Which bytecode to serve at /vl/bc.jsp. The bytecode and the locate format are
# a matched pair: 'ojn' (OpenJabNab) expects the locate WITH the xmpp port (what
# we now send); 'violet' is the original; 'pub' is the live community server's
# (newest) build, fetched at build for comparison/RE.
BOOTCODE_CHOICE = (os.environ.get("BOOTCODE") or _OPTS.get("bootcode") or "ojn").lower()
if BOOTCODE_CHOICE == "hybrid":
    # The custom mic-streaming bytecode (Phase 3) is built locally and dropped in
    # the add-on's persistent /data (it's RE-derived firmware, not shipped).
    BOOTCODE_FILE = os.environ.get("BOOTCODE_FILE", "/data/bootcode.hybrid")
else:
    BOOTCODE_FILE = os.environ.get(
        "BOOTCODE_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), f"bootcode.{BOOTCODE_CHOICE}"),
    )
# Where Nabi should stream its mic (the hybrid bytecode's RS command). The rabbit
# routes UDP to this host:4000; we resolve the advertised server address to an IP.
MIC_UDP_PORT = int(os.environ.get("MIC_UDP_PORT", _OPTS.get("mic_udp_port", 4000)))
# Phase 3B wake word: when the mic is streaming, transcribe rolling windows and,
# if the wake word is heard, treat the rest as a command for the conversation
# agent. auto_listen starts the stream automatically once Nabi is idle.
WAKE_WORD = (os.environ.get("WAKE_WORD") or _OPTS.get("wake_word") or "nabi").lower()
WAKE_WINDOW_S = float(os.environ.get("WAKE_WINDOW_S") or _OPTS.get("wake_window_s") or 3)
AUTO_LISTEN = str(os.environ.get("AUTO_LISTEN") or _OPTS.get("auto_listen") or "").lower() in ("1", "true", "yes", "on")
# Phase-2 voice pipeline: button push-to-talk → STT (bundled whisper.cpp) →
# optional conversation agent (Home Assistant, e.g. Claude) → TTS, all triggered
# by the rabbit's button. When voice_pipeline is off the recording is just stored.
VOICE_PIPELINE = str(os.environ.get("VOICE_PIPELINE") or _OPTS.get("voice_pipeline") or "").lower() in ("1", "true", "yes", "on")
CONVERSATION_AGENT = os.environ.get("CONVERSATION_AGENT") or _OPTS.get("conversation_agent") or ""
STT_LANGUAGE = os.environ.get("STT_LANGUAGE") or _OPTS.get("stt_language") or "fr"
# Prepended to what we send the conversation agent, so it can drive the rabbit
# by embedding action tags in its reply (executed + stripped before speaking).
_DEFAULT_VOICE_PROMPT = (
    "Tu es la voix d'un lapin Nabaztag espiègle. Réponds en français, en une ou "
    "deux phrases courtes. Tu peux AGIR en insérant des balises dans ta réponse : "
    "[ears G D] (oreilles, positions 0 à 16), "
    "[led ZONE R V B] (ZONE = bottom|left|middle|right|top, couleurs 0 à 255), "
    "[nose N] (nez, N = 0,1,2). Les balises sont exécutées puis retirées de ce qui "
    "est dit à voix haute. Voici ce qu'on te dit : "
)
VOICE_PROMPT = os.environ.get("VOICE_PROMPT")
if VOICE_PROMPT is None:
    VOICE_PROMPT = _OPTS.get("voice_prompt", _DEFAULT_VOICE_PROMPT)
# TTS engine: "espeak" (bundled, robotic) or "piper" (nicer; via the Home
# Assistant Piper add-on, fetched through the Supervisor proxy).
TTS_ENGINE = (os.environ.get("TTS_ENGINE") or _OPTS.get("tts_engine") or "espeak").lower()
TTS_ENTITY = os.environ.get("TTS_ENTITY") or _OPTS.get("tts_entity") or "tts.piper"
WHISPER_BIN = os.environ.get("WHISPER_BIN", "/usr/local/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "/app/models/ggml-small.bin")
# MQTT broker for exposing Nabi's controls as HA entities. If mqtt_host is set we
# use it; otherwise we try the Supervisor MQTT service (the Mosquitto add-on).
MQTT_HOST = os.environ.get("MQTT_HOST") or _OPTS.get("mqtt_host") or ""
MQTT_PORT = int(os.environ.get("MQTT_PORT") or _OPTS.get("mqtt_port") or 1883)
MQTT_USER = os.environ.get("MQTT_USER") or _OPTS.get("mqtt_user") or ""
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or _OPTS.get("mqtt_password") or ""

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nabaztag")

# Registry of connected rabbits: mac -> XmppSession
BUNNIES: dict[str, "XmppSession"] = {}
BUNNIES_LOCK = threading.Lock()

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
    for time, kind, params in sorted(actions, key=lambda a: a[0]):
        delta = min(max(time - last, 0), 255)
        body.append(delta & 0xFF)
        if kind == "motor":
            ear, angle, d = params
            body += bytes([0x08, ear & 0xFF, (angle // 18) & 0xFF, d & 0xFF])
        else:  # led
            led, r, g, b = params
            body += bytes([0x07, led & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0x00])
        last = time
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
RESOURCES: dict[str, tuple[str, bytes]] = {}
RES_LOCK = threading.Lock()

# Last microphone recording the rabbit uploaded (button push-to-talk → POST
# /vl/record.jsp). The bytecode records at 8 kHz and POSTs a RIFF WAV in
# IMA/DVI-ADPCM (mono, 4-bit, 256-byte blocks). This is the Phase-2 voice input.
LAST_RECORDING = {"wav": None, "pcm_wav": None, "ts": 0.0, "mode": None}
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


def store_resource(content: bytes, content_type: str = "application/octet-stream") -> str:
    token = os.urandom(8).hex()
    with RES_LOCK:
        RESOURCES[token] = (content_type, content)
    return token


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


def _supervisor_token() -> str:
    """The Supervisor API token. Under s6-overlay our service doesn't inherit it
    in the env, so fall back to the file s6 stores the container env in."""
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if tok:
        return tok
    for p in ("/run/s6/container_environment/SUPERVISOR_TOKEN",
              "/var/run/s6/container_environment/SUPERVISOR_TOKEN"):
        try:
            with open(p) as fh:
                tok = fh.read().strip()
                if tok:
                    return tok
        except OSError:
            pass
    return ""


def synth_via_ha(text: str) -> bytes:
    """Synthesize via a Home Assistant TTS engine (e.g. the Piper add-on) using
    the Supervisor proxy; returns the audio bytes (MP3). "" on failure."""
    token = _supervisor_token()
    if not token:
        return b""
    import urllib.request
    try:
        body = json.dumps({"engine_id": TTS_ENTITY, "message": text}).encode()
        req = urllib.request.Request(
            "http://supervisor/core/api/tts_get_url", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        path = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("path")
        if not path:
            return b""
        areq = urllib.request.Request("http://supervisor/core" + path,
                                      headers={"Authorization": f"Bearer {token}"})
        return urllib.request.urlopen(areq, timeout=30).read()
    except Exception as exc:  # noqa
        log.warning("Piper/HA TTS failed: %s", exc)
        return b""


def synth_tts(text: str, voice: str = "fr", speed: int = 160, pitch: int = 50) -> bytes:
    """Make speech audio for the rabbit. Uses the Home Assistant Piper add-on when
    tts_engine=piper (nicer voice), otherwise the bundled espeak-ng (always works,
    fully local). Returns MP3 (Piper) or WAV (espeak); the rabbit decodes both."""
    text = _clean_for_tts(text)
    if TTS_ENGINE == "piper":
        audio = synth_via_ha(text)
        if audio:
            return audio
        log.warning("Piper TTS returned nothing — falling back to espeak")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        path = tf.name
    try:
        subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(speed), "-p", str(pitch), "-w", path, text],
            check=True, capture_output=True, timeout=25,
        )
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _resample_wav_16k(wav_bytes: bytes) -> bytes:
    """Return a 16 kHz mono 16-bit PCM WAV (what whisper.cpp expects). The rabbit
    records at 8 kHz, so we upsample."""
    import wave, audioop, io
    try:
        w = wave.open(io.BytesIO(wav_bytes))
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        frames = w.readframes(n)
        w.close()
        if sw != 2:
            frames = audioop.lin2lin(frames, sw, 2)
        if ch != 1:
            frames = audioop.tomono(frames, 2, 0.5, 0.5)
        if rate != 16000:
            frames, _ = audioop.ratecv(frames, 2, 1, rate, 16000, None)
        out = io.BytesIO()
        ww = wave.open(out, "wb")
        ww.setnchannels(1); ww.setsampwidth(2); ww.setframerate(16000)
        ww.writeframes(frames)
        ww.close()
        return out.getvalue()
    except Exception:
        return wav_bytes


def stt_transcribe(pcm_wav: bytes) -> str:
    """Transcribe a PCM WAV with the bundled whisper.cpp (resampled to 16 kHz).
    Returns "" if the binary/model is missing or nothing is recognised."""
    if not (os.path.exists(WHISPER_BIN) and os.path.exists(WHISPER_MODEL)):
        log.warning("STT unavailable (whisper missing: %s / %s)", WHISPER_BIN, WHISPER_MODEL)
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tf.write(_resample_wav_16k(pcm_wav))
        path = tf.name
    try:
        subprocess.run(
            [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", path, "-l", STT_LANGUAGE,
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
    import urllib.request
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


def handle_voice(pcm_wav: bytes):
    """Full voice loop for a button recording: STT → optional conversation agent
    (which can also drive the rabbit via action tags) → speak the reply."""
    with BUNNIES_LOCK:
        bunny = next(iter(BUNNIES.values()), None)
    if bunny is None:
        return
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
            wav = synth_tts(reply)
            bunny.send_program(f"ST {resource_url(store_resource(wav, _audio_ctype(wav)))}")
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

    def __init__(self, conn: socket.socket, addr):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.buf = ""
        self.mac = None
        self.authed = False
        self.bound = False
        self.domain = SERVER_ADDRESS
        self.resource = "Boot"
        self.msg_nb = 0
        self._alive = True

    # -- low level --------------------------------------------------------- #
    def send(self, data: str):
        try:
            self.conn.sendall(data.encode("utf-8"))
            log.debug("-> rabbit %s: %s", self.addr[0], data[:300])
        except OSError:
            self._alive = False

    @property
    def jid(self):
        return f"{self.mac or '0000'}@{self.domain}/{self.resource}"

    # -- command push ------------------------------------------------------ #
    def send_violet_packet(self, raw: bytes):
        self.msg_nb += 1
        b64 = base64.b64encode(raw).decode("ascii")
        stanza = (
            f"<message from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='ojn-{self.msg_nb}'>"
            f"<packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet>"
            f"</message>"
        )
        self.send(stanza)

    def send_program(self, program: str):
        """Push a code-0x0A program (newline-separated 'KEY value' commands)."""
        self.send_violet_packet(message_packet(program))

    def send_audio(self, url: str):
        """Stream + play audio from a URL (ST). The rabbit fetches it over HTTP,
        so it can be a /res/ URL on this server (HA-generated TTS, a sound…)."""
        self.send_program(f"ST {url}")

    def send_choreography(self, tempo_ms: int, actions: list):
        """Store a choreography resource and tell the rabbit to play it (CH)."""
        token = store_resource(build_choreography(tempo_ms, actions))
        self.send_program(f"CH {resource_url(token)}")

    def send_state_query(self):
        """Ask the rabbit to report its internal state via the bytecode's
        `getrunningstate` ad-hoc command. The reply (sState/gSleepState/run…)
        arrives as a normal incoming stanza and is logged."""
        self.msg_nb += 1
        self.send(
            f"<iq type='set' from='net.openjabnab.platform@{self.domain}/services' "
            f"to='{self.mac}@{self.domain}/{self.resource}' id='state-{self.msg_nb}'>"
            f"<command xmlns='http://jabber.org/protocol/commands' node='getrunningstate' "
            f"action='execute'/></iq>"
        )

    def _start_listen(self):
        """Begin streaming the mic for the wake word (auto_listen)."""
        WAKE["on"] = True
        WAKE["cooldown"] = 0.0
        log.info("auto-listen: starting mic stream on %s", self.mac)
        self.send_program(f"RS {_server_ip()} {MIC_UDP_PORT}")

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
        log.info("<- rabbit %s: %s", self.addr[0], f[:400])

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
            if m:
                self.mac = m.group(1).lower()
            # success-bypass: tell the rabbit it authenticated
            self.authed = True
            self.send("<success xmlns='urn:ietf:params:xml:ns:xmpp-sasl'/>")
            return

        if "urn:ietf:params:xml:ns:xmpp-bind" in f and "<iq" in f and "<unbind" not in f:
            # resource bind (note: <unbind> shares this namespace — exclude it so
            # it falls through to the unbind handler and doesn't reset the resource)
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            res = re.search(r"<resource>\s*([^<]*?)\s*</resource>", f)
            if res and res.group(1).strip():
                self.resource = res.group(1).strip()
            frm = re.search(r"from='([0-9a-fA-F]{8,})@", f) or re.search(r'from="([0-9a-fA-F]{8,})@', f)
            if frm:
                self.mac = frm.group(1).lower()
            self.send(
                f"<iq type='result' id='{iq_id}'>"
                f"<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><jid>{self.jid}</jid></bind></iq>"
            )
            self._register()
            return

        if "urn:ietf:params:xml:ns:xmpp-session" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.bound = True
            self.send(f"<iq type='result' id='{iq_id}'/>")
            log.info("rabbit %s (%s) is now bound and idle — ready for commands", self.addr[0], self.mac)
            if self.resource == "idle":
                threading.Timer(2.5, self.send_state_query).start()
                if AUTO_LISTEN:
                    threading.Timer(4.0, self._start_listen).start()
            return

        if "violet:iq:sources" in f and "<iq" in f:
            # The rabbit asks for its config; answering with the init packet lets
            # it finish booting (otherwise it stays stuck in the boot resource).
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            frm = re.search(r"from='([^']*)'", f) or re.search(r'from="([^"]*)"', f)
            to = re.search(r"to='([^']*)'", f) or re.search(r'to="([^"]*)"', f)
            frm = frm.group(1) if frm else self.jid
            to = to.group(1) if to else f"net.violet.platform@{self.domain}/sources"
            b64 = base64.b64encode(INIT_PACKET).decode("ascii")
            self.send(
                f"<iq from='{to}' to='{frm}' type='result' id='{iq_id}'>"
                f"<query xmlns='violet:iq:sources'><packet xmlns='violet:packet' format='1.0' ttl='604800'>{b64}</packet></query></iq>"
            )
            log.info("rabbit %s: answered violet:iq:sources (init packet) — boot should complete", self.addr[0])
            return

        if "<unbind" in f and "<iq" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.send(f"<iq type='result' id='{iq_id}'/>")
            self.bound = True
            log.info("rabbit %s: boot unbind done — now operational, ready for commands", self.addr[0])
            return

        if f.startswith("<iq") and "ping" in f:
            iq_id = (re.search(r"id='([^']*)'", f) or re.search(r'id="([^"]*)"', f))
            iq_id = iq_id.group(1) if iq_id else "1"
            self.send(f"<iq type='result' id='{iq_id}'/>")
            return

        if f.startswith("<presence"):
            # CRITICAL: after binding its operational resource the rabbit sends a
            # presence and then sits in the ssPresence state WAITING to receive a
            # presence back from the server; only then does it move to ssFree —
            # the one state in which it acts on pushed iq/message commands. Without
            # this reply it stays stuck and silently ignores everything we push.
            self.send(f"<presence from='{self.domain}' to='{self.jid}'/>")
            log.info("rabbit %s: presence <-> presence sent (rabbit should now go free)", self.addr[0])
            return
        # Anything else (events: button/RFID, packets) -> logged above.

    def _register(self):
        if self.mac:
            with BUNNIES_LOCK:
                BUNNIES[self.mac] = self
            log.info("registered bunny mac=%s from %s", self.mac, self.addr[0])

    def run(self):
        log.info("XMPP connection from %s", self.addr[0])
        self.conn.settimeout(900)
        try:
            while self._alive:
                try:
                    chunk = self.conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                self._feed(chunk.decode("utf-8", "replace"))
        finally:
            if self.mac:
                with BUNNIES_LOCK:
                    if BUNNIES.get(self.mac) is self:
                        BUNNIES.pop(self.mac, None)
            try:
                self.conn.close()
            except OSError:
                pass
            log.info("XMPP disconnect %s (mac=%s)", self.addr[0], self.mac)

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

    def _on_stream_open(self):
        self._stream_header(self._features_bind() if self.authed else self._features_sasl())


def xmpp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", XMPP_PORT))
    srv.listen(8)
    log.info("XMPP listening on 0.0.0.0:%d (domain advertised: %s)", XMPP_PORT, SERVER_ADDRESS)
    while True:
        conn, addr = srv.accept()
        XmppSession(conn, addr).start()


# --------------------------------------------------------------------------- #
# HTTP boot server (the rabbit) — /vl/bc.jsp, /vl/locate.jsp
# --------------------------------------------------------------------------- #
class BootHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet/0.1"

    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        if path.endswith("/bc.jsp"):
            try:
                with open(BOOTCODE_FILE, "rb") as fh:
                    data = fh.read()
            except OSError:
                self.send_error(500, "bootcode missing")
                return
            log.info("serving bootcode (%d bytes) to %s [%s]", len(data), self.address_string(), p.query)
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
            ctype, data = entry
            log.info("serving resource %s (%d bytes, %s) to %s", token, len(data), ctype, self.address_string())
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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
                    f"xmpp_domain {SERVER_ADDRESS}:{XMPP_PORT}\n").encode()
            log.info("locate -> %s for %s [%s]", SERVER_ADDRESS, self.address_string(), p.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Catch-all for other /vl/* (ping/broad polls) — 200 + log for RE.
        log.info("unhandled boot GET %s from %s", self.path, self.address_string())
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        if p.path.endswith("/record.jsp"):
            # Push-to-talk: holding the rabbit's head button records the mic and
            # POSTs a RIFF WAV (IMA-ADPCM, 8 kHz mono) here. Store both the raw
            # upload and a decoded PCM WAV (for STT). This is Phase-2 voice input.
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = self.rfile.read(length) if length else b""
            mode = parse_qs(p.query).get("m", [""])[0]
            pcm = ima_adpcm_to_pcm_wav(data)
            with REC_LOCK:
                LAST_RECORDING.update(wav=data, pcm_wav=pcm, ts=time.time(), mode=mode)
            try:
                with open("/data/last_record.wav", "wb") as fh:
                    fh.write(pcm)
            except OSError:
                pass
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
                threading.Thread(target=handle_voice, args=(pcm,), daemon=True).start()
            return
        log.info("unhandled boot POST %s from %s", self.path, self.address_string())
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def http_boot_server():
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), BootHandler)
    log.info("HTTP boot server on 0.0.0.0:%d (bootcode=%s)", HTTP_PORT, BOOTCODE_FILE)
    httpd.serve_forever()


# --------------------------------------------------------------------------- #
# Control API for Home Assistant
# --------------------------------------------------------------------------- #
class ApiHandler(BaseHTTPRequestHandler):
    server_version = "nabaztag-violet-api/0.1"

    def log_message(self, fmt, *args):
        log.debug("API %s - %s", self.address_string(), fmt % args)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bunny(self, q):
        mac = (q.get("mac", [None])[0] or "").lower()
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

        if path in ("", "/api", "/api/status"):
            with BUNNIES_LOCK:
                conn = {m: {"addr": s.addr[0], "bound": s.bound, "resource": s.resource}
                        for m, s in BUNNIES.items()}
            with REC_LOCK:
                rec = {"ts": LAST_RECORDING["ts"], "mode": LAST_RECORDING["mode"],
                       "pcm_bytes": len(LAST_RECORDING["pcm_wav"] or b"")}
            with MIC_LOCK:
                mic = {"packets": MIC_STREAM["packets"], "bytes": len(MIC_STREAM["adpcm"]),
                       "ts": MIC_STREAM["ts"], "src": MIC_STREAM["src"]}
            return self._json(200, {"server_address": SERVER_ADDRESS, "bunnies": conn,
                                    "last_recording": rec, "mic_stream": mic})

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

        body = b""
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                body = self.rfile.read(length)

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
                lid = LED_NAMES.get(led, int(led) if led.isdigit() else 0)
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
                    WAKE["on"] = True
                    WAKE["cooldown"] = 0.0
                    b.send_program(f"RS {_server_ip()} {MIC_UDP_PORT}")
                else:
                    WAKE["on"] = False
                    b.send_program("RT")
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
MIC_STREAM = {"adpcm": bytearray(), "packets": 0, "ts": 0.0, "src": None}
MIC_LOCK = threading.Lock()


def _server_ip():
    try:
        return socket.gethostbyname(SERVER_ADDRESS)
    except OSError:
        return SERVER_ADDRESS


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
        with MIC_LOCK:
            MIC_STREAM["adpcm"] += data[3:]
            MIC_STREAM["packets"] += 1
            MIC_STREAM["ts"] = time.time()
            MIC_STREAM["src"] = addr[0]
            n, total = MIC_STREAM["packets"], len(MIC_STREAM["adpcm"])
        now = time.time()
        if now - last_log > 2:
            log.info("UDP mic: %d snd datagrams, %d bytes (from %s)", n, total, addr[0])
            last_log = now


# --------------------------------------------------------------------------- #
# Phase 3B — wake word on the mic stream: transcribe rolling windows; when the
# wake word ("nabi") is heard, send the rest to the conversation agent.
# --------------------------------------------------------------------------- #
WAKE = {"on": False, "cooldown": 0.0}


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
    """Consume mic-stream windows; on the wake word, send the rest to the agent."""
    while True:
        time.sleep(WAKE_WINDOW_S)
        if not WAKE["on"] or time.time() < WAKE["cooldown"]:
            with MIC_LOCK:
                MIC_STREAM["adpcm"] = bytearray()  # drop audio while idle/cooling down
            continue
        with MIC_LOCK:
            chunk = bytes(MIC_STREAM["adpcm"])
            MIC_STREAM["adpcm"] = bytearray()
        if len(chunk) < 4000:  # < ~0.5 s of audio
            continue
        try:
            text = stt_transcribe(adpcm_stream_to_pcm_wav(chunk)).lower()
        except Exception as exc:  # noqa
            log.warning("wake: STT error %s", exc)
            continue
        if not text:
            continue
        log.info("wake: heard %r", text)
        if not (WAKE_WORD in text or "nab" in text or "navi" in text):
            continue
        cmd = _strip_wake(text)
        log.info("wake: TRIGGERED — command %r", cmd)
        WAKE["cooldown"] = time.time() + 10  # don't react to our own reply
        b = _bunny_any()
        if b is None:
            continue
        reply = conversation_ask((VOICE_PROMPT or "") + cmd) if (CONVERSATION_AGENT and cmd) else (cmd or "Oui ?")
        reply = run_action_tags(b, reply or "Oui ?")
        if reply.strip():
            try:
                wav = synth_tts(reply)
                b.send_program(f"ST {resource_url(store_resource(wav, _audio_ctype(wav)))}")
            except Exception as exc:  # noqa
                log.warning("wake: reply failed %s", exc)
        WAKE["cooldown"] = time.time() + 10


# --------------------------------------------------------------------------- #
# MQTT discovery — expose Nabi's controls as native Home Assistant entities.
# --------------------------------------------------------------------------- #
DISCOVERY_PREFIX = "homeassistant"
MQTT_BASE = "nabaztag"
_DEVICE = {"identifiers": ["nabaztag_nabi"], "name": "Nabaztag (Nabi)",
           "manufacturer": "Violet", "model": "Nabaztag/tag"}


def _mqtt_service_config():
    """Broker connection info from the Supervisor (add-on declares `mqtt:need`)."""
    token = _supervisor_token()
    if not token:
        return None
    import urllib.request
    try:
        req = urllib.request.Request("http://supervisor/services/mqtt",
                                     headers={"Authorization": f"Bearer {token}"})
        data = (json.loads(urllib.request.urlopen(req, timeout=10).read()).get("data") or {})
        return data if data.get("host") else None
    except Exception as exc:  # noqa
        log.info("MQTT service not available (%s) — skipping HA entity discovery", exc)
        return None


def _bunny_any():
    with BUNNIES_LOCK:
        return next(iter(BUNNIES.values()), None)


def _mqtt_handle(topic: str, payload: str):
    b = _bunny_any()
    if b is None:
        return
    t = topic[len(MQTT_BASE) + 1:]
    if t == "light/belly/set":
        d = json.loads(payload)
        if d.get("state") == "OFF":
            b.send_choreography(200, [(0, "led", (2, 0, 0, 0))])
        else:
            c = d.get("color") or {}
            b.send_choreography(200, [(0, "led", (2, int(c.get("r", 255)),
                                                  int(c.get("g", 255)), int(c.get("b", 255))))])
    elif t.startswith("ear/"):
        ear = 0 if t.split("/")[1] == "left" else 1
        b.send_choreography(200, [(0, "motor", (ear, int(payload) * 18, 0))])
    elif t == "nose/set":
        b.send_violet_packet(ambient_packet({SVC_NOSE: {"off": 0, "blink": 1, "double": 2}.get(payload, 0)}))
    elif t == "sleep/set":
        b.send_violet_packet(frame_packet(PKT_SLEEP, bytes([1 if payload == "ON" else 0])))
    elif t == "say/set" and payload.strip():
        wav = synth_tts(payload)
        b.send_program(f"ST {resource_url(store_resource(wav, _audio_ctype(wav)))}")


def start_mqtt():
    if MQTT_HOST:
        cfg = {"host": MQTT_HOST, "port": MQTT_PORT,
               "username": MQTT_USER or None, "password": MQTT_PASSWORD or None}
    else:
        cfg = _mqtt_service_config()
    if not cfg:
        log.info("MQTT: no broker (set the mqtt_host option, or install the Mosquitto add-on) — no HA entity discovery")
        return
    try:
        import paho.mqtt.client as mqtt
    except Exception:  # noqa
        log.warning("MQTT: paho-mqtt not installed — no HA entity discovery")
        return
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="nabaztag-addon")
    except Exception:  # noqa (paho 1.x)
        cli = mqtt.Client(client_id="nabaztag-addon")
    if cfg.get("username"):
        cli.username_pw_set(cfg.get("username"), cfg.get("password"))
    avail_topic = f"{MQTT_BASE}/availability"
    cli.will_set(avail_topic, "offline", retain=True)

    def discover():
        avail = {"availability_topic": avail_topic}
        def pub(typ, oid, conf):
            cli.publish(f"{DISCOVERY_PREFIX}/{typ}/nabaztag/{oid}/config",
                        json.dumps({**conf, "device": _DEVICE, **avail}), retain=True)
        pub("light", "belly", {"name": "Belly", "unique_id": "nabi_belly", "schema": "json",
            "color_mode": True, "supported_color_modes": ["rgb"],
            "command_topic": f"{MQTT_BASE}/light/belly/set"})
        for side in ("left", "right"):
            pub("number", f"ear_{side}", {"name": f"Ear {side}", "unique_id": f"nabi_ear_{side}",
                "min": 0, "max": 16, "step": 1, "icon": "mdi:rabbit",
                "command_topic": f"{MQTT_BASE}/ear/{side}/set"})
        pub("select", "nose", {"name": "Nose", "unique_id": "nabi_nose",
            "options": ["off", "blink", "double"], "command_topic": f"{MQTT_BASE}/nose/set"})
        pub("switch", "sleep", {"name": "Sleep", "unique_id": "nabi_sleep", "icon": "mdi:sleep",
            "payload_on": "ON", "payload_off": "OFF", "command_topic": f"{MQTT_BASE}/sleep/set"})
        pub("text", "say", {"name": "Say", "unique_id": "nabi_say", "icon": "mdi:bullhorn",
            "command_topic": f"{MQTT_BASE}/say/set"})
        cli.publish(avail_topic, "online", retain=True)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        log.info("MQTT connected (%s) — publishing Nabi entity discovery", reason_code)
        for sub in ("light/belly", "ear/left", "ear/right", "nose", "sleep", "say"):
            client.subscribe(f"{MQTT_BASE}/{sub}/set")
        discover()

    def on_message(client, userdata, msg):
        try:
            _mqtt_handle(msg.topic, msg.payload.decode("utf-8", "replace"))
        except Exception as exc:  # noqa
            log.warning("MQTT command error on %s: %s", msg.topic, exc)

    cli.on_connect = on_connect
    cli.on_message = on_message
    log.info("MQTT: connecting to %s:%s for HA entity discovery", cfg["host"], cfg.get("port", 1883))
    cli.connect_async(cfg["host"], int(cfg.get("port", 1883)), 60)
    cli.loop_forever(retry_first_connection=True)


# --------------------------------------------------------------------------- #
def main():
    log.info("nabaztag-violet starting | server_address=%s http=%d xmpp=%d api=%d",
             SERVER_ADDRESS, HTTP_PORT, XMPP_PORT, API_PORT)
    if not os.path.exists(BOOTCODE_FILE):
        log.warning("bootcode file not found at %s — /vl/bc.jsp will 500", BOOTCODE_FILE)
    threading.Thread(target=xmpp_server, daemon=True).start()
    threading.Thread(target=api_server, daemon=True).start()
    threading.Thread(target=start_mqtt, daemon=True).start()
    threading.Thread(target=udp_mic_server, daemon=True).start()
    threading.Thread(target=wake_loop, daemon=True).start()
    http_boot_server()  # blocks


if __name__ == "__main__":
    main()
