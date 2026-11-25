#!/usr/bin/env python3
"""A PIC18 disassembler for the v1's two firmwares, and a diff by function.

    pic18dis.py dis     <hex> [START END]      the listing (byte addresses)
    pic18dis.py funcs   <hex>                  call targets, with size and callers
    pic18dis.py strings <hex>                  ASCII runs of 6 or more
    pic18dis.py dead    <hex>                  code no path from the vectors reaches
    pic18dis.py diff    <hex A> <hex B>        functions of A found/missing in B
    pic18dis.py refs    <hex> ADDR             who calls or jumps to ADDR

Why (2026-11-24): Lazare's 1.4b (PIC18LF6525) has no public disassembly;
Newbaztag's nabaztag_.asm covers 2.0b only. A diff by byte between the two
says nothing (everything moved); a diff by function says what one has and
the other has not. The standard instruction set only (CONFIG4L has XINST
off in both dumps). Checked against Newbaztag's PICDIS18 listing on 2.0b:
same mnemonics and operands at every address of the OKI and poll code.

A "function" is a target of CALL or RCALL; its body runs to the first
RETURN/RETLW/GOTO/BRA-backwards past which no branch from inside reaches,
which is what a compiler-free, hand-written firmware gives. The fingerprint
is the sequence of mnemonics with literals, with every address (code and
RAM) masked, so a function keeps its fingerprint when the linker moves it
and when its variables move.
"""
import sys
from collections import defaultdict

BYTE_OPS = {0x10: "iorwf", 0x14: "andwf", 0x18: "xorwf", 0x1C: "comf", 0x20: "addwfc",
            0x24: "addwf", 0x28: "incf", 0x2C: "decfsz", 0x30: "rrcf", 0x34: "rlcf",
            0x38: "swapf", 0x3C: "incfsz", 0x40: "rrncf", 0x44: "rlncf", 0x48: "infsnz",
            0x4C: "dcfsnz", 0x50: "movf", 0x54: "subfwb", 0x58: "subwfb", 0x5C: "subwf",
            0x04: "decf"}
ONE_A_OPS = {0x60: "cpfslt", 0x62: "cpfseq", 0x64: "cpfsgt", 0x66: "tstfsz", 0x68: "setf",
             0x6A: "clrf", 0x6C: "negf", 0x6E: "movwf", 0x02: "mulwf"}
LIT_OPS = {0x08: "sublw", 0x09: "iorlw", 0x0A: "xorlw", 0x0B: "andlw", 0x0C: "retlw",
           0x0D: "mullw", 0x0E: "movlw", 0x0F: "addlw"}
BIT_OPS = {0x7: "btg", 0x8: "bsf", 0x9: "bcf", 0xA: "btfss", 0xB: "btfsc"}
SKIPS = {"decfsz", "incfsz", "dcfsnz", "infsnz", "btfss", "btfsc", "cpfslt", "cpfseq", "cpfsgt", "tstfsz"}
CC = ["bz", "bnz", "bc", "bnc", "bov", "bnov", "bn", "bnn"]
SFR = {0xFD8: "STATUS", 0xFE8: "WREG", 0xFE9: "FSR0L", 0xFEA: "FSR0H", 0xFEE: "POSTINC0",
       0xFED: "POSTDEC0", 0xFEF: "INDF0", 0xFEC: "PREINC0", 0xFEB: "PLUSW0",
       0xFE1: "FSR1L", 0xFE2: "FSR1H", 0xFE6: "POSTINC1", 0xFE5: "POSTDEC1", 0xFE7: "INDF1",
       0xFD9: "FSR2L", 0xFDA: "FSR2H", 0xFDE: "POSTINC2", 0xFDD: "POSTDEC2", 0xFDF: "INDF2",
       0xFF3: "PRODL", 0xFF4: "PRODH", 0xFF5: "TABLAT", 0xFF6: "TBLPTRL", 0xFF7: "TBLPTRH",
       0xFF8: "TBLPTRU", 0xFE0: "BSR", 0xFC7: "SSPSTAT", 0xFC6: "SSPCON1", 0xFC9: "SSPBUF",
       0xFCA: "T2CON", 0xFCB: "PR2", 0xFBD: "CCP1CON", 0xFBE: "CCPR1L", 0xFCD: "T1CON",
       0xFCE: "TMR1L", 0xFCF: "TMR1H", 0xFD5: "T0CON", 0xFD6: "TMR0L", 0xFD7: "TMR0H",
       0xF80: "PORTA", 0xF81: "PORTB", 0xF82: "PORTC", 0xF83: "PORTD", 0xF84: "PORTE",
       0xF85: "PORTF", 0xF86: "PORTG", 0xF89: "LATA", 0xF8A: "LATB", 0xF8B: "LATC",
       0xF8C: "LATD", 0xF8D: "LATE", 0xF8E: "LATF", 0xF8F: "LATG", 0xF92: "TRISA",
       0xF93: "TRISB", 0xF94: "TRISC", 0xF95: "TRISD", 0xF96: "TRISE", 0xF97: "TRISF",
       0xF98: "TRISG", 0xFAB: "RCSTA1", 0xFAC: "TXSTA1", 0xFAD: "TXREG1", 0xFAE: "RCREG1",
       0xFAF: "SPBRG1", 0xFC0: "ADCON2", 0xFC1: "ADCON1", 0xFC2: "ADCON0", 0xFC3: "ADRESL",
       0xFC4: "ADRESH", 0xFD0: "RCON", 0xFD1: "WDTCON", 0xFD2: "LVDCON", 0xFD3: "OSCCON",
       0xFF2: "INTCON", 0xFF1: "INTCON2", 0xFF0: "INTCON3", 0xF9D: "PIE1", 0xF9E: "PIR1",
       0xFA6: "EECON1", 0xFA7: "EECON2", 0xFA8: "EEDATA", 0xFA9: "EEADR", 0xFAA: "EEADRH",
       0xFB1: "T3CON", 0xFB2: "TMR3L", 0xFB3: "TMR3H"}


