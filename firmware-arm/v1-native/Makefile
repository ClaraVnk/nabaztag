# The v1's native firmware, built with SDCC's pic16 port (Containerfile).
#
#   podman build -t v1native-toolchain -f Containerfile .
#   podman run --rm -v "$PWD":/src:Z -w /src v1native-toolchain make
#   -> out/nabv1native.hex, out/nabv1native.map, and the sizes on stdout
#
# PIC18LF6525: 48 KB of flash, 3 968 bytes of RAM. --obanksel=2 and the
# peephole optimiser are what shrink SDCC's output most; the stack is the
# default 64 bytes (no interrupt, no recursion).
DEV     = 18f6525
CC      = sdcc -mpic16 -p$(DEV) --use-non-free
CFLAGS  = --obanksel=2 --optimize-cmp --optimize-df --denable-peeps -Isrc
LDFLAGS = -Wl,-s,18f6525-native.lkr -Wl,-m
SRC     = $(wildcard src/*.c)
OBJ     = $(patsubst src/%.c,out/%.o,$(SRC))

all: out/nabv1native.hex size

out/%.o: src/%.c src/board.h
	@mkdir -p out
	$(CC) $(CFLAGS) -c $< -o $@

out/nabv1native.hex: $(OBJ) 18f6525-native.lkr
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJ)

# Flash: every data byte the hex places below the configuration words.
# RAM: the linker map's data sections (udata, idata, the stack).
size: out/nabv1native.hex
	@python3 size.py out/nabv1native.hex out/nabv1native.map

clean:
	rm -rf out

.PHONY: all size clean
