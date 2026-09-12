#!/usr/bin/env python3
"""
Regression runner.

Runs with the system python3 and no venv; a test whose tools are missing is
reported SKIP, never FAIL, and never silently dropped.  Every test is a
subprocess with a timeout.  Exit status is nonzero if any test FAILs.

Tiers:
  RVNTT_FAST=1        drop mutation_pipeline, riscv_formal and formal_muldiv
                      (84% of the wall clock); the summary says so
  RVNTT_NO_MUTATE=1   drop the mutation set only
  RVNTT_HW=1          also program the board (soc_hardware, bench_hardware)
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, SKIP, XFAIL = "PASS", "FAIL", "SKIP", "XFAIL"

FAST = os.environ.get("RVNTT_FAST") == "1"
FAST_SKIPS = ["mutation_pipeline", "riscv_formal", "formal_muldiv"]

@dataclass
class Test:
    name: str
    group: str
    argv: list            # command to run
    cwd: str = ROOT
    timeout: int = 600
    requires: list = field(default_factory=list)   # executables that must exist
    expect_fail: bool = False   # harness self-check: the command MUST return nonzero
    env: dict = field(default_factory=dict)

@dataclass
class Result:
    test: Test
    status: str
    seconds: float
    detail: str = ""
    output: str = ""

def discover() -> list:
    """Test registry."""
    py = sys.executable
    t = []

    # ---- infrastructure ----
    t.append(Test("verilator_lint", "rtl",
                  [py, os.path.join(ROOT, "tb/lint_all.py")],
                  requires=["verilator"], timeout=300))

    t.append(Test("verilator_sim_sync_reset", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_sync_reset_verilator.py")],
                  requires=["verilator"], timeout=300))

    t.append(Test("verilator_sim_blinky", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_blinky_verilator.py")],
                  requires=["verilator"], timeout=600))

    t.append(Test("formal_sync_reset", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_sync_reset"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("cocotb_sync_reset", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "sync_reset"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=600))

    t.append(Test("spike_smoke", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/run_spike_smoke.py")],
                  requires=["spike", "riscv-none-elf-gcc"], timeout=300))

    # ---- the pipeline, unit by unit ----
    t.append(Test("verilator_sim_regfile", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_regfile_verilator.py")],
                  requires=["verilator"], timeout=300))

    # Depth 8: the deepest regfile property spans two cycles, and each BMC step
    # adds a symbolic write to a 32x32 memory (depth 20 does not finish).
    t.append(Test("formal_regfile", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_regfile", "--depth", "8"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("cocotb_alu", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "alu"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=900))

    # IMMGEN_EXHAUSTIVE=1 adds 2^20 sweeps of the U and J fields (~25 s).
    t.append(Test("cocotb_immgen", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "immgen"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=900))

    # Combinational units prove at depth 2.
    t.append(Test("formal_alu", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_alu", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_immgen", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_immgen", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # 10^6 random words against the Python decoder (~40 s); not sampled down,
    # because the legal/illegal boundary is what a smaller run under-covers.
    t.append(Test("cocotb_decode", "cocotb",
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                   "--design", "decode"],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=1200))

    t.append(Test("formal_bitmanip", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_bitmanip", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_decode", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_decode", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_forward", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_forward", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_hazard", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_hazard", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_branch", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_branch", "--depth", "2"],
                  requires=["sby", "yosys"], timeout=600))

    # Depth 6: the trap/MRET properties span two cycles and $past needs history.
    t.append(Test("formal_csr", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_csr", "--depth", "6"],
                  requires=["sby", "yosys"], timeout=600))

    # Depth 37: a divide presents its result on its 34th EX cycle.  Proves the
    # magnitude loop via a per-iteration invariant (~3 min).
    t.append(Test("formal_muldiv", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_muldiv", "--depth", "37"],
                  requires=["sby", "yosys"], timeout=1800))

    # Depth 12: the deepest property (RAS occupancy bound) needs RAS_ENTRIES+2
    # steps.  Small BTB/RAS so the proof is about the mechanism.
    t.append(Test("formal_bpred", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_bpred", "--depth", "12",
                   "--param", "BTB_ENTRIES=8", "--param", "RAS_ENTRIES=4"],
                  requires=["sby", "yosys"], timeout=1800))

    # ---- the whole core ----
    t.append(Test("core_a4_checksum", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_core_verilator.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # Lockstep commit-log cosimulation against Spike over generated random
    # programs, plus a cycle-span check against tb/cosim/cycle_model.py (a
    # phantom stall leaves the commit log byte-identical).  Densities: RAW and
    # load-use at 1.0; branches 0.12 (an all-branch program retires little);
    # mul 0.10 (a 34-cycle divide exercises nothing else); B 0.15.
    t.append(Test("cosim_commit_log", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_cosim_a5.py"),
                   "-n", "100", "--len", "300",
                   "--raw-density", "1.0", "--load-use-density", "1.0",
                   "--branch-density", "0.12", "--mul-density", "0.10",
                   "--bm-density", "0.15"],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=1800))

    # Hand-written programs with exact hazard distances, diffed against Spike.
    t.append(Test("cosim_directed", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_cosim_directed.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # Directed CSR/trap cases; minstret after a known program equals Spike's count.
    t.append(Test("csr_traps_minstret", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_csr_traps.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # The Zihpm counters' CSR-level contract (existence, WARL, read-zero for
    # 9..31, mcountinhibit).  Event attribution is checked by stall_profile.
    t.append(Test("hpm_counters", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_hpm_a20.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=900))

    # riscv-tests (rv32ui, rv32um, rv32mi).  SKIPs if the checkout is absent.
    t.append(Test("riscv_tests", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/test_riscv_tests.py")],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=1800))

    # RISCOF over riscv-arch-test with Spike as the reference.  SKIPs if the
    # checkout or the riscof package is missing.  Slow.
    t.append(Test("riscof_arch_test", "cosim",
                  [py, os.path.join(ROOT, "tb/riscof/run_riscof.py"),
                   "--no-save-report"],
                  requires=["verilator", "riscv-none-elf-gcc", "spike"],
                  timeout=3600))

    # riscv-formal: 77 checks at depth 14 through the RVFI port.  SKIPs if the
    # checkout is absent.
    t.append(Test("riscv_formal", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_riscv_formal.py")],
                  requires=["sby", "yosys"], timeout=1800))

    # ---- the SoC ----
    t.append(Test("soc_sim", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_soc_verilator.py")],
                  requires=["verilator", "riscv-none-elf-gcc"], timeout=900))

    t.append(Test("soc_uart_parser", "meta",
                  [py, os.path.join(ROOT, "tb/fpga/parse_soc_uart.py"), "--selftest"],
                  timeout=120))

    # Every constraint file's pins against the vendor master XDC.
    t.append(Test("xdc_pin_map", "fpga",
                  [py, os.path.join(ROOT, "tb/fpga/check_xdc_pins.py")],
                  timeout=120))

    # Program the board and parse its UART.  Opt-in (RVNTT_HW=1); SKIPs if the
    # board or the bitstream is absent.
    t.append(Test("soc_hardware", "fpga",
                  [py, os.path.join(ROOT, "fpga/scripts/hw_bringup.py"), "--regress"],
                  timeout=900))

    # ---- the benchmarks ----
    # The formatter diffed against glibc; the port compiled natively against
    # CoreMark's CRCs and Dhrystone's published values; the image on the RTL.
    t.append(Test("bench_printf", "meta",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_printf.py"),
                   "--inject"],
                  timeout=300))

    t.append(Test("bench_host", "sw",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_host.py")],
                  timeout=600))

    # Three blocks: the first runs on a cold predictor and is discarded, and two
    # must remain for the reproducibility check to compare.
    t.append(Test("bench_sim", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_bench_verilator.py"),
                   "--blocks", "3"],
                  requires=["verilator", "riscv-none-elf-gcc"], timeout=1800))

    t.append(Test("bench_uart_parser", "meta",
                  [py, os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"),
                   "--selftest"],
                  timeout=120))

    t.append(Test("bench_compare", "meta",
                  [py, os.path.join(ROOT, "tb/fpga/compare_bench_runs.py"),
                   "--selftest"],
                  timeout=120))

    # The stall-attribution instrument; --selftest breaks the accounting six ways.
    t.append(Test("stall_profile", "perf",
                  [py, os.path.join(ROOT, "tb/perf/run_stall_profile.py"),
                   "--dhry-runs", "40", "--iterations", "1",
                   "--arch", "rv32im", "--selftest"],
                  requires=["verilator", "riscv-none-elf-gcc"], timeout=1800))

    # The benchmarks on the board.  Same opt-in and SKIP rules as soc_hardware.
    # The bitstream is built by `make bench-bitstream` (see the README); at
    # 96 MHz a block is about 25 s, and --min-blocks 3 after one warm-up block
    # needs four, so 120 s leaves room for the partial block a capture starts in.
    t.append(Test("bench_hardware", "fpga",
                  [py, os.path.join(ROOT, "fpga/scripts/hw_bringup.py"),
                   "--regress",
                   "--bit", os.path.join(ROOT, "fpga/build/bench/rvntt_soc_top.bit"),
                   "--seconds", "120", "--send-byte", "-1",
                   "--out-name", "bench_uart.log",
                   "--parser", os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"),
                   # `--parser-arg=--flag`: argparse rejects the separated form.
                   "--parser-arg=--min-blocks", "--parser-arg=3",
                   "--parser-arg=--warmup-blocks", "--parser-arg=1",
                   "--parser-arg=--json",
                   "--parser-arg=" + os.path.join(ROOT, "fpga/build/bench/bench_regress.json")],
                  timeout=1800))

    # ---- pre-flights (20 ms each) ----
    # The ISA string in every place it is written, and misa in both of its.  A
    # source naming a smaller ISA than the DUT does not fail: Spike traps on the
    # first unknown instruction and hangs.
    t.append(Test("isa_consistency", "meta",
                  [py, os.path.join(ROOT, "tb/unit/test_isa_consistency.py")],
                  timeout=120))

    # ---- Zkr ----
    # The health tests must fire: stuck, biased and periodic stub sources must
    # reach DEAD, and an ideal one must not.
    t.append(Test("entropy_health", "rtl",
                  [py, os.path.join(ROOT, "tb/unit/test_entropy_health.py")],
                  requires=["verilator"], timeout=600))

    # Small window so every counterexample is short.
    t.append(Test("formal_entropy_health", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_entropy_health", "--depth", "30",
                   "--param", "REP_CUTOFF=5", "--param", "AP_WINDOW=8",
                   "--param", "AP_CUTOFF=6"],
                  requires=["sby", "yosys"], timeout=600))

    t.append(Test("formal_seed", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_formal.py"),
                   "--design", "rvntt_seed", "--depth", "24",
                   "--extra", "rtl/core/rvntt_entropy.sv",
                   "--extra", "rtl/core/rvntt_entropy_health.sv",
                   "--param", "BIST_SAMPLES=4", "--param", "REP_CUTOFF=5",
                   "--param", "AP_WINDOW=8", "--param", "AP_CUTOFF=6"],
                  requires=["sby", "yosys"], timeout=600))

    # ---- Zkt ----
    # Claim B by cone of influence: `done` is not reached by the operands.
    # Claim A is an assertion in rvntt_core proved by riscv_formal.
    t.append(Test("zkt_latency", "formal",
                  [py, os.path.join(ROOT, "tb/formal/run_zkt.py")],
                  requires=["yosys"], timeout=300))

    # ---- mutation testing ----
    # Anchor pre-flight: a mutation whose search text no longer matches would
    # mutate nothing and be reported NO-OP only after the full run.
    t.append(Test("mutation_anchors", "meta",
                  [py, os.path.join(ROOT, "tb/mutate/run_mutation.py"),
                   "--check-anchors"],
                  timeout=120))

    # On by default and run whole: it is the only test that checks the other
    # tests, and an unrun manifest rots silently.  About 9 minutes in parallel.
    if not FAST and os.environ.get("RVNTT_NO_MUTATE") != "1":
        t.append(Test("mutation_pipeline", "meta",
                      [py, os.path.join(ROOT, "tb/mutate/run_mutation.py")],
                      requires=["verilator", "riscv-none-elf-gcc", "spike",
                                "sby", "yosys"],
                      timeout=3600))

    # ---- harness self-check: proves FAIL is actually detected ----
    t.append(Test("harness_detects_failure", "meta",
                  [py, "-c", "import sys; sys.exit(3)"],
                  expect_fail=True, timeout=30))
    return t

def run_one(tst: Test, verbose: bool) -> Result:
    missing = [r for r in tst.requires if shutil.which(r) is None]
    if missing:
        return Result(tst, SKIP, 0.0, f"missing tool: {','.join(missing)}")
    if not os.path.exists(tst.argv[-1]) and tst.argv[-1].endswith((".sv", ".py")):
        return Result(tst, SKIP, 0.0, f"missing file: {os.path.relpath(tst.argv[-1], ROOT)}")

    env = dict(os.environ); env.update(tst.env)
    t0 = time.time()
    try:
        p = subprocess.run(tst.argv, cwd=tst.cwd, env=env, timeout=tst.timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        rc, out = p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return Result(tst, FAIL, time.time() - t0, f"TIMEOUT after {tst.timeout}s")
    except FileNotFoundError as e:
        return Result(tst, SKIP, time.time() - t0, str(e))
    dt = time.time() - t0

    if tst.expect_fail:
        ok = rc != 0
        detail = "" if ok else "expected nonzero exit, got 0"
        return Result(tst, XFAIL if ok else FAIL, dt, detail, out)
    # A test that exits 0 after printing a `<NAME>_SKIP:` line could not run
    # (missing checkout, unplugged board) and is a SKIP, not a pass.
    for line in out.splitlines():
        line = line.strip()
        if line.split(":", 1)[0].endswith("SKIP") and line.split(":", 1)[0].isupper():
            return Result(tst, SKIP, dt, line.split(":", 1)[-1].strip()[:60], out)

    ok = rc == 0
    return Result(tst, PASS if ok else FAIL, dt, "" if ok else f"exit {rc}", out)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default=None, help="substring filter on test name")
    ap.add_argument("-v", dest="verbose", action="store_true", help="show output of every test")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    tests = discover()
    if FAST:
        tests = [t for t in tests if t.name not in FAST_SKIPS]
    if a.filter:
        tests = [t for t in tests if a.filter in t.name or a.filter == t.group]
    if a.list:
        for t in tests:
            print(f"{t.group:8s} {t.name}")
        return 0
    if not tests:
        print("ERROR: no tests matched", file=sys.stderr)
        return 2

    print(f"=== regression: {len(tests)} test(s) ===\n")
    results = []
    for t in tests:
        print(f"  running {t.name} ...", flush=True)
        r = run_one(t, a.verbose)
        results.append(r)
        if a.verbose or r.status == FAIL:
            if r.output:
                lines = r.output.rstrip().splitlines()
                print("    " + "\n    ".join(lines[-40:]))
                # A failing test's evidence may be above the 40-line tail
                # (mutation_pipeline's is), so surface failure-shaped lines too.
                if r.status == FAIL and len(lines) > 40:
                    marks = ("FAIL", "ESCAPED", "PARTIAL", "BUILD-ERR",
                             "NO-OP", "UNCHECKED", "MISSED", "ERROR",
                             "Traceback", "stuck-at-fail")
                    hits = [(i, l) for i, l in enumerate(lines[:-40], 1)
                            if any(m in l for m in marks)]
                    if hits:
                        print("    --- failure-shaped lines above the tail ---")
                        for i, l in hits[:20]:
                            print("    %5d| %s" % (i, l.rstrip()))
                        if len(hits) > 20:
                            print("    ... %d more" % (len(hits) - 20))

    w = max(len(r.test.name) for r in results)
    print("\n" + "=" * (w + 34))
    print(f"{'TEST'.ljust(w)}  {'GROUP':8s} {'STATUS':7s} {'TIME':>7s}  DETAIL")
    print("-" * (w + 34))
    for r in results:
        print(f"{r.test.name.ljust(w)}  {r.test.group:8s} {r.status:7s} "
              f"{r.seconds:6.2f}s  {r.detail}")
    print("=" * (w + 34))

    n = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP, XFAIL)}
    print(f"passed {n[PASS]}   xfail {n[XFAIL]}   skipped {n[SKIP]}   FAILED {n[FAIL]}")
    if FAST:
        print("*** RVNTT_FAST=1: this was the EDIT-LOOP TIER.  Not run: "
              + ", ".join(FAST_SKIPS) + ".")
        print("*** It is not a regression result.  Run the full set at a step "
              "boundary before claiming anything.")
    if n[SKIP]:
        print("note: SKIPs are missing tools, not passes -- see status above.")
    if n[FAIL]:
        print("\nREGRESSION FAILED")
        return 1
    print("\nREGRESSION PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
