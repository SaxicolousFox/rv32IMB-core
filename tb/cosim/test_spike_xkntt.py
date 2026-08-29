#!/usr/bin/env python3
"""
C1 -- Spike executes the Xkntt extension, and agrees with the Python model.

Five things are checked, in increasing order of what they would cost to get
wrong later:

  1. Tier-1 semantics.  Every instruction, over directed edge cases plus random
     vectors, compared against model/isa/xkntt.py through Spike's --log-commits
     output.  That output is the same artifact the A5 cosim differ consumes, so
     the format is proven here rather than during RTL bring-up.
  2. Reserved-field strictness.  isa-spec.md Deviation 3 makes a nonzero
     reserved field an illegal instruction; a lax Spike and a strict RTL
     decoder would disagree on exactly those words, which is the divergence
     plan A3's 10^6-random-word comparison is designed to catch.
  3. Extension gating.  The same program must trap under --isa=rv32i_zicsr,
     or the extension is not really an extension.
  4. Tier-2 through the MMIO aperture: sw the polynomial in, kntt.cfg /
     kntt.start / kntt.wait, lw the result out, compare to the golden NTT.
  5. Tier-2 error policy: an invalid mode sets ERR and returns 0 rather than
     trapping (isa-spec.md 5.4).
"""
import os, random, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model", "isa"))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xkntt as X                                              # noqa: E402
import ntt_ref as NR                                           # noqa: E402
import spike_asm as SA                                         # noqa: E402

KNTT_BASE = 0x50000000
KNTT_BUF  = (0x000, 0x200)

fails = []
def fail(msg):
    fails.append(msg)


# ---------------------------------------------------------------- Tier 1 ----
TIER1 = ["kmm", "kbfct", "kbfgs", "kbmul0", "kmac", "kbmul1"]

def tier1_vectors(n_random):
    """Edge cases first, then random.  Same vectors for every instruction."""
    edges = [0x00000000, 0x00000001, 0x0000FFFF, 0xFFFFFFFF, 0x00008000,
             0x80000000, 0x7FFF7FFF, 0x0D010D01, 0xF2FFF2FF,
             0x00000D01, 0x0000F2FF, 0x0681FCFF]
    vecs = [(a, b, c) for a in edges for b in edges[:4] for c in edges[:2]]
    rng = random.Random(0xC1)
    vecs += [tuple(rng.getrandbits(32) for _ in range(3)) for _ in range(n_random)]
    return vecs


def tier1_body(vecs):
    """One loop per instruction over a shared vector table."""
    text = []
    for name in TIER1:
        opcode, fmt, f3, f7f2, _ = X.ISA[name]
        if fmt == "R":
            insn = f"        .insn r 0x{opcode:02X}, {f3}, 0x{f7f2:02X}, t0, t1, t2"
        else:
            insn = (f"        .insn r4 0x{opcode:02X}, {f3}, 0x{f7f2:02X}, "
                    f"t0, t1, t2, t3")
        text.append(f"""
        la      s0, vecs
        la      s1, vecs_end
L{name}:
        lw      t1, 0(s0)
        lw      t2, 4(s0)
        lw      t3, 8(s0)
{insn}
        addi    s0, s0, 12
        bltu    s0, s1, L{name}
""")
    flat = [w for v in vecs for w in v]
    return "".join(text), SA.data_words("vecs", flat)


