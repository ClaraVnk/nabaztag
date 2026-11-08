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
CRYPTO_H := net/aes128.h net/p256.h net/sha256.h net/cmac.h net/sae.h net/nab_kx.h net/nab_tls.h

ARM_CC  ?= arm-none-eabi-gcc
ARM_FLAGS ?= -O2 -mcpu=arm7tdmi -std=c99 -Wall -Wextra -Werror

.PHONY: all vectors clean arm sign_open kx tls tls-interop
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

# The lean-wpa3-slim steps that touch C, tested the way they land in the
# tree: apply-mods.py is run on a stub tree holding copies of the files it
# edits, and the tests compile what came out. uart_fmt and sbox need nothing
# but this repository; md5 edits nabgcc's src/net/hash.c, so it wants a
# checkout (make md5 NABGCC=/path/to/nabgcc) and the emulator container has
# one. The control runs first in each: the pristine file must pass too.
SLIM_DIR := /tmp/nab-slim
NABGCC ?=

uart_fmt:
	@echo "== the console formatters, against the sprintf they replace"
	@mkdir -p $(SLIM_DIR)
	@python3 -c 'import importlib.util as u; s=u.spec_from_file_location("am","../apply-mods.py"); m=u.module_from_spec(s); s.loader.exec_module(m); open("$(SLIM_DIR)/uart_fmt.inc","w").write(m._UART_FMT_C)'
	@$(CC) $(CFLAGS) $(SAN) -I$(SLIM_DIR) -o /tmp/t_uart_fmt test_uart_fmt.c && /tmp/t_uart_fmt

sbox: $(CRYPTO_H)
	@echo "== cmac.c on aes128.c's S-box: FIPS-197, RFC 3394, CMAC and BIP again"
	@rm -rf $(SLIM_DIR)/sbox && mkdir -p $(SLIM_DIR)/sbox/src/net && touch $(SLIM_DIR)/sbox/Makefile
	@cp ../crypto/aes128.c ../crypto/cmac.c $(SLIM_DIR)/sbox/src/net/
	@python3 ../apply-mods.py --steps share_sbox $(SLIM_DIR)/sbox >/dev/null
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_aes_shared test_aes.c $(SLIM_DIR)/sbox/src/net/aes128.c && /tmp/t_aes_shared
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_cmac_shared test_cmac.c $(SLIM_DIR)/sbox/src/net/cmac.c $(SLIM_DIR)/sbox/src/net/aes128.c && /tmp/t_cmac_shared

md5:
	@test -n "$(NABGCC)" || { echo "make md5 NABGCC=/path/to/nabgcc (it edits src/net/hash.c)"; exit 2; }
	@echo "== MD5 and HMAC-MD5, pristine hash.c (control)"
	@$(CC) $(CFLAGS) $(SAN) -Wno-unused-function -I$(NABGCC)/inc -DHASH_C='"$(NABGCC)/src/net/hash.c"' -o /tmp/t_md5_control test_md5.c && /tmp/t_md5_control
	@rm -rf $(SLIM_DIR)/md5 && mkdir -p $(SLIM_DIR)/md5/src/net && touch $(SLIM_DIR)/md5/Makefile
	@cp $(NABGCC)/src/net/hash.c $(SLIM_DIR)/md5/src/net/
	@python3 ../apply-mods.py --steps md5_compact $(SLIM_DIR)/md5 >/dev/null
	@echo "== MD5 and HMAC-MD5, hash.c after md5_compact"
	@$(CC) $(CFLAGS) $(SAN) -Wno-unused-function -I$(NABGCC)/inc -DHASH_C='"$(SLIM_DIR)/md5/src/net/hash.c"' -o /tmp/t_md5 test_md5.c && /tmp/t_md5

slim: uart_fmt sbox md5

# The adoption key exchange: TweetNaCl's X25519 patched as the firmware
# builds it (sign_open_alias, verify_wdt, x25519_wdt -- the last two call
# nab_verify_tick, which test_kx.c counts) under nab_kx.c, against OpenSSL
# via vectors_kx.py.
TNKX_DIR := /tmp/nab-tn-kx
$(TNKX_DIR)/tweetnacl.c: ../upstream/tweetnacl/tweetnacl.c ../apply-mods.py
	@mkdir -p $(TNKX_DIR)/src/crypto
	@touch $(TNKX_DIR)/Makefile
	@cp ../upstream/tweetnacl/tweetnacl.c $(TNKX_DIR)/src/crypto/tweetnacl.c
	@cp ../upstream/tweetnacl/tweetnacl.h $(TNKX_DIR)/
	@python3 ../apply-mods.py --steps sign_open_alias,verify_wdt,x25519_wdt $(TNKX_DIR) >/dev/null
	@cp $(TNKX_DIR)/src/crypto/tweetnacl.c $(TNKX_DIR)/tweetnacl.c

kx: $(TNKX_DIR)/tweetnacl.c $(CRYPTO_H)
	@echo "== the adoption key exchange, against OpenSSL"
	@$(CC) -O1 -g -std=c99 -w $(SAN) -I$(TNKX_DIR) -c -o /tmp/tn_kx.o $(TNKX_DIR)/tweetnacl.c
	@$(CC) $(CFLAGS) $(SAN) $(INC) -I$(TNKX_DIR) -o /tmp/t_kx test_kx.c \
		../crypto/nab_kx.c ../crypto/sha256.c /tmp/tn_kx.o && /tmp/t_kx

# TLS 1.3 PSK. `tls` replays the recorded OpenSSL session offline
# (test_tls.c) and needs nothing; `tls-interop` runs the live client against
# `openssl s_server` and Python's ssl (tls_interop.py), which needs an
# openssl on PATH and Python 3.13+ (SSLContext.set_psk_server_callback).
tls: $(CRYPTO_H)
	@echo "== TLS 1.3 PSK: the recorded OpenSSL session, replayed and damaged"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_tls test_tls.c \
		../crypto/nab_tls.c ../crypto/sha256.c ../crypto/cmac.c && /tmp/t_tls

tls-interop: $(CRYPTO_H)
	@echo "== TLS 1.3 PSK: live, against OpenSSL and Python"
	@$(CC) $(CFLAGS) $(SAN) $(INC) -o /tmp/t_tls_client tls_client.c \
		../crypto/nab_tls.c ../crypto/sha256.c ../crypto/cmac.c
	@python3 tls_interop.py --client /tmp/t_tls_client

all: $(CRYPTO_H) sign_open uart_fmt sbox kx tls
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
	python3 vectors_tls.py session_openssl.dump > vectors_tls.h

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
	$(ARM_CC) $(ARM_FLAGS) $(INC) -c ../crypto/nab_tls.c -o /tmp/nab_tls.o
	@arm-none-eabi-size /tmp/nab_tls.o

clean:
	rm -rf net /tmp/t_aes /tmp/t_wpa2 /tmp/t_fuzz /tmp/t_p256 \
		/tmp/t_sha256 /tmp/t_cmac /tmp/t_sae
