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
NABGCC ?=

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
FW += $(NABGCC)/testvm/stubs/hal/uart.c
FW += $(NABGCC)/testvm/stubs/hal/led.c
FW += $(NABGCC)/testvm/stubs/utils/mem.c
FW += $(NABGCC)/testvm/stubs/utils/sys.c
FW += $(NABGCC)/testvm/stubs/usb/rt2501usb_io.c
FW += $(NABGCC)/testvm/stubs/usb/usbhcore.c

# The emulator itself.
SIM  = src/wifi_main.c src/sim.c src/vap.c
SIM += stubs/console.c stubs/delay.c stubs/hcdmem.c

SRC = $(FW) $(SIM)
OBJ = $(addprefix obj/,$(notdir $(SRC:.c=.o)))

CFLAGS  = -O1 -g -std=gnu99
CFLAGS += -D_NAB_SIM -DDEBUG -DDEBUG_WIFI
CFLAGS += -I$(NABGCC)/inc -I$(NABGCC)/sys/inc -Isrc -Iobj/include -I../crypto
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

$(TARGET): $(OBJ)
	$(CC) $(SAN) -o $@ $(OBJ) -lm

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
	rm -rf obj $(TARGET)

.PHONY: all check asan clean