def check_tier1(tmp, n_random=400):
    vecs = tier1_vectors(n_random)
    body, data = tier1_body(vecs)
    elf = SA.build(body, tmp, "tier1", data)
    rc, trace, text = SA.run(elf)
    if rc != 0:
        fail(f"tier1 program exited {rc}: {text[-400:]}")
        return

    # Collect the custom-opcode commits, in order.
    got = []
    for _pc, word, writes in trace:
        if (word & 0x7F) not in (X.OPC_CUSTOM_0, X.OPC_CUSTOM_1):
            continue
        d = X.decode(word)
        if d is None:
            fail(f"Spike executed 0x{word:08X} but the model calls it illegal")
            continue
        got.append((d["mnemonic"], writes))

    expect = len(TIER1) * len(vecs)
    if len(got) != expect:
        fail(f"tier1: Spike committed {len(got)} custom instructions, expected {expect}")
        return

    bad = 0
    for i, (name, writes) in enumerate(got):
        which, vi = TIER1[i // len(vecs)], i % len(vecs)
        if name != which:
            fail(f"tier1: commit {i} disassembles as {name}, expected {which}")
            return
        a, b, c = vecs[vi]
        fn = X.EXEC[name]
        want = fn(a, b, c) if name in ("kbmul0", "kmac") else fn(a, b)
        if not writes:
            fail(f"tier1: {name} committed no register write")
            return
        _rd, val = writes[0]
        if val != want:
            bad += 1
            if bad <= 5:
                fail(f"tier1 {name}(0x{a:08X},0x{b:08X},0x{c:08X}): "
                     f"Spike 0x{val:08X}, model 0x{want:08X}")
    if bad == 0:
        print(f"  Tier 1: {len(got)} executions across {len(TIER1)} instructions "
              f"match model/isa/xkntt.py")


# --------------------------------------------------- reserved-field policy ---
def check_strictness(tmp):
    """
    A nonzero reserved field must trap.  Each case is its own program, because
    the first illegal instruction ends the run.
    """
    cases = [
        ("kmm funct7 != 0",     X.encode("kmm", rd=5, rs1=6, rs2=7) | (1 << 25)),
        ("kbmul0 funct2 != 0",  X.encode("kbmul0", rd=5, rs1=6, rs2=7, rs3=28) | (1 << 25)),
        ("kntt.cfg rd != 0",    X.encode("kntt.cfg", rs1=6, rs2=7) | (5 << 7)),
        ("kntt.start rs2 != 0", X.encode("kntt.start", rd=5, rs1=6) | (7 << 20)),
        ("kntt.wait rs1 != 0",  X.encode("kntt.wait", rd=5) | (6 << 15)),
        ("kntt.stat rs2 != 0",  X.encode("kntt.stat", rd=5) | (7 << 20)),
        ("custom-0 funct3=6",   (0x0B | (6 << 12) | (5 << 7))),
        ("custom-1 funct3=4",   (0x2B | (4 << 12) | (5 << 7))),
    ]
    ok = 0
    for label, word in cases:
        if X.decode(word) is not None:
            fail(f"strictness: the model accepts {label} (0x{word:08X}); "
                 f"the test case itself is wrong")
            continue
        elf = SA.build(f"        .word 0x{word:08x}\n", tmp, "strict")
        rc, _trace, _text = SA.run(elf, log_commits=False)
        if rc != SA.TRAP_EXIT_CODE:
            fail(f"strictness: {label} (0x{word:08X}) exited {rc}, expected the "
                 f"trap handler's {SA.TRAP_EXIT_CODE}; the spec says it is an "
                 f"illegal instruction")
        else:
            ok += 1
    if ok == len(cases):
        print(f"  reserved fields: all {ok} malformed encodings rejected by Spike")


def check_decoder_sweep(tmp, n=20000):
    """
    Plan A3's decoder comparison, applied to Spike: thousands of random words on
    the two custom opcodes, checking that Spike accepts EXACTLY the set the
    Python model accepts.  The handpicked cases above cover the encodings a
    human would think to try; this covers the ones nobody would.

    A skip-and-continue trap handler lets one program probe them all.  The
    random instructions can clobber any register freely -- the exit sequence
    only ever writes t0/t1, and none of these instructions touch memory.
    """
    rng = random.Random(0xA3)
    words = []
    for _ in range(n):
        opcode = X.OPC_CUSTOM_0 if rng.random() < 0.5 else X.OPC_CUSTOM_1
        # Bias funct3 towards the assigned values so most words are near-misses
        # rather than obviously-wrong; the interesting divergences live there.
        f3 = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])
        w = (rng.getrandbits(32) & ~0x7FFF) | (f3 << 12) | \
            (rng.getrandbits(5) << 7) | opcode
        words.append(w & 0xFFFFFFFF)

    body = ("        .globl sweep_start\nsweep_start:\n"
            + "".join(f"        .word 0x{w:08x}\n" for w in words))
    elf = SA.build(body, tmp, "sweep", trap_mode="skip")
    rc, trace, _text = SA.run(elf)
    if rc != 0:
        fail(f"decoder sweep: program exited {rc}")
        return

    start = SA.symbol(elf, "sweep_start")
    if start is None:
        fail("decoder sweep: sweep_start symbol missing from the ELF")
        return

    trapped = set()
    for pc in SA.skipped_traps(elf, trace):
        idx = (pc - start) // 4
        if not (0 <= idx < len(words)):
            fail(f"decoder sweep: trap at 0x{pc:08x}, outside the word block")
            return
        trapped.add(idx)

    mism = []
    for i, w in enumerate(words):
        legal_model = X.decode(w) is not None
        legal_spike = i not in trapped
        if legal_model != legal_spike:
            mism.append((i, w, legal_model, legal_spike))
    if mism:
        for i, w, lm, ls in mism[:5]:
            fail(f"decoder sweep: 0x{w:08X} -- model says "
                 f"{'legal' if lm else 'illegal'}, Spike says "
                 f"{'legal' if ls else 'illegal'}")
        fail(f"decoder sweep: {len(mism)} of {len(words)} words disagree")
    else:
        acc = len(words) - len(trapped)
        print(f"  decoder sweep: {len(words)} random custom-opcode words, "
              f"Spike and the model agree on all of them "
              f"({acc} accepted, {len(trapped)} rejected)")


