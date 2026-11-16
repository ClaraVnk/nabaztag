# nabv1: Violet's 2005 firmware for the Nabaztag v1, on a build machine.
#
#   make                 build ./nabv1
#   make hex             Violet's firmware, from vendor/newbaztag, checked
#   ./nabv1 --hex nabaztag.hex --seconds 30 --warren 127.0.0.1 --port80 8080

CFLAGS ?= -std=c11 -D_DEFAULT_SOURCE -O2 -Wall -Wextra
SRC = pic18.c board.c spi.c prism.c ears.c net.c main.c ../src/gw.c

# Violet's firmware, read from a real v1 over ICSP and published by
# Newbaztag (MIT), vendored in ../../vendor/newbaztag (see its README).
HEX = ../../vendor/newbaztag/firmware/original/nabaztag.hex
HEX_SHA256 = f1f78e557484fea6da185f9527b7a5ad4c60ac247f5422a276ad3d5324dbb1e0

nabv1: $(SRC) *.h ../src/gw.h
	$(CC) $(CFLAGS) -Ishim -o $@ $(SRC)

hex: nabaztag.hex
nabaztag.hex: $(HEX)
	echo "$(HEX_SHA256)  $(HEX)" | sha256sum -c -
	cp $(HEX) $@

clean:
	rm -f nabv1 nabaztag.hex

.PHONY: hex clean
