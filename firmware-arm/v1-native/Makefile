# The v1's native firmware, built with SDCC's pic16 port (Containerfile).
#
#   podman build -t v1native-toolchain -f Containerfile .
#   podman run --rm -v "$PWD":/src:Z -w /src v1native-toolchain make
#   -> out/18f6525/loader.hex      the boot block, linked at 0 (CONFIG bytes in it)
#      out/18f6525/app-30.hex      the application, linked from 0x3000
#      out/18f6525/full.hex        loader + application + the test key in the
#                                  data EEPROM: what ICSP would program, what
#                                  the emulator runs
#      out/18f6525/app-30.img      the application as the Terrier serves it
#                                  (64-byte header with the HMAC, then the image)
#      the sizes on stdout
#
#   make DEV=18f6627               the 2.0b board (PIC18F6627, 96 KB)
#   make app image APP_VERSION=31  another application version (the update proof)
#   make app image APP_VERSION=32 APP_TAG=32-broken APP_DEFS=-DAPP_BROKEN
#
# --obanksel=2 and the peephole optimiser are what shrink SDCC's output most;
# no interrupt, no recursion, the default stack.
DEV         ?= 18f6525
APP_VERSION ?= 30
APP_TAG     ?= $(APP_VERSION)
APP_DEFS    ?=
KEY         ?= keys/test-rabbit.key

ifeq ($(DEV),18f6627)
BOARD_ID = 0x27
FLASH_END = 0x18000UL
WRITE_BLOCK = 64
else
BOARD_ID = 0x25
FLASH_END = 0xC000UL
WRITE_BLOCK = 8
endif

O       = out/$(DEV)
CC      = sdcc -mpic16 -p$(DEV) --use-non-free
CFLAGS  = --obanksel=2 --optimize-cmp --optimize-df --denable-peeps -Isrc \
          -DBOARD_ID=$(BOARD_ID) -DFLASH_END=$(FLASH_END) -DWRITE_BLOCK=$(WRITE_BLOCK)
COMMON  = src/common/uart.c src/common/flash.c src/common/state.c
LSRC    = src/loader/loader.c src/loader/sha256.c $(COMMON)
ASRC    = $(wildcard src/app/*.c) $(COMMON)
APP_BASE = 0x3000
LOBJ    = $(patsubst src/%.c,$(O)/obj-loader/%.o,$(LSRC))
AOBJ    = $(patsubst src/%.c,$(O)/obj-app-$(APP_TAG)/%.o,$(ASRC)) $(O)/obj-app-$(APP_TAG)/app/entry.o
LOADER  = $(O)/loader.hex
APP     = $(O)/app-$(APP_TAG).hex
IMG     = $(O)/app-$(APP_TAG).img

all: loader app full image size

loader: $(LOADER)
app: $(APP)
full: $(O)/full.hex
image: $(IMG)

$(O)/obj-loader/%.o: src/%.c src/board.h src/loader/sha256.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -DLOADER -c $< -o $@

$(O)/obj-app-$(APP_TAG)/%.o: src/%.c src/board.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) --ivt-loc=$(APP_BASE) -DAPP_VERSION=$(APP_VERSION) $(APP_DEFS) -c $< -o $@

$(O)/obj-app-$(APP_TAG)/app/entry.o: src/app/entry.asm
	@mkdir -p $(dir $@)
	gpasm -p$(DEV) -c -D APP_BASE=$(APP_BASE) -o $@ $<

$(LOADER): $(LOBJ) loader-$(DEV).lkr
	$(CC) $(CFLAGS) -Wl,-s,loader-$(DEV).lkr -Wl,-m -o $@ $(LOBJ)

$(APP): $(AOBJ) app-$(DEV).lkr
	$(CC) $(CFLAGS) --ivt-loc=$(APP_BASE) -Wl,-s,app-$(DEV).lkr -Wl,-m -o $@ $(AOBJ)

# The length patched into the application's header, the HMAC with the key.
$(IMG): $(APP) $(KEY) mkimage.py
	python3 mkimage.py image --app $(APP) --key $(KEY) --out $@

$(O)/full.hex: $(LOADER) $(APP) $(KEY) mkimage.py
	python3 mkimage.py full --loader $(LOADER) --app $(APP) --key $(KEY) --out $@

# Flash: every data byte the hex places below the configuration words.
# RAM: the linker map's data sections (udata, idata, the stack).
size: $(LOADER) $(APP)
	@echo "loader:"; python3 size.py $(LOADER) $(O)/loader.map
	@echo "app-$(APP_TAG):"; python3 size.py $(APP) $(O)/app-$(APP_TAG).map

# The loader's SHA-256 on the host against Python's hmac (needs a C compiler
# and python3 on the host, not the container): the two sigma bugs of the
# first build were found here, not on the PIC (2026-11-24).
host-check: tools/sha256-host.c src/loader/sha256.c
	@mkdir -p out
	cc -Wall -Wno-unused-function -I src -o out/sha256-host tools/sha256-host.c
	@out/sha256-host > out/sha256-host.txt
	@python3 -c 'import hmac,hashlib; k=bytes(range(32)); print(hmac.new(k,b"abc",hashlib.sha256).hexdigest()); print(hmac.new(k,bytes((i*7+3)&255 for i in range(1000)),hashlib.sha256).hexdigest())' > out/sha256-py.txt
	@cmp out/sha256-host.txt out/sha256-py.txt && echo "sha256: the loader's HMAC-SHA-256 matches Python's"

clean:
	rm -rf out

.PHONY: all loader app full image size host-check clean
