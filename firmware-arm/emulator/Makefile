# The rabbit emulator: the Nabaztag's own Wi-Fi stack, on a build machine,
# against a virtual access point.
#
#   make NABGCC=/path/to/nabgcc          build ./rabbit
#   make NABGCC=/path/to/nabgcc check    build, then run every scenario
#   ./rabbit scenarios/open-deauth.txt   one scenario, with the full transcript
#
# NABGCC must point at a nabgcc checkout with firmware-arm's modifications
# already applied — apply-mods.py, and the AES implementation from ../crypto,
# since nabgcc's own src/net/aes128.c is a stub whose every function is empty.
# ./run.sh does all of that in a container and is the easier way in.

TARGET  = rabbit
VM_TARGET = rabbit-vm
NABGCC ?=
# The bytecode image the full-rabbit target links in. Any .hybrid or .bin a
# warren serves will do; mkbc.py strips the header and checks the sections.
BYTECODE ?= ../../nabaztag-violet/bootcode.hybrid

ifeq ($(NABGCC),)
$(error set NABGCC to a nabgcc checkout, or use ./run.sh which prepares one)
endif

# The firmware's own code. Nothing here is reimplemented: this is what runs on
# the rabbit, compiled for the host.
FW  = $(NABGCC)/src/net/ieee80211.c
FW += $(NABGCC)/src/net/eapol.c
FW += $(NABGCC)/src/net/hash.c
FW += $(NABGCC)/src/net/rc4.c
FW += $(NABGCC)/src/usb/rt2501usb_buffer.c
CRYPTO_H := obj/include/net/aes128.h obj/include/net/p256.h \
            obj/include/net/sha256.h obj/include/net/cmac.h \
            obj/include/net/sae.h obj/include/net/nab_kx.h \
            obj/include/net/nab_tls.h obj/include/net/nab_rng.h

FW += ../crypto/aes128.c
# WPA3-SAE. apply-mods' wpa3_sae step also drops these into the nabgcc
# tree, but the emulator builds them from crypto/ directly, the way it
# already does for AES -- one copy, and the host tests use the same one.
FW += ../crypto/p256.c ../crypto/sha256.c ../crypto/cmac.c ../crypto/sae.c
# The entropy pool every secret is drawn from (entropy_pool step).
FW += ../crypto/nab_rng.c

# The hardware stubs nabgcc already ships for its own host build of the VM.
# Upstream's work, used as it stands — see README.md.
FW += $(NABGCC)/testvm/stubs/utils/sys.c
FW += $(NABGCC)/testvm/stubs/usb/rt2501usb_io.c
FW += $(NABGCC)/testvm/stubs/usb/usbhcore.c

# The emulator itself.
SIM  = src/sim.c src/vap.c src/vapcrypto.c src/vaph2e.c src/gw.c src/phone.c
SIM += stubs/console.c stubs/delay.c stubs/hcdmem.c stubs/uart.c stubs/hw.c

