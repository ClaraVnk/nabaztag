package net.violet.platform.datamodel;

/** Stand-in: only what Vasm.getfilebytes asks (a broadcast file is never used here). */
public class Files {
	public enum CATEGORIES {
		BROADCAST("broadcast/");
		private final String category;
		CATEGORIES(String c) { this.category = c; }
		public String getCategory() { return this.category; }
	}
}
