# Reading a Nabaztag v1's firmware (ICSP, J2)

A v1's program lives in its PIC18LF6627 (Clara's v1: "PIC18LF6…", date code
0443; Newbaztag read a PIC18F6627 the same way and published its 2.0b
firmware). Clara's rabbit runs 1.4b: reading it gives a second version of
Violet's firmware. **Read only**: never Program or Erase, there is no other
copy.

![J2 to a PICkit](wiring.svg)

`./mkwiring.py -o wiring.svg` regenerates the picture from its table.

- J2 is MCLR, 3.3v, GND, PGD (RB7), PGC (RB6) in Violet's schematic (the FCC
  filing), the order of a PICkit's own pins 1 to 5: five wires straight
  across, PICkit pin 6 free.
- J2 has no pin-1 mark. GND is the middle pin (continuity to the 8 V
  adapter's black wire); with the rabbit powered, pin 2 reads 3.3 V: pin 1
  is the end next to it.
- The rabbit keeps its own 8 V adapter; in MPLAB IPE, "Power target circuit
  from tool" stays off. Both supplies on one earthed strip, or the laptop on
  battery: tying the grounds of two floating switch-mode supplies gave
  leakage shocks on the serial test (2026-11-22).
- MPLAB IPE: device PIC18LF6627, Connect, Read, Export > Hex. If it reads all
  zeros, Violet set code protection on this rabbit and the read stops there.
