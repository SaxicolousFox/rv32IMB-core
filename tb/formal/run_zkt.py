#!/usr/bin/env python3
"""
The `Zkt` data-independent-latency proof.

The claim: every instruction on Zkt's list that this core implements has an
EX occupancy that depends only on its opcode.  Two halves:

  CLAIM A -- everything except mul/mulh/mulhsu/mulhu.
      `a_zkt_only_muldiv_stalls` in rvntt_core.sv: ex_stall is the only thing
      that can extend an instruction's stay in EX, and it is asserted only for
      the multi-cycle unit.  riscv-formal proves it on all 77 checks.

  CLAIM B -- the multiplier.
      The sequencer's state is not a function of the operands, proved
      structurally: the transitive fan-in cone of the cells driving `done`
      must not contain the operand ports.  Exact rather than depth-bounded,
      total over operands, and it fails by naming the operand.

The vacuity check is the point: run against a multiplier with an injected
data-dependent early-out, this script must report a violation.
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The operand ports.  `op` is the opcode and IS allowed in the cone: occupancy
# may depend on which instruction it is, and on nothing else.
OPERANDS = ["a", "b"]

# What this core implements from Zkt's list, and what it does not; the
# not-implemented half is reported rather than passed over.
IMPLEMENTED = {
    "RV32I arithmetic/logical/shift":
        ["add", "addi", "sub", "and", "andi", "or", "ori", "xor", "xori",
         "sll", "slli", "srl", "srli", "sra", "srai",
         "slt", "slti", "sltu", "sltiu", "lui", "auipc"],
    "M (multiply only -- Zkt excludes div/rem)":
        ["mul", "mulh", "mulhsu", "mulhu"],
    "Zbkb":
        ["ror", "rol", "rori", "andn", "orn", "xnor",
         "pack", "packh", "brev8", "rev8", "zip", "unzip"],
}
NOT_IMPLEMENTED = {
    "Zbc":  ["clmul", "clmulh"],
    "Zbkx": ["xperm4", "xperm8"],
    "Zkn / Zks (the RVK crypto instructions)":
        ["aes32*", "sha256*", "sha512*", "sm3p*", "sm4*"],
    "C (compressed)": ["c.* forms of the listed instructions"],
}

SCRIPT = """
read_verilog -sv -formal {src}
hierarchy -check -top rvntt_muldiv
proc
opt_clean
select -write {out} -list %ci*:+[done] rvntt_muldiv/w:done
"""


def cone(src, tmp):
    """Every cell and wire in the transitive fan-in of `done`."""
    ysfile = os.path.join(tmp, "zkt.ys")
    outfile = os.path.join(tmp, "cone.txt")
    with open(ysfile, "w") as f:
        # The package first: MUL_CYCLES defaults to rv32i_pkg's constant.
        f.write("read_verilog -sv -formal %s\n"
                % os.path.join(ROOT, "rtl/core/rv32i_pkg.sv"))
        f.write("read_verilog -sv -formal %s\n" % src)
        f.write("hierarchy -check -top rvntt_muldiv\n")
        f.write("proc\n")
        f.write("flatten\n")
        f.write("opt_clean\n")
        # `%ci*` is Yosys's transitive fan-in operator, written with a single
        # percent (this is generated text; a doubled form reached Yosys once).
        f.write("select -list w:done %ci*\n")
    # Not -q: `select -list` writes to the log, and -q suppresses it, which
    # gives an empty cone.
    r = subprocess.run(["yosys", "-s", ysfile],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        print(out[-3000:])
        raise SystemExit("ZKT_FAIL: yosys could not read the design")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtl", default=os.path.join(ROOT, "rtl/core/rvntt_muldiv.sv"))
    ap.add_argument("--list", action="store_true",
                    help="print the scoped instruction list and stop")
    a = ap.parse_args()

    n_impl = sum(len(v) for v in IMPLEMENTED.values())
    print("Zkt, scoped to this core.  IMPLEMENTED and therefore claimed (%d):" % n_impl)
    for g, xs in IMPLEMENTED.items():
        print("  %-42s %s" % (g, " ".join(xs)))
    print("NOT implemented -- reported as such, NOT as passing:")
    for g, xs in NOT_IMPLEMENTED.items():
        print("  %-42s %s" % (g, " ".join(xs)))
    if a.list:
        return 0

    import tempfile
    tmp = tempfile.mkdtemp(prefix="zkt_")
    txt = cone(a.rtl, tmp)

    # `select -list` prints one object per line as `module/name`.  An operand
    # port in the cone is a data-dependent path into the sequencer.
    objs = set()
    for l in txt.splitlines():
        l = l.strip()
        if l.startswith("rvntt_muldiv/"):
            objs.add(l[len("rvntt_muldiv/"):])
    bad = []
    for w in OPERANDS:
        for o in objs:
            if re.fullmatch(r"%s(\[\d+\])?" % re.escape(w), o):
                bad.append(o)

    print("\nclaim B -- the multiplier's sequencer, by cone of influence")
    print("  objects in the transitive fan-in of `done`: %d" % len(objs))
    print("  operand bits found in that cone            : %d" % len(bad))
    if bad:
        print("\nZKT_FAIL: `done` depends on the OPERANDS, so EX occupancy is "
              "data-dependent and this core does not implement Zkt.")
        for o in sorted(bad)[:8]:
            print("    " + o)
        print("  A30's stop rule: a partial Zkt is not a thing.  Report the "
              "instruction and the reason; do not claim the extension.")
        return 1

    # ...and the check must not be vacuous: `op` must be present.
    op_present = any(re.fullmatch(r"op(\[\d+\])?", o) for o in objs)
    print("  opcode bits found in that cone             : %s"
          % ("yes" if op_present else "NO"))
    if not op_present:
        print("\nZKT_FAIL: the opcode is not in the cone either, so the cone is "
              "empty or misselected and the operand check proves nothing.  An "
              "absence is trivially satisfied by looking at nothing.")
        return 1

    print("\nZKT_OK: `done` is reached by the opcode and by no operand bit, so "
          "EX occupancy is a function of the opcode alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
