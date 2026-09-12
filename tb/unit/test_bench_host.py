#!/usr/bin/env python3
"""
Build and run the benchmarks natively, as a correctness check only.

The host has no mcycle, so bench_main.c prints `host=1` and
tb/fpga/parse_bench_uart.py refuses to derive a score.  What is checked is
CoreMark's CRCs and Dhrystone's final variable values, from the same sources
the bitstream is built from: wrong on the board and right here means the core
is wrong; wrong in both means the port is.
"""
import argparse, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "sw", "bench")
DHRY  = os.path.join(ROOT, "toolchain", "riscv-tests", "benchmarks", "dhrystone")
CM    = os.path.join(ROOT, "toolchain", "coremark")

DHRY_W = ["-Wno-implicit-int", "-Wno-implicit-function-declaration",
          "-Wno-return-type", "-Wno-old-style-definition", "-Wno-builtin-declaration-mismatch"]


def build(exe, dhry_runs, iterations):
    incs = ["-I" + BENCH, "-I" + os.path.join(BENCH, "include"),
            "-I" + os.path.join(BENCH, "coremark_port"), "-I" + CM, "-I" + DHRY]
    common = ["-O2", "-DBENCH_HOST=1", "-DCORE_HZ=1000000000u",
              "-DDHRY_RUNS=%d" % dhry_runs, "-DITERATIONS=%d" % iterations,
              "-DBENCH_GAP_CYCLES=0u", "-DBENCH_BLOCKS=1", "-DPASS2=1",
              '-DBENCH_FLAGS="host functional run -- NOT a measurement"'] + incs
    cm = ["-DPERFORMANCE_RUN=1", "-DTOTAL_DATA_SIZE=2000",
          '-DCOMPILER_FLAGS="host functional run"']
    # bench_lib.c is absent on purpose: glibc supplies memcpy/strcpy here.
    srcs = [(os.path.join(BENCH, "bench_main.c"), []),
            (os.path.join(BENCH, "bench_io.c"), []),
            (os.path.join(BENCH, "dhry_glue.c"), ["-std=gnu89"]),
            (os.path.join(DHRY, "dhrystone_main.c"),
             ["-std=gnu89", "-Dmain=dhry_main"] + DHRY_W),
            (os.path.join(DHRY, "dhrystone.c"), ["-std=gnu89"] + DHRY_W),
            (os.path.join(BENCH, "coremark_port", "core_portme.c"), cm),
            (os.path.join(CM, "core_main.c"), cm + ["-Dmain=coremark_main"]),
            (os.path.join(CM, "core_list_join.c"), cm),
            (os.path.join(CM, "core_matrix.c"), cm),
            (os.path.join(CM, "core_state.c"), cm),
            (os.path.join(CM, "core_util.c"), cm)]

    objdir = os.path.dirname(exe)
    objs = []
    for src, extra in srcs:
        obj = os.path.join(objdir, os.path.basename(src)[:-2] + ".o")
        r = subprocess.run(["cc"] + common + extra + ["-c", "-o", obj, src],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            sys.stderr.write(r.stdout.decode("utf-8", "replace"))
            return None
        objs.append(obj)
    r = subprocess.run(["cc", "-o", exe] + objs,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        sys.stderr.write(r.stdout.decode("utf-8", "replace"))
        return None
    return exe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dhry-runs", type=int, default=2000)
    ap.add_argument("--iterations", type=int, default=2)
    ap.add_argument("--keep", default=None)
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        exe = build(os.path.join(td, "bench_host"), a.dhry_runs, a.iterations)
        if exe is None:
            return 1
        r = subprocess.run([exe], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=600)
        out = r.stdout.decode("utf-8", "replace")
        cap = a.keep or os.path.join(td, "host.log")
        with open(cap, "w") as f:
            f.write(out)
        p = subprocess.run([sys.executable,
                            os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"),
                            cap, "--functional-only",
                            "--dhry-runs", str(a.dhry_runs),
                            "--iterations", str(a.iterations)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(p.stdout.decode("utf-8", "replace").strip())
        return 0 if (r.returncode == 0 and p.returncode == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
