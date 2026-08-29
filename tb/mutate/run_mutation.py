#!/usr/bin/env python3
"""
Mutation testing for the Track A pipeline.

"Fault-inject every checking mechanism" is the working norm in this repo, and
through A5 it was done with a throwaway script per step.  That made each result
unreproducible the moment the session ended, which is the wrong property for the
habit that has caught more real bugs here than anything else.  This is the same
technique, kept.

WHAT MAKES A RESULT MEANINGFUL.  Every mutation declares WHICH tests must catch
it, and both directions are failures:

  * ESCAPED   -- no declared catcher failed.  The bug is invisible to the suite.
  * NOT-CAUGHT-BY -- a test that was declared to catch it did not.  Either the
    test is weaker than believed or the manifest is stale; both are worth
    knowing, and a manifest that only had to be caught by *something* would
    slowly decay into every mutation being caught by the one broadest test.

A mutation that fails to BUILD proves nothing -- it is reported as an error, not
a catch, because deleting an expression's only use makes the tool the detector
rather than the test.  Two A4 mutations were rejected on exactly those grounds.

The baseline run comes first: every test named anywhere in the manifest is run
against unmutated RTL and must pass.  Without it a stuck-at-fail test would
appear to catch everything.

Usage:
    python3 tb/mutate/run_mutation.py                 # every mutation
    python3 tb/mutate/run_mutation.py --step A6       # one step's
    python3 tb/mutate/run_mutation.py --only fwd_priority_swapped
"""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb"))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import commit_diff                  # noqa: E402
import gen_random_prog              # noqa: E402
import test_cosim_a5 as a5          # noqa: E402
import test_cosim_directed as dr    # noqa: E402
import test_core_verilator as t4    # noqa: E402
from rtl_deps import with_deps      # noqa: E402

# ---------------------------------------------------------------- the manifest
CORE  = "rtl/core/rvntt_core.sv"
FWD   = "rtl/core/rvntt_forward.sv"
HAZ   = "rtl/core/rvntt_hazard.sv"
ALU   = "rtl/core/rvntt_alu.sv"
RF    = "rtl/core/rvntt_regfile.sv"

