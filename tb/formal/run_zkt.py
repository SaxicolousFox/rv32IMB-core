#!/usr/bin/env python3
"""
MODS_A2 A30 -- the `Zkt` data-independent-latency proof.

THE CLAIM, scoped from the ratified specification (MODS_A2 3.5, which read it
rather than recalling it): every instruction on `Zkt`'s list that THIS CORE
IMPLEMENTS has an EX occupancy that depends only on its opcode.

It is proved in two halves, and they are different KINDS of proof:

  CLAIM A -- everything except mul/mulh/mulhsu/mulhu.
      `a_zkt_only_muldiv_stalls` in rvntt_core.sv: ex_stall is the only thing
      that can extend an instruction's stay in EX, and it is asserted only for
      the multi-cycle unit.  riscv-formal proves it at depth 14 on all 77
      checks, because an assert in the design is an obligation on all of them.
      Nothing to do here.

  CLAIM B -- the multiplier.
      THE SEQUENCER'S STATE IS NOT A FUNCTION OF THE OPERANDS.  This script
      proves that STRUCTURALLY, by computing the transitive fan-in cone of the
      cells that drive `done` and checking that the operand ports are not in it.

WHY STRUCTURALLY AND NOT BY BMC, which is the interesting part.  The obvious
formulation -- "two executions of the same opcode with different operands take
the same number of cycles" -- asks a solver to relate two copies of a circuit,
and MODS_A A15 already paid fifteen minutes to learn that lesson on the divider.
MODS_A2 A30 says it outright: state the invariant one circuit maintains rather
than the conclusion two of them reach.

A cone-of-influence check is better than a bounded proof here in three ways.
It is EXACT rather than bounded to a depth -- there is no unrolling and no
horizon past which a data-dependent path could hide.  It is total over
operands rather than over the reachable states a BMC happens to explore.  And
it fails LOUDLY and specifically: the failure names the operand bit and the
path, rather than producing a counterexample trace to be read.

THE VACUITY CHECK IS THE POINT, and A30 makes it the done-when: this script is
run against a multiplier with an injected data-dependent early-out and MUST
report a violation.  A Zkt proof that has never been observed to fail is a
security claim resting on nothing, and this project has twice shipped a checker
that reported success over a broken design.
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The operand ports.  `op` is the opcode and IS allowed in the cone -- that is
# the whole point: occupancy may depend on which instruction it is, and on
# nothing else.
OPERANDS = ["a", "b"]

# What this core implements from Zkt's list, and what it does not.  MODS_A2 3.5
# read the list from riscv-crypto's zkt document; these are transcribed from it
# and reported by the write-up so the NOT-implemented half is stated rather than
# passed over.
IMPLEMENTED = {
    "RV32I arithmetic/logical/shift":
        ["add", "addi", "sub", "and", "andi", "or", "ori", "xor", "xori",
         "sll", "slli", "srl", "srli", "sra", "srai",
         "slt", "slti", "sltu", "sltiu", "lui", "auipc"],
    "M (multiply only -- Zkt excludes div/rem)":
        ["mul", "mulh", "mulhsu", "mulhu"],
    "Zbkb, as implemented by A21":
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
        # The package first.  MUL_CYCLES defaults to rv32i_pkg's constant, and
        # without the package Yosys reports "Condition for generate if is not
        # constant" -- a parameter it cannot evaluate, not a design problem.
        f.write("read_verilog -sv -formal %s\n"
                % os.path.join(ROOT, "rtl/core/rv32i_pkg.sv"))
        f.write("read_verilog -sv -formal %s\n" % src)
        f.write("hierarchy -check -top rvntt_muldiv\n")
        f.write("proc\n")
        f.write("flatten\n")
        f.write("opt_clean\n")
        # %ci* is the transitive fan-in ("cone in") of the selection.
        # `%ci*` is Yosys's transitive fan-in operator.  It is written with a
        # single percent here: the doubled form was a Python %-format escape
        # that survived into the Tcl-ish script and Yosys rejected it as an
        # unknown operator -- a reminder that this file is generated text and
        # its escaping is not checked by anything but Yosys.
        f.write("select -list w:done %ci*\n")
    # NOT -q.  `select -list` writes to the log, and -q suppresses it -- the
    # first run of this script came back with an empty cone for that reason
    # alone.  The vacuity guard below is what caught it, which is the argument
    # for having one.
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

    # The selection prints one object per line.  An operand PORT in the cone is
    # a data-dependent path into the sequencer.
    # `select -list` prints one object per line as `module/name`, NOT as the
    # `\name` form the RTLIL identifiers use.  Parsed from the artefact rather
    # than assumed: assuming the other form is what produced an empty set.
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

    # ...and the check must not be vacuous.  If `op` is ALSO absent, the
    # selection found nothing at all and the absence above means nothing.
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
