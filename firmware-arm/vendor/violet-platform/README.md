# Violet's own v1 programs (the platform's VASM files)

What Violet's server sent a Nabaztag v1, from Violet's platform source as
published under MIT at
`https://github.com/sebastienhouzet/nabaztag-source-code`, commit
65197ea668e40fadb35d8ebd0aeb512311f9f547 (2015). Copied unchanged
(`LICENSE` is that repository's); files keep their original Latin-1 and CRLF.

- `vasm/Nabaztag_wait.vasm`: the program a v1 ran between messages (Sylvain
  Huet). It reads the 04 frame as eight (service, value) pairs, then
  **ear_left, ear_right**, the message colours and 0, and drives both ears
  with `motor`/`motorget`. Violet served it to firmware 12, 14 and 20 alike
  (`java/MessageSerializer.java`, `computetimeout`: only the poll interval
  depended on the version).
- `vasm/Nabaztag_msg_v0.9.vasm`, `Nabaztag_asleep_v1.0.vasm`,
  `Nabaztag_pointbleu.vasm`, `Nabaztag_demo.vasm`: a message, sleep, the
  blue dot (error), the demo. `Daldal_*` are the same for the Daldal.
- `vasm/audio/*.mid`: the MIDI jingles these programs play.
- `java/Vasm.java`: Violet's assembler for these files (`maketrame`);
  `java/EventPing.java`, `PingV1EntryPoint.java`, `MessageSerializer.java`:
  how a p3.jsp poll was answered; `test/`: Violet's tests of the assembler.

Why here (2026-11-22): our own v1 program moves the ears on the 2.0b
firmware in the emulator but makes a real 1.4b rabbit reboot at every ear
order. These are the programs that worked on every v1 Violet served.
