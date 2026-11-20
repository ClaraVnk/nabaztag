#!/bin/sh
# Raspberry Pi Imager on a virtual screen (the Containerfile here), started on
# our repository; the screenshots are then taken step by step from outside:
#
#   podman build -t imager-shots karotz/lapinlibre/imager-shots
#   podman run -d --name shots -v "$PWD/shots:/out" imager-shots
#   podman exec shots sh -c 'xdotool mousemove X Y click 1; import -window root /out/NAME.png'
#
# The window is 680x450 at (300,205) on the 1280x860 screen: crop with
# `convert NAME.png -crop 680x450+300+205 +repage -quality 85 NAME.webp`.
# The first list download can fail while the container's network comes up:
# RETRY (880,336). The container has no /dev/sd*, so nothing can be written;
# never press WRITE anyway. Remove the image afterwards (Ubuntu, by exception).
REPO=${REPO:-https://raw.githubusercontent.com/ClaraVnk/nabaztag/main/karotz/lapinlibre/terrier-os.rpi-imager.json}
Xvfb :99 -screen 0 1280x860x24 > /dev/null 2>&1 &
sleep 2
/opt/imager/AppRun --repo "$REPO" > /out/imager.log 2>&1 &
sleep 20
import -window root /out/00-start.png
sleep 3600