def check_gating(tmp):
    """kmm must be illegal when xkntt is not in the ISA string."""
    body = "        .insn r 0x0B, 0, 0x00, t0, t1, t2\n"
    elf = SA.build(body, tmp, "gate")
    rc, _t, _text = SA.run(elf, isa=SA.ISA_BASE, log_commits=False)
    if rc != SA.TRAP_EXIT_CODE:
        fail(f"gating: kmm exited {rc} under --isa={SA.ISA_BASE}, expected the "
             f"trap handler's {SA.TRAP_EXIT_CODE}; require_extension(EXT_XKNTT) "
             f"is not doing its job")
    else:
        print("  gating: kmm traps without xkntt in the ISA string")
    rc, _t, text = SA.run(elf, isa=SA.ISA_XKNTT, log_commits=False)
    if rc != 0:
        fail(f"gating: kmm ALSO traps with xkntt enabled (exit {rc}) -- "
             f"the positive control failed, so the negative one proves nothing")


# ---------------------------------------------------------------- Tier 2 ----
def pack_poly(coeffs):
    """256 int16 coefficients -> 128 little-endian words, low half first."""
    return [((coeffs[2*i+1] & 0xFFFF) << 16) | (coeffs[2*i] & 0xFFFF)
            for i in range(128)]


def unpack_poly(words):
    out = []
    for w in words:
        lo, hi = w & 0xFFFF, (w >> 16) & 0xFFFF
        out.append(lo - 0x10000 if lo & 0x8000 else lo)
        out.append(hi - 0x10000 if hi & 0x8000 else hi)
    return out


def tier2_body(mode, src_words, second=None):
    """Fill buffer A (and optionally B), run one operation, read the result."""
    src, dst = (mode >> 2) & 1, (mode >> 3) & 1
    fill = ""
    for label, buf in [("polya", src)] + ([("polyb", 1 - src)] if second else []):
        fill += f"""
        la      s0, {label}
        li      s1, 0x{KNTT_BASE + KNTT_BUF[buf]:08x}
        li      s2, 128
Lfill_{label}:
        lw      t0, 0(s0)
        sw      t0, 0(s1)
        addi    s0, s0, 4
        addi    s1, s1, 4
        addi    s2, s2, -1
        bnez    s2, Lfill_{label}
"""
    body = fill + f"""
        li      t1, 0                                   # descriptor (deferred)
        li      t2, {mode}                              # mode word
        .insn r 0x2B, 0, 0x00, x0, t1, t2               # kntt.cfg
        .insn r 0x2B, 1, 0x00, t0, t2, x0               # kntt.start t0, t2
        .insn r 0x2B, 2, 0x00, t0, x0, x0               # kntt.wait t0

        li      s1, 0x{KNTT_BASE + KNTT_BUF[dst]:08x}
        li      s2, 128
2:      lw      a7, 0(s1)                               # a7 is read back ONLY here
        addi    s1, s1, 4
        addi    s2, s2, -1
        bnez    s2, 2b
"""
    data = SA.data_words("polya", src_words)
    if second:
        data += SA.data_words("polyb", second)
    return body, data


def readback(trace, reg=17):
    return [v for _pc, _w, writes in trace for r, v in writes if r == reg]


