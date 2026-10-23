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
FW += ../crypto/aes128.c

# The hardware stubs nabgcc already ships for its own host build of the VM.
# Upstream's work, used as it stands — see README.md.
FW += $(NABGCC)/testvm/stubs/utils/sys.c
FW += $(NABGCC)/testvm/stubs/usb/rt2501usb_io.c
FW += $(NABGCC)/testvm/stubs/usb/usbhcore.c

# The emulator itself.
SIM  = src/sim.c src/vap.c src/vapcrypto.c src/gw.c
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
VM += obj/dumpbc.c
VM += src/vm_main.c

SRC = $(FW) $(SIM) src/wifi_main.c $(VM)
OBJ = $(addprefix obj/,$(notdir $(SRC:.c=.o)))

CFLAGS  = -O1 -g -std=gnu99
CFLAGS += -D_NAB_SIM -DDEBUG -DDEBUG_WIFI
CFLAGS += -I$(NABGCC)/inc -I$(NABGCC)/sys/inc -Isrc -Istubs -Iobj/include -I../crypto
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
obj/include/net/aes128.h: ../crypto/aes128.h
	@mkdir -p $(@D)
	cp $< $@

define emulator_compile
obj/$(notdir $(basename $(1))).o: $(1) obj/include/net/aes128.h
	@mkdir -p obj
	$$(CC) -c $$(CFLAGS) $$(SAN) $$< -o $$@
endef
$(foreach s,$(SRC),$(eval $(call emulator_compile,$(s))))

WIFI_OBJ = $(addprefix obj/,$(notdir $(FW:.c=.o) $(SIM:.c=.o))) obj/wifi_main.o
VM_OBJ   = $(addprefix obj/,$(notdir $(FW:.c=.o) $(SIM:.c=.o) $(VM:.c=.o)))

$(TARGET): $(WIFI_OBJ)
	$(CC) $(SAN) -o $@ $(WIFI_OBJ) -lm

$(VM_TARGET): $(VM_OBJ)
	$(CC) $(SAN) -o $@ $(VM_OBJ) -lm

# The bytecode as a C array. Regenerated whenever the image changes.
obj/dumpbc.c: $(BYTECODE) mkbc.py
	@mkdir -p obj
	python3 mkbc.py $(BYTECODE) -o $@

SCENARIOS = $(sort $(wildcard scenarios/*.txt))

check: $(TARGET)
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
	exit $$fail

# Prove the emulator's own code is clean under the sanitizers. The firmware's
# is not, and that is a separate conversation.
asan:
	$(MAKE) NABGCC=$(NABGCC) SAN="-fsanitize=address,undefined -fno-omit-frame-pointer" check

clean:
	rm -rf obj $(TARGET) $(VM_TARGET)

.PHONY: all vm check asan clean