# The Metal VM and the hardware it expects, for the full-rabbit target.
VM  = $(NABGCC)/src/vm/vinterp.c
VM += $(NABGCC)/src/vm/vmem.c
VM += $(NABGCC)/src/vm/vloader.c
VM += $(NABGCC)/src/vm/vlog.c
VM += $(NABGCC)/src/vm/vnet.c
VM += $(NABGCC)/src/vm/vaudio.c
# The hardware the bytecode actually touches is ours, not upstream's stubs:
# see the file headers for what the stubs get wrong for this purpose.
VM += stubs/audio.c
VM += $(NABGCC)/testvm/stubs/hal/i2c.c
VM += $(NABGCC)/testvm/stubs/hal/spi.c
# The bootloader bytecode calls verifySig before it flashes anything, so a
# VM pointed at boot.0.0.0.13 needs the opcode's implementation. Harmless
# when the step list leaves verifySig out: nothing then references them.
VM += ../crypto/nab_sig.c
# The adoption opcodes (kxBegin, kxFinish, kxUse), for a VM running the
# bootloader of a mode with adoption_kx; the Containerfile's step list has it.
VM += ../crypto/nab_kx.c ../crypto/nab_kx_vm.c
# resetInfo (crash_report) reads the reset counters; see stubs/counters.c.
VM += stubs/counters.c
# TLS on the XMPP connection (tlsStart..tlsClose), for a VM running a
# runtime bytecode built with bytecode/patch_tls.py against a step list
# with tls_psk; the opcodes are numbered after adoption_kx's.
VM += ../crypto/nab_tls.c ../crypto/nab_tls_vm.c
# The certificate session and the https opcodes (tls_certs, Naboot 1.2.0.0),
# when the step list has it: the step leaves nab_https_vm.c in the tree.
# wifi_diag's line (2026-11-19): the opcode wifiDiag calls nab_wdiag.
ifneq ($(wildcard $(NABGCC)/src/utils/wdiag.c),)
FW += $(NABGCC)/src/utils/wdiag.c
endif
# usb_watch (2026-11-20): the tick is the firmware's; the controller it reads
# is vm_main.c's (usbwatch_frame), which `usb-host-stop` stops.
ifneq ($(wildcard $(NABGCC)/src/utils/usbwatch.c),)
VM += $(NABGCC)/src/utils/usbwatch.c
endif
ifneq ($(wildcard $(NABGCC)/src/net/nab_https_vm.c),)
VM += ../crypto/nab_bn.c ../crypto/nab_rsa.c ../crypto/nab_ecdsa.c ../crypto/nab_x509.c
# The tree's copy of nab_https_vm.c, not ../crypto's: boot_https puts its
# NAB_BOOT_ANCHOR there, as the firmware build compiles it. With ../crypto's
# the VM had no built-in root and refused every first boot (2026-11-13).
VM += ../crypto/nab_sha384.c ../crypto/chachapoly.c $(NABGCC)/src/net/nab_https_vm.c
HAS_ECDSA = 1
endif
# The two flash levers of Naboot 1.2.1.0-lean-tel, read off the tree's
# Makefile as apply-mods.py leaves it, so the emulator runs the TLS the image
# runs: tls_chacha_only compiles AES-GCM out of nab_tls.c (an emulator built
# without the define would bind its XMPP session on a suite the rabbit no
# longer offers -- a proxy that lies), and opt_size_crypto puts the pure
# crypto objects at -Os. Neither is in run.sh's default STEPS; emu-steps.py
# passes them through for the modes that carry them (2026-11-19).
ifneq ($(shell grep -c -- -DNAB_TLS_CHACHA_ONLY $(NABGCC)/Makefile 2>/dev/null),0)
# The define itself joins CFLAGS below, after `CFLAGS =` has run: appended
# here it was overwritten, and the first lean emulator still had GCM in it
# (nm rabbit-vm | grep gcm: 3, 2026-11-19 -- checked by content, not by rc).
CHACHA_ONLY = 1
# tls_certs copies net/chachapoly.h into the tree; a step list without it
# (this one) gets the header the way the others come, from ../crypto.
CRYPTO_H += obj/include/net/chachapoly.h
ifeq ($(filter ../crypto/chachapoly.c,$(VM)),)
VM += ../crypto/chachapoly.c
endif
endif
ifneq ($(shell grep -c '^# opt_size_crypto' $(NABGCC)/Makefile 2>/dev/null),0)
CRYPTO_OS = p256 sae sha256 cmac aes128 nab_kx nab_kx_vm nab_tls nab_tls_vm nab_https_vm \
            nab_rng nab_bn nab_rsa nab_ecdsa nab_x509 nab_sha384 chachapoly tweetnacl nab_sig hash