def load_hex(path):
    mem, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        b = bytes.fromhex(line[1:])
        n, addr, kind = b[0], (b[1] << 8) | b[2], b[3]
        if kind == 4:
            base = ((b[4] << 8) | b[5]) << 16
        elif kind == 0:
            for i in range(n):
                mem[base + addr + i] = b[4 + i]
    return mem


def word(mem, a):
    return mem.get(a, 0xFF) | (mem.get(a + 1, 0xFF) << 8)


def freg(f, a):
    """An access-bank address (a=0) is 0x000-0x05F or an SFR at 0xF60-0xFFF."""
    if a == 0 and f >= 0x60:
        return SFR.get(0xF00 | f, "0x%03X" % (0xF00 | f))
    return "0x%02X" % f


def decode(mem, pc):
    """-> (size, mnemonic, operand text, fingerprint, flow)
    flow: ('call'|'goto'|'bra'|'ret'|None, target)"""
    w = word(mem, pc)
    hi = w >> 8
    if w == 0x0000: return 2, "nop", "", "nop", (None, None)
    if w == 0x0003: return 2, "sleep", "", "sleep", (None, None)
    if w == 0x0004: return 2, "clrwdt", "", "clrwdt", (None, None)
    if w == 0x0007: return 2, "daw", "", "daw", (None, None)
    if 0x0008 <= w <= 0x000B: return 2, ["tblrd*", "tblrd*+", "tblrd*-", "tblrd+*"][w - 8], "", "tblrd", (None, None)
    if 0x000C <= w <= 0x000F: return 2, ["tblwt*", "tblwt*+", "tblwt*-", "tblwt+*"][w - 12], "", "tblwt", (None, None)
    if w in (0x0010, 0x0011): return 2, "retfie", "s" if w & 1 else "", "retfie", ("ret", None)
    if w in (0x0012, 0x0013): return 2, "return", "s" if w & 1 else "", "return", ("ret", None)
    if w == 0x00FF: return 2, "reset", "", "reset", ("ret", None)
    if hi == 0x01: return 2, "movlb", "%d" % (w & 0xF), "movlb %d" % (w & 0xF), (None, None)
    if hi in LIT_OPS:
        m = LIT_OPS[hi]
        return 2, m, "0x%02X" % (w & 0xFF), "%s %02X" % (m, w & 0xFF), ("ret" if m == "retlw" else None, None)
    if (hi & 0xFC) in BYTE_OPS and hi >= 0x04:
        m = BYTE_OPS[hi & 0xFC]
        d, a, f = (w >> 9) & 1, (w >> 8) & 1, w & 0xFF
        return 2, m, "%s,%s%s" % (freg(f, a), "f" if d else "W", ",B" if a else ""), "%s %s%s" % (m, "f" if d else "W", "B" if a else ""), (None, None)
    if (hi & 0xFE) in ONE_A_OPS:
        m = ONE_A_OPS[hi & 0xFE]
        a, f = (w >> 8) & 1, w & 0xFF
        return 2, m, "%s%s" % (freg(f, a), ",B" if a else ""), "%s%s" % (m, "B" if a else ""), (None, None)
    if (hi >> 4) in BIT_OPS:
        m = BIT_OPS[hi >> 4]
        b, a, f = (w >> 9) & 7, (w >> 8) & 1, w & 0xFF
        return 2, m, "%s,%d%s" % (freg(f, a), b, ",B" if a else ""), "%s %d%s" % (m, b, "B" if a else ""), (None, None)
    if hi >> 4 == 0xC:
        w2 = word(mem, pc + 2)
        src, dst = w & 0xFFF, w2 & 0xFFF
        return 4, "movff", "%s,%s" % (SFR.get(src, "0x%03X" % src), SFR.get(dst, "0x%03X" % dst)), "movff", (None, None)
    if hi >> 3 == 0x1A:                       # 0xD0-0xD7 bra
        n = w & 0x7FF
        if n & 0x400: n -= 0x800
        t = pc + 2 + 2 * n
        return 2, "bra", "0x%04X" % t, "bra", ("bra", t)
    if hi >> 3 == 0x1B:                       # 0xD8-0xDF rcall
        n = w & 0x7FF
        if n & 0x400: n -= 0x800
        t = pc + 2 + 2 * n
        return 2, "rcall", "0x%04X" % t, "rcall", ("call", t)
    if 0xE0 <= hi <= 0xE7:
        n = w & 0xFF
        if n & 0x80: n -= 0x100
        t = pc + 2 + 2 * n
        return 2, CC[hi - 0xE0], "0x%04X" % t, CC[hi - 0xE0], ("cbra", t)
    if hi in (0xEC, 0xED):
        w2 = word(mem, pc + 2)
        t = ((w & 0xFF) | ((w2 & 0xFFF) << 8)) * 2
        return 4, "call", "0x%04X%s" % (t, ",s" if hi & 1 else ""), "call", ("call", t)
    if hi == 0xEE:
        w2 = word(mem, pc + 2)
        f = (w >> 4) & 3
        k = ((w & 0xF) << 8) | (w2 & 0xFF)
        return 4, "lfsr", "%d,0x%03X" % (f, k), "lfsr %d" % f, (None, None)
    if hi == 0xEF:
        w2 = word(mem, pc + 2)
        t = ((w & 0xFF) | ((w2 & 0xFFF) << 8)) * 2
        return 4, "goto", "0x%04X" % t, "goto", ("goto", t)
    if hi >= 0xF0: return 2, "nop", "(2nd word)", "nop2", (None, None)
    return 2, "dw", "0x%04X" % w, "dw", (None, None)


