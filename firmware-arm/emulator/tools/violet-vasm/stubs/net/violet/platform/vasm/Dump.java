package net.violet.platform.vasm;

import java.io.ByteArrayOutputStream;

/**
 * Assemble one of Violet's v1 programs with Violet's own Vasm.maketrame and
 * write the 05 frame ("amber" ... "mind") to stdout:
 *
 *     java ... net.violet.platform.vasm.Dump Nabaztag_wait.vasm [ID_HEX [PRIORITY [DATA]]]
 *
 * DATA replaces "<data>" in the source, as MessageSerializer does for a message.
 */
public class Dump {
	public static void main(String[] args) throws Exception {
		final String name = args[0];
		final int id = args.length > 1 ? (int) Long.parseLong(args[1], 16) : 0x7FFFFFFF;
		final int priority = args.length > 2 ? Integer.parseInt(args[2]) : 0;
		final String data = args.length > 3 ? args[3] : null;
		final ByteArrayOutputStream frame = Vasm.maketrame(name, data, id, priority, Vasm.VASM_FILE.V1);
		System.out.write(frame.toByteArray());
		System.out.flush();
	}
}
