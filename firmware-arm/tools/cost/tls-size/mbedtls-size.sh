#!/bin/bash
# Flash size of Mbed TLS 3.6's TLS 1.3 client, compiled for the rabbit's
# CPU with -Os, as object files (an upper bound: nothing is garbage
# collected, nothing is linked). Runs in localhost/nab-qemu.
#   bash mbedtls-size.sh /path/to/mbedtls
set -e
M=$1; HERE=$(cd "$(dirname "$0")" && pwd); OUT=$HERE/out-mbedtls; mkdir -p $OUT
CF=(-Os -mthumb -mthumb-interwork -mcpu=arm7tdmi -ffunction-sections -fdata-sections -w
    "-DMBEDTLS_CONFIG_FILE=\"$HERE/mbedtls-config.h\"" "-DMBEDTLS_PSA_CRYPTO_CONFIG_FILE=\"$HERE/mbedtls-psa-config.h\""
    -I$M/include -I$M/library)
tot() { arm-none-eabi-size "$@" | awk 'NR>1{t+=$1; d+=$2} END{print t+d}'; }
ssl="ssl_tls.c ssl_msg.c ssl_client.c ssl_tls13_client.c ssl_tls13_generic.c ssl_tls13_keys.c ssl_ciphersuites.c"
crypto="aes.c gcm.c sha256.c md.c hkdf.c cipher.c cipher_wrap.c ecdh.c ecp.c ecp_curves.c ecp_curves_new.c bignum.c bignum_core.c bignum_mod.c bignum_mod_raw.c constant_time.c platform_util.c"
x509="x509.c x509_crt.c asn1parse.c oid.c pk.c pk_wrap.c pkparse.c pk_ecc.c ecdsa.c psa_crypto.c psa_crypto_ecp.c psa_crypto_cipher.c psa_crypto_aead.c psa_crypto_hash.c psa_crypto_mac.c psa_crypto_slot_management.c psa_crypto_client.c psa_crypto_driver_wrappers_no_static.c psa_util.c"
for group in ssl crypto x509; do
  objs=""
  for s in ${!group}; do
    [ -f $M/library/$s ] || { echo "skip $s"; continue; }
    arm-none-eabi-gcc "${CF[@]}" -c $M/library/$s -o $OUT/${s%.c}.o && objs="$objs $OUT/${s%.c}.o"
  done
  echo "== $group: $(tot $objs) bytes text+rodata (-Os, .o sum)"
  for o in $objs; do printf "   %7d %s\n" $(tot $o) $(basename $o); done
done
