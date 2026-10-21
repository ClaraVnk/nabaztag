# The crypto half of the WPA2 fix, tested on a build machine.
#
# eapol.c cannot be compiled off the rabbit — it reaches into the RT2501
# driver, the 802.11 state machine and the debug UART — so the group-key path
# was historically only testable by flashing a rabbit and hoping. Everything
# under test here touches nothing but bytes, so it runs anywhere, including in
# CI, and under the sanitizers.
#
#   make           vectors, then every suite, with ASan and UBSan
#   make vectors   regenerate vectors.h (needs python + cryptography)
#   make arm       prove it still cross-compiles for the ARM7TDMI

CC      ?= cc
CFLAGS  ?= -O1 -g -std=c99 -Wall -Wextra -Werror -pedantic
SAN     ?= -fsanitize=address,undefined -fno-omit-frame-pointer
INC     := -I. -I../crypto -Inet
SRC     := ../crypto/aes128.c

ARM_CC  ?= arm-none-eabi-gcc
ARM_FLAGS ?= -O2 -mcpu=arm7tdmi -std=c99 -Wall -Wextra -Werror

.PHONY: all vectors clean arm
all: net/aes128.h
	@echo "== AES-128 and RFC 3394, against FIPS-197 and the RFC"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_aes test_aes.c ../crypto/aes128.c && /tmp/t_aes
	@echo
	@echo "== a real WPA2 message 3, end to end"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_wpa2 test_wpa2.c $(SRC) && /tmp/t_wpa2
	@echo
	@echo "== random, truncated and adversarial input"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_fuzz test_fuzz.c $(SRC) && /tmp/t_fuzz

# The sources include <net/aes128.h> the way the firmware tree lays them out.
net/aes128.h:
	@mkdir -p net
	@cp ../crypto/aes128.h net/

vectors:
	python3 vectors.py > vectors.h

arm:
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/aes128.c -o /tmp/aes128.o
	@arm-none-eabi-size /tmp/aes128.o

clean:
	rm -rf net /tmp/t_aes /tmp/t_wpa2 /tmp/t_fuzz