def check_tier2(tmp):
    rng = random.Random(0xC12)
    poly = [rng.randrange(-1664, 1665) for _ in range(256)]
    other = [rng.randrange(-1664, 1665) for _ in range(256)]

    cases = [
        ("forward NTT", 0 | (0 << 2) | (1 << 3), pack_poly(poly), None,
         NR.ntt(poly)),
        ("inverse NTT", 1 | (0 << 2) | (1 << 3), pack_poly(poly), None,
         NR.invntt(poly)),
    ]
    for label, mode, words, second, expect in cases:
        b, d = tier2_body(mode, words, second)
        elf = SA.build(b, tmp, "tier2", d)
        rc, trace, text = SA.run(elf)
        if rc != 0:
            fail(f"tier2 {label}: exited {rc}: {text[-400:]}")
            continue
        got = unpack_poly(readback(trace))
        if len(got) != 256:
            fail(f"tier2 {label}: read back {len(got)} coefficients, expected 256")
            continue
        if got != expect:
            diff = [i for i in range(256) if got[i] != expect[i]]
            fail(f"tier2 {label}: {len(diff)} coefficients differ, first at "
                 f"{diff[0]}: got {got[diff[0]]}, want {expect[diff[0]]}")
        else:
            print(f"  Tier 2 {label}: 256 coefficients match model/ntt_ref.py "
                  f"through the MMIO aperture")

    # basemul: both buffers are operands (PROVISIONAL, see riscv/xkntt.h)
    expect = [0] * 256
    for i in range(64):
        for h in range(2):
            o = 4 * i + 2 * h
            z = NR.ZETAS[64 + i] * (-1 if h else 1)
            c0 = NR.fqmul(NR.fqmul(poly[o+1], other[o+1]), z)
            c0 = NR._i16(c0 + NR.fqmul(poly[o], other[o]))
            c1 = NR._i16(NR.fqmul(poly[o], other[o+1]) +
                         NR.fqmul(poly[o+1], other[o]))
            expect[o], expect[o+1] = c0, c1
    mode = 2 | (0 << 2) | (1 << 3)
    b, d = tier2_body(mode, pack_poly(poly), pack_poly(other))
    elf = SA.build(b, tmp, "bm", d)
    rc, trace, text = SA.run(elf)
    if rc != 0:
        fail(f"tier2 basemul: exited {rc}: {text[-400:]}")
    else:
        got = unpack_poly(readback(trace))
        if got != expect:
            diff = [i for i in range(256) if got[i] != expect[i]]
            fail(f"tier2 basemul: {len(diff)} coefficients differ, first at "
                 f"{diff[0]}: got {got[diff[0]]}, want {expect[diff[0]]}")
        else:
            print("  Tier 2 basemul: 256 coefficients match the reference basemul")


def check_tier2_errors(tmp):
    """
    isa-spec.md 5.4: an invalid mode does not trap.  kntt.start returns 0 and
    ERR appears in the status word.
    """
    body = """
        li      t2, 0x10                        # reserved bit 4 set
        .insn r 0x2B, 1, 0x00, t0, t2, x0       # kntt.start t0, t2  -> must be 0
        .insn r 0x2B, 3, 0x00, a7, x0, x0       # kntt.stat a7       -> ERR set
"""
    elf = SA.build(body, tmp, "err")
    rc, trace, text = SA.run(elf)
    if rc != 0:
        fail(f"tier2 error path: exited {rc} -- an invalid mode must not trap")
        return
    tok = [v for _p, w, wr in trace if (w & 0x7F) == X.OPC_CUSTOM_1
           and X.decode(w) and X.decode(w)["mnemonic"] == "kntt.start"
           for _r, v in wr]
    st = readback(trace)
    if not tok or tok[0] != 0:
        fail(f"tier2 error path: kntt.start returned {tok}, expected 0")
    elif not st or not (st[0] & 0x4):
        fail(f"tier2 error path: status 0x{st[0] if st else 0:08X} has ERR clear")
    else:
        print("  Tier 2 error policy: invalid mode returns 0 and sets ERR, no trap")


def main():
    import shutil
    for tool in ("spike", "riscv-none-elf-gcc"):
        if shutil.which(tool) is None:
            print(f"SKIP: {tool} not on PATH")
            return 0
    tmp = tempfile.mkdtemp(prefix="spike_xkntt_")
    n = int(os.environ.get("XKNTT_SPIKE_RANDOM", "400"))
    check_tier1(tmp, n)
    check_strictness(tmp)
    check_decoder_sweep(tmp)
    check_gating(tmp)
    check_tier2(tmp)
    check_tier2_errors(tmp)
    if fails:
        print("\nSPIKE_XKNTT_FAIL:")
        for f in fails:
            print("  " + f)
        return 1
    print("spike xkntt OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
