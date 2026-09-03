#!/usr/bin/env python3
"""
Drive riscv-formal over rvntt_core (plan A11, milestone M6).

riscv-formal bounded-model-checks the core against a formal model of the ISA,
attached to the RVFI port rvntt_rvfi.sv exposes.  This script generates the
check set with riscv-formal's own genchecks.py and runs each one under sby.

WHY THE CONFIGURATION LIVES HERE AND NOT IN THE CHECKOUT.  genchecks.py derives
both its base directory and the core's name from the working directory, so it
must be run from `<riscv-formal>/cores/<name>/`.  toolchain/riscv-formal is a
third-party checkout and is gitignored, so anything left there is a build
artifact.  The configuration is therefore generated into that directory from the
template below, exactly as tb/riscof/run_riscof.py generates config.ini -- one
committed source, and --rtl-dir can point the file list at a mirrored tree
without editing anything.

DEPTH, AND WHY IT IS 14.  BMC depth is the number of cycles the solver unrolls,
so it has to be counted from when this core produces its first retirement, not
from zero.  riscv-formal's testbench constrains `reset` to step 0 only, and this
pipeline is five stages deep: the first instruction is fetched at cycle 1,
decodes at 2, executes at 3, and retires at 5.  A check at cycle N therefore sees
at most N-4 retired instructions.

The deepest thing a check has to be able to reach is a register dependency at
distance 3 -- the boundary between the forwarding network, which covers
distances 1 and 2, and the register file's write-through, which covers 3 -- with
a load-use stall and a two-cycle control-flow bubble also in the window.  That is
four instructions plus up to three bubbles, so the checked instruction can be as
late as the 8th cycle after the first retirement.  14 leaves margin on top of
that and still solves in seconds.

It is deliberately not larger.  rtl/core/CLAUDE.md records that BMC time here
blows up superlinearly with depth -- formal_regfile proves at depth 8 in about
three seconds and was still grinding at step 13 of depth 20 after eight minutes
-- so depth is picked from the deepest property, never from a default.

WHAT IS NOT CHECKED, stated rather than left to be discovered:

  * `dmem` and the `bus_*` family.  They verify that a load returns what an
    earlier store wrote, which needs a memory model in the wrapper; this
    wrapper leaves dmem_rdata unconstrained on purpose (see its header).
    Memory consistency is covered by A5's cosimulation and riscv-tests against
    a real RAM instead.
  * `ill`, `csrw` and `csr_ill`.  riscv-formal has no instruction model for
    Zicsr, ECALL, EBREAK, MRET or FENCE, and its `ill` check asserts that
    anything outside the model set traps -- which would demand that this core
    trap on `csrr` and on `fence`.  The CSR file is covered by formal_csr, by
    the rv32mi-p tests and by RISCOF's privilege suite.
  * The Xkntt encodings.  No stage executes them yet and there is no model to
    check them against, so the proof simply says nothing about custom-0 and
    custom-1.  That is M13's work.
  * WHAT THE M INSTRUCTIONS COMPUTE (A14/A15).  rvntt_muldiv's arithmetic is
    abstracted to a free value here -- see RVNTT_ABSTRACT_MULDIV in the
    configuration below for why, and rvntt_muldiv.sv's header for exactly what
    survives.  Its SEQUENCER is not abstracted, so everything these checks
    actually depend on -- when the pipeline stalls, when it bubbles, when it
    retires -- is the real design.  The arithmetic is covered by
    tb/formal/rvntt_muldiv.sby, by the eight rv32um tests and by lockstep
    cosimulation.
"""
import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RF = os.path.join(ROOT, "toolchain/riscv-formal")
CORE_NAME = "rvntt"
# H1: the core directory is settable, because the mutation harness now runs
# mutations IN PARALLEL and each one invokes this script against its own
# mirrored tree.  A single shared cores/rvntt would have every worker writing
# the same checks.cfg and the same generated .sby files on top of each other --
# which does not fail cleanly, it produces a check that describes some other
# worker's design.  Set by --core-name; the default is unchanged.
CORE_DIR = os.path.join(RF, "cores", CORE_NAME)   # rebound by --core-name
WRAPPER = os.path.join(ROOT, "tb/formal/rvntt_rvfi_wrapper.sv")

