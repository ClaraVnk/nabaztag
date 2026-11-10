#!/usr/bin/env python3
"""Violet's Mir:ror as a tag reader for Le Terrier.

The Mir:ror (2008) is Violet's USB RFID reader: put a Ztamp or a Nano:ztag
on it and something happens. It is a USB HID device, 1da8:1301, with no
driver needed: Linux gives it a /dev/hidraw node, and every event is one
64-byte report. Read on a real Mir:ror (2026-11-10):

    02 01 00 00 08 <8 bytes>   a tag was put on it; the 8 bytes are its id
    02 02 00 00 08 <8 bytes>   that tag was taken off
    01 05                      the Mir:ror was turned upside down
    01 04                      it was turned back up
    all zeros                  nothing (it sends about sixty a second)

The id is the one a Nabaztag:tag reads on its nose, so a tag means the same
on both. This posts each tag put on it, and each flip, to the warren's
POST /api/rabbits/<mac>/rfid/seen, as if it had been shown to that rabbit:
the action the owner bound to the tag runs, and Home Assistant hears of it
through a webhook action, as for the nose. The flips arrive as the
pseudo-tags mirror-upside-down and mirror-upright. A tag taken off posts
nothing.

Configuration, from the environment:

    TERRIER_TOKEN     an API token (the warren's Home Assistant page); or
    TERRIER_TOKEN_FILE   a file holding it
    RABBIT_MAC        the rabbit the tags are for, 12 hex digits
    TERRIER_URL       default https://terrier.cyberloutre.fr
    MIRROR_DEVICE     default: /dev/violet-mirror, else the hidraw node
                      whose HID id is 1da8:1301

Standard library only. Unplugging the Mir:ror is not an error: it is looked
for again every few seconds.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request

log = logging.getLogger("mirror")

VENDOR, PRODUCT = 0x1DA8, 0x1301
REPORT = 64
UPSIDE_DOWN, UPRIGHT = "mirror-upside-down", "mirror-upright"


def parse(report: bytes) -> tuple[str, str] | None:
    """One report as (kind, value), or None for nothing worth a word.

    kind is "on" or "off" with the tag's id in hex, or "flip" with
    UPSIDE_DOWN or UPRIGHT."""
    if len(report) < 2 or not any(report):
        return None
    if report[0] == 0x02 and report[1] in (0x01, 0x02) and len(report) >= 5:
        n = report[4]
        tag = report[5:5 + n]
        if n == 0 or len(tag) != n:
            return None
        return ("on" if report[1] == 0x01 else "off", tag.hex())
    if report[0] == 0x01 and report[1] in (0x04, 0x05):
        return ("flip", UPSIDE_DOWN if report[1] == 0x05 else UPRIGHT)
    return None


def find_device() -> str | None:
    want = os.environ.get("MIRROR_DEVICE")
    if want:
        return want if os.path.exists(want) else None
    if os.path.exists("/dev/violet-mirror"):
        return "/dev/violet-mirror"
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            text = open(uevent).read()
        except OSError:
            continue
        m = re.search(r"HID_ID=[0-9A-Fa-f]+:([0-9A-Fa-f]+):([0-9A-Fa-f]+)", text)
        if m and int(m.group(1), 16) == VENDOR and int(m.group(2), 16) == PRODUCT:
            return "/dev/" + uevent.split("/")[4]
    return None


class Terrier:
    def __init__(self, url: str, token: str, mac: str):
        self.endpoint = f"{url.rstrip('/')}/api/rabbits/{mac}/rfid/seen"
        self.token = token

    def seen(self, tag: str) -> bool:
        req = urllib.request.Request(
            self.endpoint, method="POST",
            data=json.dumps({"tag": tag, "reader": "mirror"}).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "User-Agent": "violet-mirror/1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            log.warning("the warren refused %s: %s %s", tag, e.code,
                        e.read(200).decode("utf-8", "replace"))
        except OSError as e:
            log.warning("could not reach the warren for %s: %s", tag, e)
        return False


def run(terrier: Terrier, path: str) -> None:
    """Read one plugged-in Mir:ror until it is unplugged."""
    log.info("reading the Mir:ror on %s", path)
    # It says nothing about how it lies when it is opened: every orientation
    # report is a flip. Only a repeat of the last one is dropped.
    upright = None
    with open(path, "rb", buffering=0) as dev:
        while True:
            event = parse(dev.read(REPORT))
            if event is None:
                continue
            kind, value = event
            if kind == "on":
                log.info("tag %s put on it", value)
                terrier.seen(value)
            elif kind == "off":
                log.info("tag %s taken off", value)
            elif kind == "flip":
                now = value == UPRIGHT
                if now != upright:
                    log.info("turned %s", "back up" if now else "upside down")
                    terrier.seen(value)
                upright = now


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("TERRIER_TOKEN", "")
    if not token and os.environ.get("TERRIER_TOKEN_FILE"):
        token = open(os.environ["TERRIER_TOKEN_FILE"]).read().strip()
    mac = os.environ.get("RABBIT_MAC", "").lower().replace(":", "")
    if not token or not re.fullmatch(r"[0-9a-f]{12}", mac):
        sys.exit("set TERRIER_TOKEN (or TERRIER_TOKEN_FILE) and RABBIT_MAC")
    terrier = Terrier(os.environ.get("TERRIER_URL", "https://terrier.cyberloutre.fr"),
                      token, mac)
    waiting = False
    while True:
        path = find_device()
        if path is None:
            if not waiting:
                log.info("no Mir:ror plugged in; looking again every 5 s")
            waiting = True
            time.sleep(5)
            continue
        waiting = False
        try:
            run(terrier, path)
        except OSError as e:
            log.info("the Mir:ror went away (%s)", e)
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
