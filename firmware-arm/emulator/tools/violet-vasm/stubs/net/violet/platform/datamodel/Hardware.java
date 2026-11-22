package net.violet.platform.datamodel;

/** Stand-in: the two hardware kinds Vasm.VASM_FILE maps, with the label that
 * prefixes their program files (Nabaztag_wait.vasm, Daldal_wait.vasm). */
public class Hardware {
	public enum HARDWARE {
		V1("Nabaztag"), DALDAL("Daldal");
		private final String label;
		HARDWARE(String l) { this.label = l; }
		public String getLabel() { return this.label; }
	}
}