MUTATIONS = [
    # ---------------------------------------------------------------- A6 ----
    dict(step="A6", name="fwd_priority_swapped",
         why="the older producer wins over the younger one -- the exact case "
             "plan A6 names as its directed test",
         edits=[(FWD,
                 "      if      (mem_supplies && (mem_rd_addr == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_MEM;\n"
                 "      else if (wb_supplies  && (wb_rd_addr  == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_WB;",
                 "      if      (wb_supplies  && (wb_rd_addr  == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_WB;\n"
                 "      else if (mem_supplies && (mem_rd_addr == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_MEM;")],
         caught=["formal:rvntt_forward", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_rs2_port_dropped",
         why="rs2 is never forwarded, so arithmetic on a just-computed value "
             "in the second operand reads the stale register",
         edits=[(FWD, "    if (ex_uses_rs2) begin",
                      "    if (ex_uses_rs2 && (ex_rs2_addr == 5'd0)) begin")],
         caught=["formal:rvntt_forward", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_x0_suppression_dropped",
         why="a producer whose rd is x0 becomes a forwarding source, so x0 "
             "stops reading as zero",
         edits=[(FWD,
                 "  wire mem_supplies = mem_valid && mem_reg_write && !mem_mem_read &&\n"
                 "                      (mem_rd_addr != 5'd0);",
                 "  wire mem_supplies = mem_valid && mem_reg_write && !mem_mem_read;")],
         caught=["formal:rvntt_forward", "directed:a6_forward"]),

    dict(step="A6", name="fwd_store_data_not_forwarded",
         why="the store DATA operand keeps its stale register read -- plan A7 "
             "calls this the case people forget, and it never goes through the "
             "ALU so an arithmetic-only test cannot see it",
         edits=[(CORE, "          dmem_wdata = ex_rs2_fwd;\n          dmem_be    = 4'b1111;",
                       "          dmem_wdata = id_ex_q.rs2_data;\n          dmem_be    = 4'b1111;")],
         caught=["directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_valid_ignored",
         why="an && typo'd to ||, so an invalid MEM/WB slot can supply a "
             "value.  Only formal catches this at A6: the only invalid slots "
             "before A8 are the reset bubbles, whose rd is x0 and which the "
             "rd != 0 term already excludes.  A8 adds the flushed-slot case",
         edits=[(FWD, "  wire wb_supplies  = wb_valid  && wb_reg_write  && (wb_rd_addr  != 5'd0);",
                      "  wire wb_supplies  = (wb_valid  || wb_reg_write) && (wb_rd_addr  != 5'd0);")],
         caught=["formal:rvntt_forward"]),

    dict(step="A6", name="regfile_writethrough_dropped",
         why="distance 3 stops working.  Forwarding covers 1 and 2 only, so "
             "without write-through there is a hole at exactly 3 -- and a "
             "generator that pads to 3 would sit in it",
         edits=[(RF, "assign rd1 = (wr_en && (wa == ra1)) ? wd : regs[ra1];",
                     "assign rd1 = regs[ra1];")],
         # NOT a4_checksum: that program pads every RAW with three NOPs, so
         # its dependencies are at distance 4 and never touch the
         # write-through path at all.  Found by this harness reporting it as
         # PARTIAL, which is the manifest earning its keep.
         caught=["formal:rvntt_regfile", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="break_sra",
         why="the plan's own suggested injection, kept as a permanent check "
             "that the differ still localises an ordinary datapath bug",
         edits=[(ALU, "rv32i_pkg::ALU_SRA:    y = $unsigned($signed(a) >>> shamt);",
                      "rv32i_pkg::ALU_SRA:    y = a >> shamt;")],
         caught=["formal:rvntt_alu", "random:raw"]),

    # ---------------------------------------------------------------- A7 ----
    dict(step="A7", name="interlock_wired_to_mem_write",
         why="a port-wiring typo: the interlock watches mem_write instead of "
             "mem_read, so loads stop stalling and stores start.  Both halves "
             "are bugs and they are caught by different mechanisms -- the "
             "missing stall by the commit log, the phantom one by the span",
         edits=[(CORE, "      .ex_mem_read (id_ex_q.ctrl.mem_read),",
                       "      .ex_mem_read (id_ex_q.ctrl.mem_write),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="interlock_rs2_watches_rs1",
         why="the rs2 comparison is wired to rs1, so a load feeding a STORE'S "
             "DATA operand does not stall.  That operand never reaches the ALU, "
             "which is why plan A7 names it specifically",
         edits=[(CORE, "      .id_rs2_addr (id_rs2),",
                       "      .id_rs2_addr (id_rs1),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="interlock_x0_load_stalls",
         why="`lw x0, ...` becomes a stall source.  Nothing can read its "
             "result, so NO VALUE CHANGES ANYWHERE -- and every nop is "
             "`addi x0, x0, 0`, so the pipeline stalls on a large fraction of "
             "all code while remaining functionally perfect.  Only the cycle "
             "model can see this",
         edits=[(HAZ, "  wire ex_pending_load = ex_valid && ex_mem_read && (ex_rd_addr != 5'd0);",
                      "  wire ex_pending_load = ex_valid && ex_mem_read;")],
         caught=["formal:rvntt_hazard", "directed:a7_loaduse"]),

    dict(step="A7", name="interlock_stalls_on_non_source_field",
         why="the interlock keys on the rs1 FIELD rather than on whether the "
             "instruction reads rs1, so LUI and AUIPC -- whose insn[19:15] is "
             "part of an immediate -- stall behind an unrelated load.  Again no "
             "value changes; this is the phantom stall the plan warns about",
         edits=[(CORE, "      .id_uses_rs1 (id_ctrl.uses_rs1),",
                       "      .id_uses_rs1 (1'b1),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_lets_the_pc_advance",
         why="IF is not held, so the fetch stream runs on by one during the "
             "bubble and an instruction is skipped entirely",
         edits=[(CORE, "    else if (stall) pc_q <= pc_q;          // A8 adds a redirect ahead of this\n", "")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_forgets_the_instruction_hold",
         why="the held word is never replayed, so the stalled slot decodes the "
             "NEXT instruction while carrying the previous pc.  This is the "
             "failure mode that makes the hold register necessary at all: "
             "holding pc_q and if_id_q is not enough, because the RAM's output "
             "register has already moved on",
         edits=[(CORE, "      insn_held_q <= stall;", "      insn_held_q <= 1'b0;")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_injects_no_bubble",
         why="ID/EX is not cleared, so the consumer is issued twice -- a "
             "stalled cycle retires an instruction, which is exactly what plan "
             "A7's done-when forbids",
         edits=[(CORE, "    end else if (stall) begin\n      id_ex_q <= '0;\n", "    end else begin\n" if False else "    end else if (1'b0) begin\n      id_ex_q <= '0;\n")],
         caught=["directed:a7_loaduse", "random:loaduse"]),
]

# ------------------------------------------------------------------- the tests
RANDOM_SUITES = {
    # Small on purpose: these run once per mutation, and the acceptance runs
    # (1000 programs) are a separate thing.  A mutation that needs more than a
    # handful of random programs to show up is a mutation the random suite
    # should not be credited with catching.
    "raw":      dict(n=8, length=250, raw=1.0, lu=0.0, br=0.0, seed=0xA6000000),
    "loaduse":  dict(n=8, length=250, raw=1.0, lu=1.0, br=0.0, seed=0xA7000000),
    "branch":   dict(n=8, length=250, raw=1.0, lu=1.0, br=0.12, seed=0xA8000000),
}


def mirror_rtl(work, name, edits):
    """Copy the RTL tree, apply `edits`, return (dir, ok)."""
    d = os.path.join(work, "rtl_" + name)
    for p in t4.RTL:
        dst = os.path.join(d, os.path.relpath(p, ROOT))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(p, dst)
    for rel, old, new in edits:
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            return d, f"{rel} is not in the build's source list"
        s = open(fp).read()
        if old not in s:
            return d, f"anchor not found in {rel}"
        open(fp, "w").write(s.replace(old, new, 1))
    return d, None


def build(work, name, rtl_dir, image):
    """Build the traced simulator from `rtl_dir`.  Returns (exe, error)."""
    build_dir = os.path.join(work, "obj_" + name)
    srcs = [os.path.join(rtl_dir, os.path.relpath(p, ROOT)) for p in t4.RTL]
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_trace_top",
           "--Mdir", build_dir, "--prefix", "Vrvntt_trace_top",
           "-CFLAGS", "-DVTOP=Vrvntt_trace_top",
           '-GINIT_FILE="%s"' % image, "-GWORDS=16384"] + srcs + a5.TRACE_RTL + [a5.TB]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        tail = r.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        return None, " / ".join(tail)
    return os.path.join(build_dir, "Vrvntt_trace_top"), None


def run_formal(work, name, rtl_dir, design):
    """Run SymbiYosys on the (possibly mutated) design.  True if it PASSES."""
    src = os.path.join(rtl_dir, "rtl/core", design + ".sv")
    if not os.path.exists(src):
        src = os.path.join(rtl_dir, "rtl/soc", design + ".sv")
    # with_deps resolves packages against the real tree, so a mutated module
    # is read against the unmutated package -- which is what is wanted: no
    # mutation here touches rv32i_pkg.
    srcs = with_deps(src)
    wd = os.path.join(work, "formal_%s_%s" % (name, design))
    os.makedirs(wd, exist_ok=True)
    sby = os.path.join(wd, design + ".sby")
    reads = "\n".join("read -formal %s" % os.path.basename(s) for s in srcs)
    open(sby, "w").write(
        "[options]\nmode bmc\ndepth 8\n\n[engines]\nsmtbmc bitwuzla\n\n"
        "[script]\nread -define FORMAL\n%s\nprep -top %s\n\n[files]\n%s\n"
        % (reads, design, "\n".join(srcs)))
    r = subprocess.run(["sby", "-f", sby], cwd=wd,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def run_program_test(exe, elf, work, image, tag):
    """True if the commit logs match (i.e. the test PASSES)."""
    return a5.run_one(exe, elf, work, image, tag, verbose=False)


class Fixture:
    """Everything that does not depend on the mutation, built once."""

    def __init__(self, work, image):
        self.work = work
        self.image = image
        self.elfs = {}

    def directed_elf(self, prog):
        if prog not in self.elfs:
            src = dict(dr.PROGRAMS)[prog]
            self.elfs[prog] = dr.compile_s(src, self.work, "d_" + prog)
        return self.elfs[prog]

    def a4_elf(self):
        if "a4" not in self.elfs:
            self.elfs["a4"] = t4.build_elf(self.work)
        return self.elfs["a4"]

    def random_elfs(self, suite):
        key = "r_" + suite
        if key not in self.elfs:
            cfg = RANDOM_SUITES[suite]
            out = []
            for i in range(cfg["n"]):
                seed = cfg["seed"] + i
                src = gen_random_prog.generate(seed, cfg["length"], None,
                                               cfg["raw"], cfg["lu"], cfg["br"])
                out.append(a5.assemble(src, self.work, "%s_%d" % (key, i)))
            self.elfs[key] = out
        return self.elfs[key]


def run_test(kind, fx, exe, rtl_dir, work, mut_name, quiet=True):
    """Run one named test.  True = PASSED (so False = caught the mutation)."""
    # A failing cosim prints its whole first-divergence report.  That is the
    # right behaviour for the acceptance runs and the wrong one here, where a
    # failure is the EXPECTED outcome and there may be dozens of them: the
    # verdict table is the report.  The baseline run is not quiet, because
    # there a failure is real and its detail is the point.
    if quiet:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return _run_test(kind, fx, exe, rtl_dir, work, mut_name)
    return _run_test(kind, fx, exe, rtl_dir, work, mut_name)


def _run_test(kind, fx, exe, rtl_dir, work, mut_name):
    if kind.startswith("formal:"):
        return run_formal(work, mut_name, rtl_dir, kind.split(":", 1)[1])
    if kind.startswith("directed:"):
        prog = kind.split(":", 1)[1]
        return run_program_test(exe, fx.directed_elf(prog), work, fx.image, prog)
    if kind == "a4":
        return run_program_test(exe, fx.a4_elf(), work, fx.image, "a4")
    if kind.startswith("random:"):
        suite = kind.split(":", 1)[1]
        for i, elf in enumerate(fx.random_elfs(suite)):
            if not run_program_test(exe, elf, work, fx.image, "%s#%d" % (suite, i)):
                return False
        return True
    raise ValueError("unknown test kind: " + kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default=None, help="only mutations for this step")
    ap.add_argument("--only", default=None, help="one mutation by name")
    a = ap.parse_args()

    muts = [m for m in MUTATIONS
            if (a.step is None or m["step"] == a.step)
            and (a.only is None or m["name"] == a.only)]
    if not muts:
        print("no mutations selected")
        return 2

    work = tempfile.mkdtemp(prefix="mutate_")
    problems = 0
    try:
        image = os.path.join(work, "image.hex")
        open(image, "w").write("00000000\n")
        fx = Fixture(work, image)

        # ---- baseline: unmutated RTL must pass everything the manifest names.
        wanted = sorted({t for m in muts for t in m["caught"]})
        base_dir, err = mirror_rtl(work, "base", [])
        assert err is None, err
        base_exe, err = build(work, "base", base_dir, image)
        if base_exe is None:
            print("BASELINE BUILD FAILED: " + err)
            return 1
        print("baseline (unmutated RTL):")
        for kind in wanted:
            ok = run_test(kind, fx, base_exe, base_dir, work, "base",
                          quiet=False)
            print("  %-28s %s" % (kind, "pass" if ok else "FAIL <-- stuck-at-fail"))
            if not ok:
                problems += 1
        if problems:
            print("\nbaseline is not clean; mutation results would be meaningless")
            return 1

        # ---- the mutations.
        print("\n%-32s %-10s %s" % ("mutation", "verdict", "caught by"))
        print("-" * 96)
        for m in muts:
            d, err = mirror_rtl(work, m["name"], m["edits"])
            if err:
                print("%-32s %-10s %s" % (m["name"], "NO-OP", err))
                problems += 1
                continue
            exe, err = build(work, m["name"], d, image)
            if exe is None:
                print("%-32s %-10s %s" % (m["name"], "BUILD-ERR", err))
                print("    A mutation that does not compile proves nothing; "
                      "reformulate it so every bit stays referenced.")
                problems += 1
                continue

            caught_by, missed = [], []
            for kind in m["caught"]:
                if run_test(kind, fx, exe, d, work, m["name"]):
                    missed.append(kind)
                else:
                    caught_by.append(kind)

            if not m["caught"]:
                print("%-32s %-10s (no catcher declared -- see the manifest)"
                      % (m["name"], "UNCHECKED"))
            elif not caught_by:
                print("%-32s %-10s declared: %s" % (m["name"], "ESCAPED",
                                                    ", ".join(m["caught"])))
                problems += 1
            elif missed:
                print("%-32s %-10s caught: %s | NOT by: %s"
                      % (m["name"], "PARTIAL", ", ".join(caught_by),
                         ", ".join(missed)))
                problems += 1
            else:
                print("%-32s %-10s %s" % (m["name"], "CAUGHT",
                                          ", ".join(caught_by)))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n=== %s ===" % ("ALL MUTATIONS CAUGHT AS DECLARED" if problems == 0
                            else "%d PROBLEM(S)" % problems))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