$(addsuffix .o,$(addprefix obj/,$(CRYPTO_OS))): CFLAGS += -Os
endif
# The tree's own copy when the container staged one (and sign_open_alias
# patched it), the vendored one otherwise.
TWEETNACL ?= $(firstword $(wildcard $(NABGCC)/src/crypto/tweetnacl.c) ../upstream/tweetnacl/tweetnacl.c)
VM += $(TWEETNACL)
VM += obj/dumpbc.c
VM += src/vm_main.c

SRC = $(FW) $(SIM) src/wifi_main.c $(VM)
OBJ = $(addprefix obj/,$(notdir $(SRC:.c=.o)))

CFLAGS  = -O1 -g -std=gnu99
CFLAGS += -D_NAB_SIM -DDEBUG -DDEBUG_WIFI
CFLAGS += -I$(NABGCC)/inc -I$(NABGCC)/sys/inc -Isrc -Istubs -Iobj/include -I../crypto -I../keys -I../upstream/tweetnacl -I../tests
# Declarations the firmware uses without declaring; see the header.
CFLAGS += -include src/prelude.h
# The 2006 sources predate most of these diagnostics and the emulator exists
# to run them, not to tidy them. Nothing here hides a mistake that matters:
# the pointer-to-int casts are ieee80211.c computing a frame length, and the
# fallthroughs are its rate tables.
CFLAGS += -Wall -Wextra -Wno-unused-parameter -Wno-sign-compare
CFLAGS += -Wno-pointer-to-int-cast -Wno-implicit-fallthrough -Wno-format
CFLAGS += -Wno-unused-but-set-variable -Wno-return-type -Wno-pointer-sign
CFLAGS += -Wno-missing-field-initializers -Wno-unused-function
ifdef HAS_ECDSA
CFLAGS += -DNAB_HAS_ECDSA
endif
ifdef CHACHA_ONLY
CFLAGS += -DNAB_TLS_CHACHA_ONLY
endif

SAN ?=

# No VPATH, and that is deliberate. A nabgcc tree contains two files called
# rt2501usb_buffer.c and two called usbhcore.c: the real driver under src/usb,
# and a hollow stub under testvm/stubs/usb whose functions return whatever was
# in the return register. A search path picks one of them by accident. Here
# every source is named by its full path and compiled by its own rule, so the
# choice between the real receive queue and the stub is written down.
CC ?= cc

all: $(TARGET)

vm: $(VM_TARGET)

# eapol.c includes <net/aes128.h>, so the AES header has to sit under a net/
# directory. ../crypto keeps it flat, as the ARM build's Dockerfile does.
obj/include/net/%.h: ../crypto/%.h
	@mkdir -p $(@D)
	cp $< $@

define emulator_compile
obj/$(notdir $(basename $(1))).o: $(1) $(CRYPTO_H) obj/.tree-stamp
	@mkdir -p obj
	$$(CC) -c $$(CFLAGS) $$(SAN) $$< -o $$@
endef
$(foreach s,$(SRC),$(eval $(call emulator_compile,$(s))))

WIFI_OBJ = $(addprefix obj/,$(notdir $(FW:.c=.o) $(SIM:.c=.o))) obj/wifi_main.o
VM_OBJ   = $(addprefix obj/,$(notdir $(FW:.c=.o) $(SIM:.c=.o) $(VM:.c=.o)))

$(TARGET): $(WIFI_OBJ)
	$(CC) $(SAN) -o $@ $(WIFI_OBJ) -lm

# The signature gate is watched from vm_main.c through a linker wrap, so the
# heap and (--crypto-time) the crypto's cost on the rabbit are in the transcript;
# heap at the moment verifySig runs is in the transcript without touching
# the firmware's own sources. GNU ld only; macOS's linker has no --wrap and
# the wrapper then simply goes unused.
ifeq ($(shell uname -s),Linux)
VM_LDFLAGS = -Wl,--wrap=nab_verify_sig -Wl,--wrap=crypto_scalarmult_curve25519_tweet
# Only when nab_ecdsa.c is linked: a step list without the https steps (the
# emulator image's default) failed to link on the wrapper (2026-11-14).
ifdef HAS_ECDSA
VM_LDFLAGS += -Wl,--wrap=nab_ecdsa_verify
endif
endif