def program_end(mem):
    return max(a for a in mem if a < 0x200000) + 1


def walk(mem, start=0, end=None):
    """Linear sweep from `start`: the firmwares have no data in the code
    stream except the second words of 4-byte instructions."""
    end = end or program_end(mem)
    pc = start
    while pc < end:
        size, m, ops, fp, flow = decode(mem, pc)
        yield pc, size, m, ops, fp, flow
        pc += size


def analyse(mem):
    insns = {pc: (size, m, ops, fp, flow) for pc, size, m, ops, fp, flow in walk(mem)}
    calls, jumps = defaultdict(set), defaultdict(set)
    for pc, (size, m, ops, fp, (kind, t)) in insns.items():
        if kind == "call": calls[t].add(pc)
        elif kind in ("goto", "bra", "cbra"): jumps[t].add(pc)
    return insns, calls, jumps


def body(insns, start):
    """The function's instructions, by the rule in the docstring."""
    out, pc, reach = [], start, start
    while pc in insns:
        size, m, ops, fp, (kind, t) = insns[pc]
        out.append((pc, m, ops, fp))
        if kind in ("bra", "cbra") and t is not None and t > reach and t - pc < 0x400:
            reach = t
        if kind in ("ret", "goto", "bra") and pc >= reach and not (out[-2:-1] and out[-2][1] in SKIPS):
            break
        pc += size
        if len(out) > 4000: break
    return out


def fingerprint(fn):
    return "|".join(fp for _, _, _, fp in fn)


def strings(mem, minimum=6):
    out, run, at = [], bytearray(), None
    end = program_end(mem)
    for a in range(end):
        c = mem.get(a, 0xFF)
        if 0x20 <= c < 0x7F:
            if not run: at = a
            run.append(c)
        else:
            if len(run) >= minimum: out.append((at, run.decode()))
            run = bytearray()
    return out


