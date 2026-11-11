#!/usr/bin/env bash
# Build and run the rabbit emulator in a container.
#
#   ./run.sh                                 every Wi-Fi scenario, pass or fail
#   ./run.sh scenarios/open-deauth.txt       one of them, full transcript
#   ./run.sh --vm scenarios/vm/boot-open.txt --eeprom open.eeprom --warren HOST
#                                            a whole rabbit, booting
#   ./run.sh --steps wpa2_gtk,mdns           pick which firmware mods to include
#
# Nothing is needed on the host but podman (or docker, with --runtime docker).
# The rabbit stays plugged in.
set -euo pipefail

RUNTIME=podman
STEPS=vbc,vinterp,stdlib,x25519_wdt,sign_open_alias,verify_wdt,vm_bounds,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,eapol_replay,audio_halfduplex,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,adoption_kx,tls_psk,link_liveness,crash_report,flash_precheck,ccmp_replay_count
BINARY=./rabbit
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --runtime) RUNTIME="$2"; shift 2 ;;
    --steps)   STEPS="$2";   shift 2 ;;
    --vm)      BINARY=./rabbit-vm; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *)         ARGS+=("$1"); shift ;;
  esac
done

cd "$(dirname "$0")/../.."     # the repository root, which is the context

echo "== building the emulator image (nabgcc mods: $STEPS)"
"$RUNTIME" build \
  --build-arg "MOD_STEPS=$STEPS" \
  -t nabaztag-emulator \
  -f firmware-arm/emulator/Containerfile .

# A whole-rabbit run needs to reach a warren, so it gets the host's network.
# The Wi-Fi scenarios need nothing at all.
NET=()
if [ "$BINARY" = "./rabbit-vm" ]; then NET=(--network host); fi

if [ ${#ARGS[@]} -eq 0 ]; then
  exec "$RUNTIME" run --rm "${NET[@]}" nabaztag-emulator
fi

exec "$RUNTIME" run --rm "${NET[@]}" nabaztag-emulator "$BINARY" "${ARGS[@]}"
