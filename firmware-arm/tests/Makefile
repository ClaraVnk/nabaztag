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
# Defined before any rule that uses it: a prerequisite list is
# expanded when the rule is read, not when it runs.
CRYPTO_H := net/aes128.h net/p256.h net/sha256.h net/cmac.h net/sae.h

ARM_CC  ?= arm-none-eabi-gcc
ARM_FLAGS ?= -O2 -mcpu=arm7tdmi -std=c99 -Wall -Wextra -Werror

.PHONY: all vectors clean arm sign_open
# `all` must stay the default goal. The rules below used to come first,
# which made the staging file the default target: `make` then built that
# one file, ran not a single suite, and exited 0. The ARM step noticed
# only because net/*.h was never created.
.DEFAULT_GOAL := all
# The shipped tweetnacl is upstream + apply-mods' `sign_open_alias`, so the
# test has to be built against the patched copy, not against upstream: the
# whole point is the one-buffer call, and upstream fails it.
TN_DIR := /tmp/nab-tn
$(TN_DIR)/tweetnacl.c: ../upstream/tweetnacl/tweetnacl.c ../apply-mods.py
	@mkdir -p $(TN_DIR)/src/crypto
	@# apply-mods.py checks it is looking at a nabgcc tree; this stub is
	@# enough for a step that only touches src/crypto/tweetnacl.c.
	@touch $(TN_DIR)/Makefile
	@cp ../upstream/tweetnacl/tweetnacl.c $(TN_DIR)/src/crypto/tweetnacl.c
	@cp ../upstream/tweetnacl/tweetnacl.h $(TN_DIR)/
	@python3 ../apply-mods.py --steps sign_open_alias $(TN_DIR) >/dev/null
	@cp $(TN_DIR)/src/crypto/tweetnacl.c $(TN_DIR)/tweetnacl.c

# Third-party code does not get our warning policy -- upstream tweetnacl does
# not survive -Wextra -Werror -pedantic, and the firmware does not build it
# that way either. The sanitizers still apply: those are what matter here.
sign_open: $(TN_DIR)/tweetnacl.c
	@echo "== crypto_sign_open called with one buffer, as nab_sig.c calls it"
	@$(CC) -O1 -g -std=c99 -w $(SAN) -I$(TN_DIR) -c -o /tmp/tn.o $(TN_DIR)/tweetnacl.c
	@$(CC) $(CFLAGS) $(SAN) -I$(TN_DIR) -o /tmp/t_sign_open \
		test_sign_open.c /tmp/tn.o && /tmp/t_sign_open

all: $(CRYPTO_H) sign_open
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
net/%.h: ../crypto/%.h
	@mkdir -p net
	@cp $< net/

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
