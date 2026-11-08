#!/bin/bash
# Flash size of picotls' protocol core (lib/picotls.c: record layer,
# handshake state machine, key schedule, extensions; TLS 1.3 only, client
# and server in one file), compiled for the rabbit's CPU with -Os. The
# crypto backend is external to this file, so the number is protocol logic
# alone. POSIX headers it drags in are stubbed; nothing is linked.
#   bash picotls-size.sh /path/to/picotls
set -e
P=$1; HERE=$(cd "$(dirname "$0")" && pwd); OUT=$HERE/out-picotls; mkdir -p $OUT/stub/sys $OUT/stub/arpa $OUT/stub/netinet
for h in pthread.h arpa/inet.h netinet/in.h sys/syscall.h; do
  echo "/* stub */" > $OUT/stub/$h
done
cat > $OUT/stub/sys/socket.h <<'EOF'
#include <stdint.h>
#define AF_INET 2
#define AF_INET6 10
struct sockaddr { unsigned short sa_family; char sa_data[14]; };
struct in_addr { uint32_t s_addr; };
struct in6_addr { uint8_t s6_addr[16]; };
struct sockaddr_in { unsigned short sin_family; uint16_t sin_port; struct in_addr sin_addr; };
struct sockaddr_in6 { unsigned short sin6_family; uint16_t sin6_port; uint32_t sin6_flowinfo; struct in6_addr sin6_addr; };
EOF
cat > $OUT/stub/arpa/inet.h <<'EOF'
static inline unsigned ntohl(unsigned x) { return __builtin_bswap32(x); }
static inline unsigned short ntohs(unsigned short x) { return __builtin_bswap16(x); }
static inline unsigned htonl(unsigned x) { return __builtin_bswap32(x); }
static inline unsigned short htons(unsigned short x) { return __builtin_bswap16(x); }
EOF
CF="-Os -mthumb -mthumb-interwork -mcpu=arm7tdmi -ffunction-sections -fdata-sections -w \
    -DNDEBUG -DPTLS_HAVE_LOG=0 -I$OUT/stub -I$P/include -I$P/deps/picotest"
arm-none-eabi-gcc $CF -c $P/lib/picotls.c -o $OUT/picotls.o
echo "== picotls.c (client+server, all extensions): $(arm-none-eabi-size $OUT/picotls.o | awk 'NR==2{print $1+$2}') bytes text+rodata (-Os)"
# What a client reaches: link with the client entry points as roots, every
# other section garbage-collected, undefined symbols ignored.
arm-none-eabi-ld --gc-sections --unresolved-symbols=ignore-all -e ptls_handshake \
  -u ptls_client_new -u ptls_handshake -u ptls_receive -u ptls_send -u ptls_free \
  -o $OUT/picotls-client.elf $OUT/picotls.o 2>/dev/null || true
[ -f $OUT/picotls-client.elf ] && echo "== reachable from ptls_client_new/handshake/send/receive: $(arm-none-eabi-size $OUT/picotls-client.elf | awk 'NR==2{print $1+$2}') bytes"
arm-none-eabi-nm -S --size-sort $OUT/picotls.o | python3 $HERE/../symsz.py | tail -25
