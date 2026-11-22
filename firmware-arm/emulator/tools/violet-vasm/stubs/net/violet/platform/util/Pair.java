package net.violet.platform.util;

/** Stand-in: a map key (opcode, addressing mode), so equals and hashCode matter. */
public class Pair<A, B> {
	private final A first;
	private final B second;
	public Pair(A a, B b) { this.first = a; this.second = b; }
	public A getFirst() { return this.first; }
	public B getSecond() { return this.second; }
	@Override public boolean equals(Object o) {
		if (!(o instanceof Pair)) return false;
		final Pair<?, ?> p = (Pair<?, ?>) o;
		return java.util.Objects.equals(this.first, p.first) && java.util.Objects.equals(this.second, p.second);
	}
	@Override public int hashCode() { return java.util.Objects.hash(this.first, this.second); }
}
