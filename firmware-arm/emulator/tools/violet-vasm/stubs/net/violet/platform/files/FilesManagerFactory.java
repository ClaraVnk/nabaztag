package net.violet.platform.files;

import java.io.FileNotFoundException;
import java.io.IOException;

/** Stand-in: the platform's file store, which these programs never reach. */
public class FilesManagerFactory {
	public static final FilesManagerFactory FILE_MANAGER = new FilesManagerFactory();
	public byte[] getFilesContent(String name) throws IOException { throw new FileNotFoundException(name); }
}
