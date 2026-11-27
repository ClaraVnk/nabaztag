#!/bin/sh
# A TagTagTag without its hardware, for the Terrier's TagTagTag client: Pynab's
# own development image (its Docker/nab/Dockerfile, Debian with Python 3.7, no
# speech recognition) and its nabd with the virtual I/O (nabd/nabio_virtual.py
# logs what the Pi Zero would do with the LEDs, ears and sound), a PostgreSQL
# beside it, in one podman pod. Debian by Clara's exception (2026-11-27, "prends
# leur image debian"), as LapinLibre's Ubuntu builder: only the test bench, no
# Debian reaches a rabbit. On the dev VM, from the repository root:
#
#   firmware-arm/emulator/tagtagtag/nabd.sh up      # nabd on 127.0.0.1:10543 (the pod)
#   firmware-arm/emulator/tagtagtag/nabd.sh logs    # what nabd did
#   firmware-arm/emulator/tagtagtag/nabd.sh down
#
# POD_NET: the pod on that podman network instead of a published port (the
# Terrier's karotz/proof/tagtagtag.sh, a test Terrier beside it); PROOF_DIR:
# mounted at /proof in the nabd container (its observer, ttt-observe.py);
# LOGLEVEL=DEBUG: nabd logs the exception that makes it drop a service
# (service_loop logs it at debug only, 2026-11-27).
set -eu
PYNAB_COMMIT=${PYNAB_COMMIT:-f24d3e1}      # nabaztag2018/pynab, 2025-01-12
POD=${POD:-tagtagtag}
SRC=${PYNAB_SRC:-$HOME/.cache/pynab-$PYNAB_COMMIT}
case "${1:-up}" in
up)
  if [ ! -d "$SRC" ]; then
    git clone -q https://github.com/nabaztag2018/pynab.git "$SRC"
    git -C "$SRC" checkout -q "$PYNAB_COMMIT"
  fi
  # Debian 11 left the mirrors for archive.debian.org (2026-11-27: every
  # package 404): their Dockerfile, copied, with the archive's sources.
  if ! podman image exists "pynab-dev:$PYNAB_COMMIT"; then
    sed '/^FROM /a RUN printf "deb http://archive.debian.org/debian bullseye main\\ndeb http://archive.debian.org/debian-security bullseye-security main\\n" > /etc/apt/sources.list && rm -rf /etc/apt/sources.list.d/*' \
      "$SRC/Docker/nab/Dockerfile" > "$SRC/Docker/nab/Dockerfile.archive"
    podman build -q -t "pynab-dev:$PYNAB_COMMIT" -f "$SRC/Docker/nab/Dockerfile.archive" "$SRC" >/dev/null
  fi
  podman pod rm -f "$POD" >/dev/null 2>&1 || true
  if [ -n "${POD_NET:-}" ]; then
    podman pod create --name "$POD" --network "$POD_NET" >/dev/null
  else
    podman pod create --name "$POD" -p 127.0.0.1:10543:10543 >/dev/null
  fi
  PROOF_MOUNT=""
  [ -n "${PROOF_DIR:-}" ] && PROOF_MOUNT="-v $PROOF_DIR:/proof:z"
  podman run -d -q --pod "$POD" --name "$POD-db" -e POSTGRES_USER=pynab -e POSTGRES_PASSWORD=pynab \
    -e POSTGRES_DB=pynab docker.io/library/postgres:11-alpine >/dev/null
  # shellcheck disable=SC2086
  podman run -d -q --pod "$POD" --name "$POD-nabd" -v "$SRC:/opt/pynab:Z,U" $PROOF_MOUNT \
    -e LOGLEVEL="${LOGLEVEL:-INFO}" -e NABD_HOST=127.0.0.1 -e PGHOST=127.0.0.1 -e PGUSER=pynab -e PGPASSWORD=pynab -e PGDATABASE=pynab -e DAEMONS= \
    "pynab-dev:$PYNAB_COMMIT" /bin/bash /usr/local/bin/run-pynab.sh >/dev/null
  i=0; until podman logs "$POD-nabd" 2>&1 | grep -q "Starting nabd"; do
    i=$((i + 1)); [ $i -lt 120 ] || { podman logs "$POD-nabd" 2>&1 | tail -20; exit 1; }; sleep 2; done
  sleep 5; echo "nabd up in pod $POD" ;;
logs) podman logs "$POD-nabd" 2>&1 | tail -${2:-40} ;;
down) podman pod rm -f "$POD" >/dev/null ;;
esac