def cmd_dis(path, start=0, end=None):
    mem = load_hex(path)
    insns, calls, jumps = analyse(mem)
    for pc, size, m, ops, fp, flow in walk(mem, start, end):
        tag = ""
        if pc in calls: tag = "  ; <- call %s" % ",".join("%04X" % c for c in sorted(calls[pc])[:6])
        elif pc in jumps: tag = "  ; <- %s" % ",".join("%04X" % c for c in sorted(jumps[pc])[:6])
        raw = " ".join("%02X%02X" % (mem.get(pc + i + 1, 0xFF), mem.get(pc + i, 0xFF)) for i in range(0, size, 2))
        print("%05X  %-9s %-8s %s%s" % (pc, raw, m, ops, tag))


def cmd_funcs(path):
    mem = load_hex(path)
    insns, calls, jumps = analyse(mem)
    print("%d functions, program up to 0x%04X" % (len(calls), program_end(mem) - 1))
    for t in sorted(calls):
        fn = body(insns, t)
        print("%05X  %4d B  %3d callers  first %s" % (t, sum(insns[p][0] for p, *_ in fn), len(calls[t]), " ".join(m for _, m, _, _ in fn[:5])))


def cmd_refs(path, addr):
    mem = load_hex(path)
    insns, calls, jumps = analyse(mem)
    a = int(addr, 16)
    print("calls:", " ".join("%04X" % c for c in sorted(calls.get(a, ()))))
    print("jumps:", " ".join("%04X" % c for c in sorted(jumps.get(a, ()))))


def cmd_dead(path):
    """Code no path from the reset and interrupt vectors reaches: what could
    be reclaimed. The walk follows calls and branches and falls through
    after everything but return/goto/bra; after the one computed jump
    (`movwf PCL`, the VM's opcode dispatch) it goes on into the table that
    follows, which is how that table is laid out. Text and tables read with
    TBLRD are not code: a range that is mostly printable is marked so."""
    mem = load_hex(path)
    end = program_end(mem)
    seen, todo = {}, [0x0000, 0x0008, 0x0018]
    # The VM's opcode dispatch: `mullw N; movlw lo; addwf PRODL; movwf TBLPTRL;
    # movlw hi; addwfc PRODH; movwf TBLPTRH`, then TBLRD into PCLATH/PCL. The
    # table of N-byte addresses (2 on the 48 KB part, 3 on the 96 KB one) is
    # read here and its entries followed (1.4b: 0x16F3, 2.0b: 0x1CFF, 200 each).
    lin = list(walk(mem))
    for i in range(len(lin) - 7):
        seq = lin[i:i + 7]
        if (seq[0][2] == "mullw" and seq[1][2] == "movlw" and seq[2][3] == "PRODL,W" and seq[3][3] == "TBLPTRL"
                and seq[4][2] == "movlw" and seq[5][3] == "PRODH,W" and seq[6][3] == "TBLPTRH"):
            n = int(seq[0][3], 16)
            base = (int(seq[4][3], 16) << 8) | int(seq[1][3], 16)
            count = 256
            for back in range(1, 6):
                if lin[i - back][2] == "movlw": count = int(lin[i - back][3], 16); break
            for k in range(count):
                e = base + n * k
                t = mem.get(e, 0) | (mem.get(e + 1, 0) << 8) | ((mem.get(e + 2, 0) << 16) if n == 3 else 0)
                if t < end: todo.append(t)
            print("jump table at 0x%04X: %d entries of %d bytes (dispatch at 0x%04X)" % (base, count, n, seq[0][0]))
    # Recursive descent, decoding at each reached address: the linear sweep
    # loses its alignment on filler words between functions.
    while todo:
        pc, skip, table = todo.pop(), False, False
        while 0 <= pc < end and pc not in seen:
            size, m, ops, fp, (kind, t) = decode(mem, pc)
            seen[pc] = size
            if kind in ("call", "goto", "bra", "cbra") and t is not None and t < end: todo.append(t)
            if kind in ("ret", "goto", "bra"):
                if skip: todo.append(pc + size)     # `decfsz; goto loop`: the skip lands past the goto
                if not (table and kind != "ret"): break   # a jump table: one goto per entry
            skip = m in SKIPS
            if ops.endswith("0xFF9") and m in ("movwf", "addwf"): table = True   # PCL written: a table follows
            elif kind not in ("goto", "bra"): table = False
            pc += size
    covered = set()
    for pc, size in seen.items():
        for i in range(size): covered.add(pc + i)
    dead, a = [], 0
    while a < end:
        if a in covered: a += 1; continue
        b = a
        while b < end and b not in covered: b += 1
        text = sum(1 for x in range(a, b) if 0x20 <= mem.get(x, 0) < 0x7F) / max(1, b - a)
        dead.append((a, b, text))
        a = b
    total = sum(b - a for a, b, _ in dead)
    code = sum(b - a for a, b, t in dead if t < 0.5)
    print("program up to 0x%04X: %d bytes not reached, of which %d look like code (not text)" % (end - 1, total, code))
    for a, b, t in dead:
        if b - a >= 8:
            print("%05X-%05X  %5d B  %s" % (a, b - 1, b - a, "text/table" if t >= 0.5 else "code?"))


