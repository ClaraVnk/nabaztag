#!/bin/sh
# Review lever O-1: what -Os would save on the pure-computation crypto files
# alone (host-tested with vectors, no driver or VM code), measured inside the
# firmware build image, which holds the patched tree and the release gcc.
#
#   ssh <dev-vm> 'flock ~/heavy.lock podman run --rm -i localhost/nab-firmware-build:naboot-1.2.1.0 sh -s' < firmware-arm/tools/os-crypto-size.sh
set -e
cd /build/nabgcc
FL="--specs=nosys.specs -mthumb -mthumb-interwork -fdata-sections -ffunction-sections -fno-exceptions -fno-delete-null-pointer-checks -mcpu=arm7tdmi -Iinc/ -Isys/inc -DHIDE_VM_OUTPUT -w"
arm-none-eabi-gcc --version | head -1
tot1=0; tot2=0
for f in src/net/p256.c src/net/sae.c src/net/sha256.c src/net/cmac.c src/net/aes128.c src/net/nab_kx.c src/net/nab_kx_vm.c src/net/nab_tls.c src/net/nab_tls_vm.c src/net/nab_https_vm.c src/net/nab_rng.c src/net/nab_bn.c src/net/nab_rsa.c src/net/nab_ecdsa.c src/net/nab_x509.c src/net/nab_sha384.c src/net/chachapoly.c src/crypto/tweetnacl.c src/crypto/nab_sig.c src/net/hash.c; do
  b=$(basename $f .c)
  arm-none-eabi-gcc -c $FL -O1 $f -o /tmp/$b.O1.o
  arm-none-eabi-gcc -c $FL -Os $f -o /tmp/$b.Os.o
  s1=$(arm-none-eabi-size /tmp/$b.O1.o | awk 'NR==2{print $1+$2}')
  s2=$(arm-none-eabi-size /tmp/$b.Os.o | awk 'NR==2{print $1+$2}')
  printf "%-14s O1=%6s Os=%6s diff=%6s\n" $b $s1 $s2 $((s1-s2))
  tot1=$((tot1+s1)); tot2=$((tot2+s2))
done
echo "TOTAL O1=$tot1 Os=$tot2 saved=$((tot1-tot2))  (object text+data, before --gc-sections and linking)"
