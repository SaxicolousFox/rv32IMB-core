#!/usr/bin/env python3
"""
C0 -- the `.insn` bridge.

Plan step C0's acceptance: "every instruction in the spec can be emitted via
.insn and the disassembled hex matches your hand-computed encoding exactly."

Phase 1's test_isa_encoding.py already proved that for hand-written assembly.
What this adds is the path Track A and C6 will actually use: ordinary C calling
sw/include/xkntt.h, compiled at -O0 and -O2, disassembled, and decoded field by
field with the Python model.  Optimisation is included on purpose -- an inline
asm template with a mis-declared constraint tends to survive -O0 and break at
-O2, and C6 will only ever be built optimised.

It also checks the XKNTT_EMULATE path against the same golden model, because
that is what makes a full 10000-vector host KAT run affordable.
"""
import os, random, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model", "isa"))
import xkntt as X                                              # noqa: E402

INC = os.path.join(ROOT, "sw", "include")
DRV = os.path.join(ROOT, "sw", "tests", "insn_bridge.c")
EMU = os.path.join(ROOT, "sw", "tests", "emul_check.c")

GCC     = "riscv-none-elf-gcc"
OBJDUMP = "riscv-none-elf-objdump"

# One instance of every instruction in the spec, in source order.
EXPECT = ["kmm", "kbfct", "kbfgs", "kbmul0", "kmac", "kbmul1",
          "kntt.cfg", "kntt.start", "kntt.wait", "kntt.stat"]

fails = []


def build_and_dump(opt, tmp, extra=()):
    obj = os.path.join(tmp, f"drv{opt}.o")
    cmd = [GCC, "-march=rv32i", "-mabi=ilp32", f"-{opt}", "-I", INC,
           *extra, "-c", "-o", obj, DRV]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise RuntimeError(f"compile at -{opt} failed:\n" + r.stdout.decode())
    r = subprocess.run([OBJDUMP, "-d", obj], stdout=subprocess.PIPE)
    words = []
    for line in r.stdout.decode().splitlines():
        m = re.match(r"\s+[0-9a-f]+:\s+([0-9a-f]{8})\s", line)
        if m:
            words.append(int(m.group(1), 16))
    return words


def check_encodings():
    """Every custom-opcode word emitted from C must decode, and re-encode."""
    tmp = tempfile.mkdtemp(prefix="xkntt_bridge_")
    for opt in ("O0", "O2"):
        words = build_and_dump(opt, tmp)
        custom = [w for w in words if (w & 0x7F) in (X.OPC_CUSTOM_0, X.OPC_CUSTOM_1)]
        got = []
        for w in custom:
            d = X.decode(w)
            if d is None:
                fails.append(f"-{opt}: word 0x{w:08X} on a custom opcode does "
                             f"not decode as any Xkntt instruction")
                continue
            m = d.pop("mnemonic")
            got.append(m)
            # Round-trip: the decoded fields must rebuild the exact word.
            if X.encode(m, **d) != w:
                fails.append(f"-{opt}: {m} 0x{w:08X} re-encodes to "
                             f"0x{X.encode(m, **d):08X}")
        if got != EXPECT:
            fails.append(f"-{opt}: emitted {got}, expected {EXPECT}")
        else:
            print(f"  -{opt}: {len(got)}/{len(EXPECT)} instructions emitted, "
                  f"decoded and round-tripped")


def check_emulation(n=2000):
    """XKNTT_EMULATE must match model/isa/xkntt.py bit-for-bit."""
    tmp = tempfile.mkdtemp(prefix="xkntt_emul_")
    exe = os.path.join(tmp, "emul")
    r = subprocess.run(["cc", "-O2", "-DXKNTT_EMULATE=1", "-I", INC,
                        "-o", exe, EMU],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        fails.append("host emulation build failed:\n" + r.stdout.decode())
        return

    names = ["kmm", "kbfct", "kbfgs", "kbmul0", "kmac", "kbmul1"]
    rng = random.Random(0xC0)
    vecs, lines = [], []
    for _ in range(n):
        op = rng.randrange(len(names))
        # Full 32-bit random inputs: the instructions are defined over whatever
        # the register happens to hold, including garbage in ignored halves.
        a, b, c = (rng.getrandbits(32) for _ in range(3))
        vecs.append((op, a, b, c))
        lines.append(f"{op} {a:x} {b:x} {c:x}")
    r = subprocess.run([exe], input="\n".join(lines).encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode().split()
    if len(out) != n:
        fails.append(f"emulation printed {len(out)} results, expected {n}: "
                     f"{r.stdout.decode()[:200]}")
        return
    bad = 0
    for (op, a, b, c), got in zip(vecs, out):
        name = names[op]
        fn = X.EXEC[name]
        want = fn(a, b, c) if name in ("kbmul0", "kmac") else fn(a, b)
        if int(got, 16) != want:
            bad += 1
            if bad <= 3:
                fails.append(f"emulated {name}(0x{a:08X},0x{b:08X},0x{c:08X}) = "
                             f"0x{int(got,16):08X}, model says 0x{want:08X}")
    if bad == 0:
        print(f"  XKNTT_EMULATE: {n} random vectors match the golden model")


def main():
    for tool in (GCC, OBJDUMP):
        if subprocess.run(["which", tool], stdout=subprocess.DEVNULL).returncode:
            print(f"SKIP: {tool} not on PATH")
            return 0
    check_encodings()
    check_emulation()
    if fails:
        print("\nINSN_BRIDGE_FAIL:")
        for f in fails:
            print("  " + f)
        return 1
    print("insn bridge OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
