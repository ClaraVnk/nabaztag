# The Mir:ror as a tag reader

Violet's **Mir:ror** (2008) is a USB RFID reader: put a Ztamp or a Nano:ztag
on it and something happens. This makes it a second reader for a rabbit on
Le Terrier: a tag on the Mir:ror does what the same tag does on the rabbit's
nose — the action bound to it on the rabbit's page runs, and Home Assistant
hears of it through a webhook action. Turning the Mir:ror upside down, and
back, are two more "tags" (`mirror-upside-down`, `mirror-upright`) that can
be bound like any other.

It needs no driver: it is a USB HID device, `1da8:1301`. What its reports
mean is in [`mirror.py`](mirror.py), read on a real one.

## On a Raspberry Pi (or any Linux box)

1. Let your user open the reader, and give it a stable name:

   ```
   echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1da8", ATTRS{idProduct}=="1301", MODE="0660", GROUP="plugdev", SYMLINK+="violet-mirror"' \
     | sudo tee /etc/udev/rules.d/99-violet-mirror.rules
   sudo udevadm control --reload && sudo udevadm trigger
   ```

   Your user must be in `plugdev` (`groups`; else
   `sudo usermod -aG plugdev $USER` and log in again).

2. An API token from the warren's **Integration** page, and the rabbit the
   tags are for, in `~/.config/violet-mirror.env` (keep it to yourself:
   `chmod 600`):

   ```
   TERRIER_TOKEN=trr_...
   RABBIT_MAC=0019db123456
   ```

3. Either run it directly — Python 3, standard library only:

   ```
   set -a; . ~/.config/violet-mirror.env; set +a
   python3 mirror/mirror.py
   ```

   or as a rootless podman service:

   ```
   podman build -t violet-mirror mirror/
   cp mirror/mirror.container ~/.config/containers/systemd/violet-mirror.container
   systemctl --user daemon-reload && systemctl --user start violet-mirror
   ```

Put a tag on it: the rabbit's page lists it under the recent tags, ready to be
bound.

The parser's tests: `cd mirror && python3 -m unittest test_mirror`.