def cmd_strings(path):
    for at, s in strings(load_hex(path)):
        print("%05X  %s" % (at, s))


def cmd_diff(pa, pb):
    ma, mb = load_hex(pa), load_hex(pb)
    ia, ca, _ = analyse(ma)
    ib, cb, _ = analyse(mb)
    fa = {t: body(ia, t) for t in ca}
    fb = {t: body(ib, t) for t in cb}
    by_fp_b = defaultdict(list)
    for t, fn in fb.items(): by_fp_b[fingerprint(fn)].append(t)
    by_fp_a = defaultdict(list)
    for t, fn in fa.items(): by_fp_a[fingerprint(fn)].append(t)
    same, only_a, only_b = [], [], []
    for t in sorted(fa):
        fp = fingerprint(fa[t])
        if fp in by_fp_b: same.append((t, by_fp_b[fp][0], len(fa[t])))
        else: only_a.append(t)
    for t in sorted(fb):
        if fingerprint(fb[t]) not in by_fp_a: only_b.append(t)
    # Second pass: a function whose fingerprint changed (a literal, a few
    # instructions) is matched by its nearest neighbour, mnemonics only.
    import difflib
    changed, left_a, used_b = [], [], set()
    skel = lambda fn: [m for _, m, _, _ in fn]
    for t in only_a:
        best, score = None, 0.0
        for u in only_b:
            if u in used_b: continue
            r = difflib.SequenceMatcher(None, skel(fa[t]), skel(fb[u]), autojunk=False).ratio()
            if r > score: best, score = u, r
        if best is not None and score >= 0.75:
            used_b.add(best)
            changed.append((t, best, score, len(fa[t]), len(fb[best])))
        else:
            left_a.append(t)
    left_b = [u for u in only_b if u not in used_b]
    print("A %s: %d functions; B %s: %d functions" % (pa, len(fa), pb, len(fb)))
    print("%d identical by fingerprint, %d changed (nearest match >= 0.75), %d only in A, %d only in B\n"
          % (len(same), len(changed), len(left_a), len(left_b)))
    print("== identical (A -> B, instructions)")
    for a, b, n in same: print("%05X -> %05X  %4d" % (a, b, n))
    print("\n== changed (A -> B, similarity, instructions A/B, what differs)")
    for a, b, r, na, nb in changed:
        sm = difflib.SequenceMatcher(None, [f for *_, f in fa[a]], [f for *_, f in fb[b]], autojunk=False)
        ops = ["%s A[%d:%d]=%s B[%d:%d]=%s" % (tag, i1, i2, " ".join(fa[a][k][1] + " " + fa[a][k][2] for k in range(i1, min(i2, i1 + 4))),
                                              j1, j2, " ".join(fb[b][k][1] + " " + fb[b][k][2] for k in range(j1, min(j2, j1 + 4))))
               for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
        print("%05X -> %05X  %.2f  %4d/%-4d  %s" % (a, b, r, na, nb, "; ".join(ops[:4])))
    only_a, only_b = left_a, left_b
    for name, lst, fns, insns, calls in (("A", only_a, fa, ia, ca), ("B", only_b, fb, ib, cb)):
        print("\n== only in %s (address, bytes, callers, first instructions)" % name)
        for t in lst:
            fn = fns[t]
            print("%05X  %4d B  %3d callers  %s" % (t, sum(insns[p][0] for p, *_ in fn), len(calls[t]),
                                                    " ".join(m + (" " + o if m in ("movlw", "call", "rcall") else "") for _, m, o, _ in fn[:6])))


if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "dis": cmd_dis(args[0], *(int(x, 16) for x in args[1:3]))
    elif cmd == "funcs": cmd_funcs(args[0])
    elif cmd == "strings": cmd_strings(args[0])
    elif cmd == "dead": cmd_dead(args[0])
    elif cmd == "diff": cmd_diff(args[0], args[1])
    elif cmd == "refs": cmd_refs(args[0], args[1])
    else: sys.exit(__doc__)
