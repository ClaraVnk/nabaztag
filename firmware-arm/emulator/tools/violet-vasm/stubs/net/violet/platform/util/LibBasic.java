package net.violet.platform.util;

import java.io.ByteArrayOutputStream;

/** Stand-in: big-endian writes, as the frames read them. */
public class LibBasic {
	public static void writeIntTo3Bytes(ByteArrayOutputStream out, int v) {
		out.write((v >> 16) & 0xFF); out.write((v >> 8) & 0xFF); out.write(v & 0xFF);
	}
	public static void writeIntTo4Bytes(ByteArrayOutputStream out, int v) {
		out.write((v >> 24) & 0xFF); writeIntTo3Bytes(out, v);
	}
}