# Read in this order: rv32i_pkg must precede anything whose port list uses its
# types, because Yosys -- like Verilator -- needs a package declared before use.
RTL = [
    "rtl/core/rv32i_pkg.sv",
    "rtl/core/rvntt_decode.sv",
    "rtl/core/rvntt_alu.sv",
    "rtl/core/rvntt_immgen.sv",
    "rtl/core/rvntt_regfile.sv",
    "rtl/core/rvntt_forward.sv",
    "rtl/core/rvntt_hazard.sv",
    "rtl/core/rvntt_branch.sv",
    "rtl/core/rvntt_csr.sv",
    "rtl/core/rvntt_muldiv.sv",
    "rtl/core/rvntt_bitmanip.sv",
    "rtl/core/rvntt_bpred.sv",
    "rtl/core/rvntt_rvfi.sv",
    "rtl/core/rvntt_core.sv",
]

DEPTH = 14

# LIVENESS IS THE ONE CHECK WHOSE DEPTH IS SET BY A LATENCY RATHER THAN BY A
# DEPENDENCY DISTANCE, and A14's divider moved it.  The property is "if
# instruction N retires at the trigger cycle, N+1 has retired by the check
# cycle", so the window has to hold the worst case gap between two consecutive
# RETIREMENTS -- not, as everywhere else here, the worst case distance between
# two dependent instructions.  With the trigger at 9 that gap is: one cycle
# normally, plus up to two for a control-flow flush, plus one for a load-use
# stall, plus 33 for a DIV occupying EX for 34 cycles.  9 + 37 = 46.
#
# This is MODS_A 3.2's route 1, and it is used ONLY here.  The other 42 checks
# stay at 14: raising the global depth to accommodate one check would weaken
# nothing but would cost every check, and 3.2 is explicit that lowering the
# global depth to make a new check fit is the thing not to do -- the same
# argument in the other direction.
#
# It is affordable only because RVNTT_ABSTRACT_MULDIV is set.  Against the
# concrete multiplier this check took eight seconds to FAIL at depth 14; at 46
# it would not have finished.
LIVENESS_DEPTH = 46

# A21 (MODS_A2).  riscv-formal ships instruction models for all 34 B and Zbkb
# instructions and pre-built bundles for them -- isa_rv32ib.txt (RV32I + Zba +
# Zbb + Zbs) and isa_rv32iZbkb.txt -- but there is NO bundle for the union, and
# genchecks.py takes exactly one bundle name.  So the union is generated here,
# every run, into the checkout's insns/ directory.
#
# WRITING INTO toolchain/riscv-formal IS ALREADY HOW THIS WORKS: the checkout is
# gitignored third-party code and this script already generates cores/rvntt/
# inside it.  Regenerating the bundle on every run is what makes a fresh clone
# work rather than depending on a file someone left behind.
#
# THE NAME HAS TO PARSE AS AN ISA STRING.  genchecks.py matches it against
# ^rv(\d+)([ie])([a-v]*)(_?[SZX]\w+)?$ before opening the file, so `rvntt` is
# rejected outright and `rv32ib_Zbkb` is not.
#
# M's models are STILL NOT INCLUDED, and that is M6's recorded boundary rather
# than an oversight: against RVNTT_ABSTRACT_MULDIV they would be vacuous, and
# against the concrete multiplier they do not converge.  See the module
# docstring.  B adds no such problem -- every instruction it adds is
# combinational and single-cycle, so the depth stays 14.
ISA_BUNDLE = "rv32ib_Zbkb"
ISA_PARTS  = ("rv32ib", "rv32iZbkb")


def write_isa_bundle():
    """Generate insns/isa_rv32ib_Zbkb.txt as the union of its two parts.

    WRITTEN ATOMICALLY.  Parallel invocations all generate byte-identical
    content, but two of them writing the same path at once can still be read
    by a third as a truncated file.  Write-then-rename makes the observable
    file always complete.
    """
    insns_dir = os.path.join(RF, "insns")
    union = []
    for part in ISA_PARTS:
        src = os.path.join(insns_dir, "isa_%s.txt" % part)
        if not os.path.exists(src):
            raise SystemExit(
                "riscv-formal has no insns/isa_%s.txt -- the checkout is older "
                "than the B models this step needs" % part)
        with open(src) as f:
            union += [l.strip() for l in f if l.strip()]
    union = sorted(set(union))
    dst = os.path.join(insns_dir, "isa_%s.txt" % ISA_BUNDLE)
    tmp = "%s.tmp.%d" % (dst, os.getpid())
    with open(tmp, "w") as f:
        f.write("\n".join(union) + "\n")
    os.replace(tmp, dst)
    return len(union)


