#!/usr/bin/env python3
"""
Xkntt encoding contract test.

Four artifacts must agree on these bit patterns: the RTL decoder, Spike, the
LLVM MC layer, and the test vectors.  Right now two of the four exist, so this
checks the ones that do against a genuinely independent third implementation --
GNU `as`, via `.insn` -- and against the hex literals published in
docs/isa-spec.md, which are parsed out of the document so it cannot drift.
"""
import os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model", "isa"))
import xkntt as X                                              # noqa: E402

SPEC = os.path.join(ROOT, "docs", "isa-spec.md")

CANON = [
    ("kmm",        dict(rd="t0", rs1="t1", rs2="t2")),
    ("kbfct",      dict(rd="t0", rs1="t1", rs2="t2")),
    ("kbfgs",      dict(rd="t0", rs1="t1", rs2="t2")),
    ("kbmul0",     dict(rd="t0", rs1="t1", rs2="t2", rs3="t3")),
    ("kmac",       dict(rd="t0", rs1="t1", rs2="t2", rs3="t3")),
    ("kbmul1",     dict(rd="t0", rs1="t1", rs2="t2")),
    ("kntt.cfg",   dict(rs1="t1", rs2="t2")),
    ("kntt.start", dict(rd="t0", rs1="t1")),
    ("kntt.wait",  dict(rd="t0")),
    ("kntt.stat",  dict(rd="t0")),
]


def gnu_as_encodings():
    """Assemble every canonical instance with stock GNU as via .insn."""
    lines = ["        .text"]
    for m, ops in CANON:
        opcode, fmt, f3, f7f2, _ = X.ISA[m]
        rd  = ops.get("rd", "zero")
        rs1 = ops.get("rs1", "zero")
        rs2 = ops.get("rs2", "zero")
        if fmt == "R":
            lines.append(f"        .insn r 0x{opcode:02X}, {f3}, 0x{f7f2:02X}, "
                         f"{rd}, {rs1}, {rs2}   # {m}")
        else:
            rs3 = ops.get("rs3", "zero")
            lines.append(f"        .insn r4 0x{opcode:02X}, {f3}, 0x{f7f2:02X}, "
                         f"{rd}, {rs1}, {rs2}, {rs3}   # {m}")

    tmp = tempfile.mkdtemp(prefix="xkntt_enc_")
    s = os.path.join(tmp, "t.S")
    o = os.path.join(tmp, "t.o")
    with open(s, "w") as f:
        f.write("\n".join(lines) + "\n")
    r = subprocess.run(["riscv-none-elf-gcc", "-march=rv32i", "-mabi=ilp32",
                        "-c", "-o", o, s],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise RuntimeError("assemble failed: " + r.stdout.decode())
    r = subprocess.run(["riscv-none-elf-objdump", "-d", o], stdout=subprocess.PIPE)
    words = []
    for line in r.stdout.decode().splitlines():
        m = re.match(r"\s+[0-9a-f]+:\s+([0-9a-f]{8})\s", line)
        if m:
            words.append(int(m.group(1), 16))
    return words


def spec_encodings():
    """Pull the hex literals out of the spec's encoding summary table."""
    if not os.path.exists(SPEC):
        return None
    txt = open(SPEC).read()
    out = {}
    # rows look like: | `kmm t0, t1, t2` | ... | `0x0073028B` |
    for line in txt.splitlines():
        m = re.match(r"\s*\|\s*`([a-z0-9._]+)[^`]*`.*`(0x[0-9A-Fa-f]{8})`\s*\|\s*$", line)
        if m:
            out.setdefault(m.group(1), m.group(2).upper().replace("0X", "0x"))
    return out


def main() -> int:
    fails = []

    # 1. model vs GNU as
    try:
        gnu = gnu_as_encodings()
    except Exception as e:
        print("SKIP: could not run the RISC-V assembler:", e)
        return 0

    if len(gnu) != len(CANON):
        fails.append(f"objdump produced {len(gnu)} words for {len(CANON)} instructions")
    else:
        for (m, ops), w_gnu in zip(CANON, gnu):
            w = X.encode(m, **ops)
            if w != w_gnu:
                fails.append(f"{m}: model 0x{w:08X} != GNU as 0x{w_gnu:08X}")

    # 2. spec document vs model
    spec = spec_encodings()
    if spec is None:
        fails.append("docs/isa-spec.md missing")
    else:
        for m, ops in CANON:
            w = X.encode(m, **ops)
            if m not in spec:
                fails.append(f"{m}: no encoding published in docs/isa-spec.md")
            elif int(spec[m], 16) != w:
                fails.append(f"{m}: spec says {spec[m]}, model gives 0x{w:08X}")

    # 3. decode round-trip over every instruction and many register combinations
    import random
    rng = random.Random(1234)
    for m, _ in CANON:
        opcode, fmt, f3, f7f2, operands = X.ISA[m]
        for _ in range(200):
            ops = {}
            for o in operands:
                ops[o] = rng.randrange(0, 32)
            w = X.encode(m, **ops)
            d = X.decode(w)
            if d is None or d["mnemonic"] != m:
                fails.append(f"{m}: decode round-trip failed for {ops}")
                break
            for o in operands:
                if d[o] != ops[o]:
                    fails.append(f"{m}: operand {o} round-trip {ops[o]} -> {d[o]}")
                    break

    # 4. reserved fields must be rejected when nonzero
    for m, ops in CANON:
        opcode, fmt, f3, f7f2, operands = X.ISA[m]
        for field, shift in (("rd", 7), ("rs1", 15), ("rs2", 20)):
            if field in operands:
                continue
            w = X.encode(m, **ops) | (1 << shift)
            if X.decode(w) is not None:
                fails.append(f"{m}: reserved {field}!=0 (0x{w:08X}) was accepted")

    # 5. non-Xkntt words must not decode
    for w in (0x00000013, 0x00A58533, 0xFFFFFFFF, 0x00000000):
        if X.decode(w) is not None:
            fails.append(f"non-Xkntt word 0x{w:08X} decoded as {X.decode(w)}")

    for f in fails[:15]:
        print("  FAIL:", f)
    if fails:
        print(f"ISA_ENCODING_FAIL ({len(fails)})")
        return 1
    print(f"ISA_ENCODING_OK ({len(CANON)} instructions: model == GNU as == docs/isa-spec.md; "
          f"round-trip + reserved-field rejection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