$(VM_TARGET): $(VM_OBJ)
	$(CC) $(SAN) $(VM_LDFLAGS) -o $@ $(VM_OBJ) -lm

# The bytecode as a C array. Regenerated whenever the image changes *or the
# path does*: the stamp holds the last BYTECODE compiled in, because without
# it `make BYTECODE=a vm` followed by `make BYTECODE=b vm` silently keeps a —
# obj/dumpbc.c is newer than b, so make has nothing to do. Two runs then
# compare one image against itself and agree, which is exactly how a test
# comes out green for the wrong reason.
obj/.bytecode-stamp: FORCE
	@mkdir -p obj
	@printf '%s\n' "$(abspath $(BYTECODE))" > $@.new
	@cmp -s $@.new $@ 2>/dev/null || mv $@.new $@
	@rm -f $@.new

# The same trap one level up, and it bit twice: changing NABGCC does not make
# any object out of date, so `make NABGCC=patched` after `make NABGCC=stock`
# relinks the *patched* objects and the comparison agrees with itself. Every
# object depends on this stamp.
obj/.tree-stamp: FORCE
	@mkdir -p obj
	@printf '%s\n' "$(abspath $(NABGCC))" > $@.new
	@cmp -s $@.new $@ 2>/dev/null || mv $@.new $@
	@rm -f $@.new

obj/dumpbc.c: $(BYTECODE) mkbc.py obj/.bytecode-stamp
	@mkdir -p obj
	python3 mkbc.py $(BYTECODE) -o $@

FORCE:

SCENARIOS = $(sort $(wildcard scenarios/*.txt))

# The config page the WPA3 scenarios boot from. SAE reads its passphrase out
# of offset 92, exactly as the bootloader's wifiAuth does, so the scenarios
# cannot pass by being handed a password the firmware would not have.
#
# With the entropy pool's seed in it: a rabbit that has booted before, whose
# pool is ready at init. The first-boot page has none, and the one scenario
# that boots from it is the one that measures the wait (wpa3-sae-first-boot).
# ./rabbit keeps both as fixtures -- a seed the run stores stays in memory.
obj/conf/wpa3.eeprom: mkconf.py | obj
	@mkdir -p obj/conf
	python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 --rng-seed fixed -o $@

obj/conf/wpa3-first-boot.eeprom: mkconf.py | obj
	@mkdir -p obj/conf
	python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 -o $@

CONFS = obj/conf/wpa3.eeprom obj/conf/wpa3-first-boot.eeprom

check: $(TARGET) $(CONFS)
	@mkdir -p obj/logs; fail=0; \
	for s in $(SCENARIOS); do \
	  log=obj/logs/$$(basename $$s .txt).log; \
	  if ./$(TARGET) "$$s" > "$$log" 2>&1; then \
	    printf 'PASS  %-34s %s\n' "$$s" \
	      "$$(sed -n 's/^final state *//p' $$log)"; \
	  else \
	    printf 'FAIL  %s\n' "$$s"; sed -n '/FAIL/p' "$$log"; fail=1; \
	  fi; \
	done; \
	echo; echo "transcripts in obj/logs/"; \
	tools/rng-boots.sh || fail=1; \
	tools/sae-downgrade.sh || fail=1; \
	exit $$fail

# Prove the emulator's own code is clean under the sanitizers. The firmware's
# is not, and that is a separate conversation.
asan:
	$(MAKE) NABGCC=$(NABGCC) SAN="-fsanitize=address,undefined -fno-omit-frame-pointer" check

clean:
	rm -rf obj $(TARGET) $(VM_TARGET)

.PHONY: all vm check asan clean FORCE