CHECKS_CFG = """# GENERATED by tb/formal/run_riscv_formal.py -- edit that, not this.

[options]
isa {isa}
mode bmc
# bitwuzla rather than the genchecks default of boolector: boolector is
# unmaintained and bitwuzla is its successor, and it is what the rest of this
# project's formal flow already uses (tb/formal/run_formal.py).
solver bitwuzla

# Columns are [reset_cycles] [trig_cycle] check_cycle; see the module docstring
# for where 14 comes from.  reset_cycles is 1 everywhere -- the number of cycles
# the CHECKER ignores at the start -- because this core retires nothing before
# cycle 5 anyway, so there is no startup garbage to skip and no reason to make
# the checkers blind to anything.
[depth]
insn                {depth}
reg        1        {depth}
pc_fwd     1        {depth}
pc_bwd     1        {depth}
causal     1        {depth}
unique     1  5     {depth}
liveness   1  9     {live_depth}

[defines]
# Yosys defines this itself, but the NERV configuration sets it too as a hotfix
# for older CAD releases -- and it gates the testbench's
# `assume (reset == $initstate)`, without which reset would never be constrained
# and every check would pass vacuously.
`define YOSYS
# This core traps on a misaligned load, store or jump target, so the aligned-mem
# instruction models are the ones that describe it: they report the word-aligned
# base address with a byte mask, and set spec_trap on a misaligned access.  With
# the define absent the models would instead expect misaligned accesses to
# SUCCEED, and every one of them would fail.
`define RISCV_FORMAL_ALIGNED_MEM
# A14's multi-cycle unit, with its arithmetic replaced by a free value and its
# SEQUENCER left exactly as it is.  A combinational 33x33 multiplier unrolled
# fourteen times is the classic worst case for a SAT solver, and it sits in the
# cone of the RVFI outputs whether or not any check reads it: without this the
# 43 checks went from about 40 seconds for the whole set to several hundred
# seconds each.  Stall behaviour, bubble insertion and retirement timing are
# untouched, which is all these checks depend on.  rvntt_muldiv.sv's header
# says what this does and does not weaken; the arithmetic itself is proved by
# tb/formal/rvntt_muldiv.sby, by rv32um and by cosimulation against Spike.
`define RVNTT_ABSTRACT_MULDIV
`define RVNTT_ABSTRACT_BPRED

[verilog-files]
{files}
"""


def cfg_files(rtl_dir):
    """The [verilog-files] list, optionally rebased onto a mirrored RTL tree."""
    base = rtl_dir or ROOT
    return "\n".join([WRAPPER] + [os.path.join(base, p) for p in RTL])


