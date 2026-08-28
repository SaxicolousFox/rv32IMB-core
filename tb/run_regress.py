#!/usr/bin/env python3
"""
Project regression runner.

Design notes (this is deliberately simple and dependency-free):
  * It must run with the SYSTEM python3, with no venv and no cocotb installed,
    so that `make regress` never fails for reasons unrelated to the DUT.
    Tests whose tools are missing are reported SKIP, not FAIL -- but a SKIP is
    still visible in the table so it can never quietly rot into "we have no tests".
  * Exit code is nonzero if ANY test FAILs (P0.2 acceptance criterion).
  * Every test is a subprocess with a timeout, so a hung simulator cannot wedge
    the whole regression.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, SKIP, XFAIL = "PASS", "FAIL", "SKIP", "XFAIL"

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
    """Test registry. Add tests here as the project grows."""
    py = sys.executable
    t = []

    # ---- P0.2 placeholders that nonetheless exercise the real toolchain ----
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
                  [py, os.path.join(ROOT, "tb/cocotb/run_cocotb.py")],
                  cwd=os.path.join(ROOT, "tb/cocotb"),
                  requires=["verilator"], timeout=600))

    t.append(Test("spike_smoke", "cosim",
                  [py, os.path.join(ROOT, "tb/cosim/run_spike_smoke.py")],
                  requires=["spike", "riscv-none-elf-gcc"], timeout=300))

    t.append(Test("model_selftest", "model",
                  [py, os.path.join(ROOT, "model/selftest.py")], timeout=120))

    t.append(Test("patches_in_sync", "toolchain",
                  [py, os.path.join(ROOT, "tb/cosim/check_patches.py")], timeout=120))

    t.append(Test("kyber_ref_kats", "model",
                  [py, os.path.join(ROOT, "model/run_kyber_kats.py")], timeout=900))

    t.append(Test("ntt_golden_compare", "model",
                  [py, os.path.join(ROOT, "model/compare_ntt.py"), "-n", "1000"],
                  timeout=900))

    # ---- harness self-check: proves FAIL is actually detected (see P0.2) ----
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
    ok = rc == 0
    return Result(tst, PASS if ok else FAIL, dt, "" if ok else f"exit {rc}", out)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default=None, help="substring filter on test name")
    ap.add_argument("-v", dest="verbose", action="store_true", help="show output of every test")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    tests = discover()
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
                print("    " + "\n    ".join(r.output.rstrip().splitlines()[-40:]))

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
    if n[SKIP]:
        print("note: SKIPs are missing tools, not passes -- see status above.")
    if n[FAIL]:
        print("\nREGRESSION FAILED")
        return 1
    print("\nREGRESSION PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
