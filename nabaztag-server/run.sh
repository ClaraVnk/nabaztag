#!/usr/bin/env sh
# Add-on entrypoint. The base image is debian:jessie (no s6/bashio), and we
# avoid extra deps: HA writes options to /data/options.json, parsed here with
# grep/sed (the schema is flat, so this is robust). Persistent state lives in
# /data so it survives reboots and add-on updates.
set -e

OPTIONS=/data/options.json

get_str()  { grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$OPTIONS" 2>/dev/null | sed -E "s/.*:[[:space:]]*\"(.*)\"$/\1/" | head -n1; }
get_bool() { grep -oE "\"$1\"[[:space:]]*:[[:space:]]*(true|false)" "$OPTIONS" 2>/dev/null | grep -oE '(true|false)$' | head -n1; }

SERVER_ADDRESS="$(get_str server_address)"
LOG_LEVEL="$(get_str log_level)"
AUTH_BYPASS="$(get_bool auth_bypass)"

# 192.0.2.x is the RFC 5737 documentation range — a placeholder. Set the real
# HAOS host IP in the add-on options.
[ -z "$SERVER_ADDRESS" ] && SERVER_ADDRESS="192.0.2.10"
[ -z "$LOG_LEVEL" ] && LOG_LEVEL="Info"
[ -z "$AUTH_BYPASS" ] && AUTH_BYPASS="true"

OJN_DIR=/var/www/OpenJabNab/server
BIN_DIR="$OJN_DIR/bin"
STATE_DIR=/data/state
mkdir -p "$STATE_DIR"

echo "[nabaztag] server_address=$SERVER_ADDRESS log_level=$LOG_LEVEL auth_bypass=$AUTH_BYPASS"

# --- Render the persistent OpenJabNab config -------------------------------
# OpenJabNab's HTTP listener binds 127.0.0.1 (QHostAddress::LocalHost in the
# upstream source), so it sits behind Apache on an internal port (8080). Its
# XMPP listener binds 0.0.0.0, so it is reached directly on :5222.
# PingServer/BroadServer/XmppServer = the address handed back to the rabbit so
# it keeps talking to us. Anonymous registration is on so a fresh rabbit pairs
# itself on first boot.
cat > /data/openjabnab.ini <<EOF
[Config]
httpListener = true
httpApi = true
httpVioletApi = true
xmppListener = true
RealHttpRoot = ../../http-wrapper/ojn_local/
HttpRoot = ojn_local
HttpPluginsFolder = plugins
StandAloneAuthBypass = $AUTH_BYPASS
AllowAnonymousRegistration = true
AllowUserManageBunny = true
AllowUserManageZtamp = true
SessionTimeout = 300
TTS = acapela
MaxNumberOfBunnies = 64
MaxBurstNumberOfBunnies = 72

[OpenJabNabServers]
PingServer = $SERVER_ADDRESS
BroadServer = $SERVER_ADDRESS
XmppServer = $SERVER_ADDRESS
ListeningHttpPort = 8080
ListeningXmppPort = 5222

[Log]
LogFile = /data/openjabnab.log
LogFileLevel = $LOG_LEVEL
LogScreenLevel = Info
DisplayCronLog = false
EOF

ln -sf /data/openjabnab.ini "$BIN_DIR/openjabnab.ini"

# --- Persist mutable runtime state across rebuilds -------------------------
# OpenJabNab writes pairing/tag/account state under bin/. Relocate the likely
# data dirs to /data and symlink them back (NOT the `plugins` dir — that holds
# plugin code, which must track the image). NOTE: confirm the exact dir names
# from the logs on first run and adjust this list if needed.
for d in bunnies ztamps accounts; do
  if [ ! -e "$STATE_DIR/$d" ]; then
    if [ -d "$BIN_DIR/$d" ]; then cp -a "$BIN_DIR/$d" "$STATE_DIR/$d"; else mkdir -p "$STATE_DIR/$d"; fi
  fi
  rm -rf "$BIN_DIR/$d"
  ln -sf "$STATE_DIR/$d" "$BIN_DIR/$d"
done

# --- Apache reverse proxy on :80 -------------------------------------------
# OpenJabNab listens on 127.0.0.1:8080 only, so Apache (0.0.0.0:80) proxies the
# rabbit protocol (/vl/) and the control API (/ojn_api/) to it, and serves the
# admin UI (/ojn_admin/) from the http-wrapper document root.
if command -v apache2ctl >/dev/null 2>&1; then
  a2enmod proxy proxy_http >/dev/null 2>&1 || true
  printf 'Listen 80\n' > /etc/apache2/ports.conf
  cat > /etc/apache2/sites-enabled/000-default.conf <<'VHOST'
<VirtualHost *:80>
    ServerName nabaztag.local
    DocumentRoot /var/www/OpenJabNab/http-wrapper

    ProxyPreserveHost On
    ProxyPass        /vl/      http://127.0.0.1:8080/vl/
    ProxyPassReverse /vl/      http://127.0.0.1:8080/vl/
    ProxyPass        /ojn_api/ http://127.0.0.1:8080/ojn_api/
    ProxyPassReverse /ojn_api/ http://127.0.0.1:8080/ojn_api/

    <Directory /var/www/OpenJabNab/http-wrapper>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
VHOST
  : "${APACHE_RUN_USER:=www-data}";  : "${APACHE_RUN_GROUP:=www-data}"
  : "${APACHE_LOG_DIR:=/var/log/apache2}"; : "${APACHE_PID_FILE:=/var/run/apache2.pid}"
  : "${APACHE_LOCK_DIR:=/var/lock/apache2}"; : "${APACHE_RUN_DIR:=/var/run/apache2}"
  export APACHE_RUN_USER APACHE_RUN_GROUP APACHE_LOG_DIR APACHE_PID_FILE APACHE_LOCK_DIR APACHE_RUN_DIR
  mkdir -p "$APACHE_LOG_DIR" "$APACHE_LOCK_DIR" "$APACHE_RUN_DIR"
  service apache2 start 2>/dev/null || apache2ctl start 2>/dev/null || true
fi

# --- Start OpenJabNab (foreground) -----------------------------------------
cd "$BIN_DIR"
echo "[nabaztag] starting OpenJabNab — HTTP 127.0.0.1:8080 (proxied on :80 by Apache), XMPP :5222"
exec ./openjabnab