def generate(rtl_dir, verbose):
    """Write the configuration and run genchecks.  Returns the checks dir."""
    os.makedirs(CORE_DIR, exist_ok=True)
    n = write_isa_bundle()
    print("riscv-formal: %s bundle has %d instruction models" % (ISA_BUNDLE, n))
    with open(os.path.join(CORE_DIR, "checks.cfg"), "w") as f:
        f.write(CHECKS_CFG.format(depth=DEPTH, live_depth=LIVENESS_DEPTH,
                                  isa=ISA_BUNDLE, files=cfg_files(rtl_dir)))

    r = subprocess.run([sys.executable, os.path.join(RF, "checks/genchecks.py")],
                       cwd=CORE_DIR, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        print(out)
        raise SystemExit("genchecks.py failed")
    if verbose:
        print(out.rstrip())
    return os.path.join(CORE_DIR, "checks")


def discover(checks_dir):
    return sorted(f[:-4] for f in os.listdir(checks_dir) if f.endswith(".sby"))


def run_check(checks_dir, name):
    """Run one check.  Returns (name, passed, seconds, log tail).

    SBY'S EXIT CODE IS NOT THE VERDICT, and this is the second time this project
    has been bitten by that particular shape (see A10's RISCOF runner).
    genchecks writes `expect pass,fail` into every generated .sby, which tells
    sby that a failing proof is an ACCEPTABLE outcome -- so it prints
    "DONE (FAIL, rc=0)" and exits 0.  A runner that trusts the return code
    reports a green 43/43 over a core with a broken adder; that is exactly what
    the first version of this function did, and only fault injection said so.
    The verdict comes from the status file, and a missing one is a failure
    rather than a pass.
    """
    t0 = time.time()
    r = subprocess.run(["sby", "-f", name + ".sby"], cwd=checks_dir,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    status_path = os.path.join(checks_dir, name, "status")
    try:
        verdict = open(status_path).read().split()[0]
    except (OSError, IndexError):
        verdict = "NO-STATUS"
    if verdict != "PASS":
        out += "\nverdict from %s: %s (sby exit %d)" % (
            os.path.relpath(status_path, ROOT), verdict, r.returncode)
    return name, verdict, time.time() - t0, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", default=None,
                    help="run only these checks (exact names, or regexes)")
    ap.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--core-name", default=CORE_NAME,
                    help="riscv-formal cores/<name> working directory. The "
                         "mutation harness gives each parallel worker its own, "
                         "so they cannot overwrite each other's generated "
                         "checks.")
    ap.add_argument("--rtl-dir", default=None,
                    help="read the core from this mirrored RTL tree instead of "
                         "rtl/ (used to fault-inject this harness)")
    a = ap.parse_args()
    global CORE_DIR
    CORE_DIR = os.path.join(RF, "cores", a.core_name)

    if not os.path.isdir(RF):
        print("RVFORMAL_SKIP: toolchain/riscv-formal is not checked out.\n"
              "  git clone https://github.com/YosysHQ/riscv-formal.git \\\n"
              "      toolchain/riscv-formal")
        return 0

    checks_dir = generate(a.rtl_dir, a.verbose)
    names = discover(checks_dir)
    if a.only:
        pats = [re.compile("^(%s)$" % p) for p in a.only]
        names = [n for n in names if any(p.match(n) for p in pats)]

    if a.list:
        for n in names:
            print(n)
        return 0

    # An empty check set is not a pass.  genchecks prints "Current isa string
    # ... not supported, skipping instruction checks" to stderr and exits 0 when
    # it cannot find the ISA file, which would otherwise leave a green run that
    # proved nothing -- the same trap A10 hit with riscof's exit code.
    if not names:
        print("RVFORMAL_FAIL: no checks were generated or selected")
        return 1

    print("=== riscv-formal: %d check(s), depth %d (liveness %d), "
          "muldiv abstracted, %d job(s) ==="
          % (len(names), DEPTH, LIVENESS_DEPTH, a.jobs))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_check, checks_dir, n) for n in names]
        for fut in concurrent.futures.as_completed(futs):
            name, verdict, dt, out = fut.result()
            results.append((name, verdict, dt, out))
            print("  %-28s %-9s %6.1fs" % (name, verdict, dt), flush=True)

    results.sort()
    bad = [r for r in results if r[1] != "PASS"]
    # A design Yosys could not read, or a solver that gave up, is NOT a caught
    # bug -- it is a broken experiment, and it has to be distinguishable from a
    # genuine counterexample.  tb/mutate/run_mutation.py relies on this exit
    # code to refuse to credit a mutation that merely fails to elaborate, the
    # same rule the mutation harness already applies to Verilator build errors.
    broken = [r for r in results if r[1] not in ("PASS", "FAIL")]
    for name, _v, _dt, out in bad:
        print("\n---- %s ----" % name)
        print("\n".join(out.rstrip().splitlines()[-25:]))
    if a.verbose:
        for name, _v, _dt, out in results:
            print("\n---- %s ----" % name)
            print("\n".join(out.rstrip().splitlines()[-8:]))

    total = sum(r[2] for r in results)
    print("\n%d/%d checks passed (%.0fs of solver time across %d job(s))"
          % (len(results) - len(bad), len(results), total, a.jobs))
    if broken:
        print("RVFORMAL_ERROR: " + ", ".join("%s=%s" % (r[0], r[1])
                                             for r in broken))
        return 2
    if bad:
        print("RVFORMAL_FAIL: " + ", ".join(r[0] for r in bad))
        return 1
    print("RVFORMAL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
