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
	@echo
	@echo "== P-256, against OpenSSL and against the curve"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_p256 test_p256.c ../crypto/p256.c && /tmp/t_p256
	@echo
	@echo "== SHA-256, HMAC and the 802.11 KDF"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_sha256 test_sha256.c ../crypto/sha256.c && /tmp/t_sha256
	@echo
	@echo "== the AES forward cipher, CMAC and BIP"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_cmac test_cmac.c ../crypto/cmac.c && /tmp/t_cmac
	@echo
	@echo "== a full SAE exchange (see the header: the peer is this code)"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_sae test_sae.c ../crypto/sae.c ../crypto/p256.c ../crypto/sha256.c && /tmp/t_sae

# The sources include <net/aes128.h> the way the firmware tree lays them out.
# The prerequisite matters: without it the copy is made once and then goes
# stale in silence, so a new declaration in crypto/aes128.h reads as an
# undeclared function in a file that includes the header it is in.
net/aes128.h: ../crypto/aes128.h
	@mkdir -p net
	@cp ../crypto/aes128.h net/

vectors:
	python3 vectors.py > vectors.h
	python3 vectors_p256.py > vectors_p256.h
	python3 vectors_sha256.py > vectors_sha256.h
	python3 vectors_cmac.py > vectors_cmac.h

# The flash budget is the reason WPA3 was "not impossible, just unimplemented"
# for so long, so the size is part of the test, not a footnote.
arm:
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/aes128.c -o /tmp/aes128.o
	@arm-none-eabi-size /tmp/aes128.o
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/p256.c -o /tmp/p256.o
	@arm-none-eabi-size /tmp/p256.o
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/sha256.c -o /tmp/sha256.o
	@arm-none-eabi-size /tmp/sha256.o
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/cmac.c -o /tmp/cmac.o
	@arm-none-eabi-size /tmp/cmac.o
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/sae.c -o /tmp/sae.o
	@arm-none-eabi-size /tmp/sae.o

clean:
	rm -rf net /tmp/t_aes /tmp/t_wpa2 /tmp/t_fuzz /tmp/t_p256 \
		/tmp/t_sha256 /tmp/t_cmac /tmp/t_sae
