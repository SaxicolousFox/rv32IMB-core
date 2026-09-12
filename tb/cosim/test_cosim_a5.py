#!/usr/bin/env python3
"""
Run programs on Spike and the RTL and diff their commit logs.

The hand-written checksum program plus a batch of generated random ones.
The simulator is built once: the memory image is a module parameter, so the
RAM's INIT_FILE points at a fixed path that this script rewrites before each
run.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import spike_asm          # noqa: E402
import commit_diff        # noqa: E402
import cycle_model        # noqa: E402

# The span identity's control term is `2 x redirects` on a statically
# not-taken core and `2 x mispredicts` on a predicting one.  A constant rather
# than a probe of the RTL, so the model stays independent of the design.
CORE_HAS_PREDICTOR = True
import gen_random_prog    # noqa: E402
import test_core_verilator as t4   # noqa: E402

TB = os.path.join(ROOT, "tb/unit/tb_core.cpp")
TRACE_RTL = [
    os.path.join(ROOT, "tb/unit/rvntt_trace.sv"),
    os.path.join(ROOT, "tb/unit/rvntt_trace_top.sv"),
]


def build_sim(tmp, image_path):
    """Build the traced simulator once, with INIT_FILE bound to `image_path`."""
    build = os.path.join(tmp, "obj")
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_trace_top",
           "--Mdir", build, "--prefix", "Vrvntt_trace_top",
           "-CFLAGS", "-DVTOP=Vrvntt_trace_top",
           '-GINIT_FILE="%s"' % image_path,
           "-GWORDS=16384"] + t4.RTL + TRACE_RTL + [TB]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError("building the traced simulator failed")
    return os.path.join(build, "Vrvntt_trace_top")


def assemble(src_text, tmp, name):
    src = os.path.join(tmp, name + ".S")
    elf = os.path.join(tmp, name + ".elf")
    open(src, "w").write(src_text)
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=" + spike_asm.MARCH, "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", t4.LD, "-o", elf, src],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError(f"assembling {name} failed")
    return elf


SPAN_RE = re.compile(r"span=(-?\d+)")


def run_one(exe, elf, tmp, image_path, name, context=6, verbose=False,
            check_cycles=True):
    """
    Load `elf`'s image, run the RTL, diff against Spike.  True if identical.

    Two checks, not one.  The commit-log diff covers architectural state; the
    span check covers TIMING, which the diff is structurally unable to see -- a
    phantom stall produces a byte-identical log.  See tb/cosim/cycle_model.py.
    """
    hexf, _n = t4.elf_to_hex(elf, tmp)
    shutil.copy(hexf, image_path)

    trace = os.path.join(tmp, "rtl_trace.log")
    if os.path.exists(trace):
        os.remove(trace)
    handler_pc = spike_asm.symbol(elf, "trap_handler")
    r = subprocess.run([exe, "--no-check", "+trace_file=" + trace,
                        "--stop-pc", "0x%08x" % handler_pc,
                        "--max-cycles", "2000000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not os.path.exists(trace):
        print(f"  {name}: RTL run failed\n    " + out.replace("\n", "\n    "))
        return False

    handler = spike_asm.symbol(elf, "trap_handler")
    sp = commit_diff.spike_records(elf)
    rt = commit_diff.rtl_records(trace, stop_pc=handler)
    report = commit_diff.diff(sp, rt, context)
    if report is not None:
        print(f"\n=== {name}: COMMIT LOGS DIVERGE ===")
        print(report)
        return False

    if check_cycles:
        m = SPAN_RE.search(out)
        if not m:
            print(f"\n=== {name}: no span reported ===\n    " + out)
            return False
        actual = int(m.group(1))
        pred = cycle_model.analyse(rt, predictor=CORE_HAS_PREDICTOR)
        if pred["span"] != actual:
            print(f"\n=== {name}: CYCLE COUNT DISAGREES ===")
            print("  The commit logs are byte-identical, so this is a timing "
                  "bug, not a data one.")
            print(cycle_model.explain(pred, actual))
            return False

    if verbose:
        print(f"  {name}: OK  ({len(sp)} commits byte-identical)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=20,
                    help="number of random programs")
    ap.add_argument("--len", type=int, default=250,
                    help="body instructions per random program")
    ap.add_argument("--seed", type=lambda v: int(v, 0), default=0xA5A50000,
                    help="base seed; accepts 0x... as well as decimal")
    ap.add_argument("--raw-density", type=float, default=0.0)
    ap.add_argument("--load-use-density", type=float, default=0.0)
    ap.add_argument("--branch-density", type=float, default=0.0)
    ap.add_argument("--mul-density", type=float, default=0.0,
                    help="probability an instruction is an M one")
    ap.add_argument("--bm-density", type=float, default=0.0,
                    help="density of B / Zbkb / Zicond instructions")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")     # placeholder for the build
        exe = build_sim(tmp, image)

        failures = []

        # 1. The hand-written checksum program.
        elf = t4.build_elf(tmp)
        if not run_one(exe, elf, tmp, image, "a4_checksum", verbose=True):
            failures.append("a4_checksum")

        # 2. Generated programs.
        ok = 0
        for i in range(a.num):
            seed = a.seed + i
            src = gen_random_prog.generate(
                seed, a.len, None, a.raw_density,
                a.load_use_density, a.branch_density, a.mul_density,
                a.bm_density)
            name = f"rand_seed{seed:08x}"
            elf = assemble(src, tmp, name)
            if run_one(exe, elf, tmp, image, name, verbose=a.verbose):
                ok += 1
            else:
                failures.append(name)
                # Keep the offending source where it can be looked at.
                keep = os.path.join(tempfile.gettempdir(), name + ".S")
                shutil.copy(os.path.join(tmp, name + ".S"), keep)
                print(f"  (source kept at {keep})")
                if len(failures) >= 3:
                    break

        print(f"\nrandom programs: {ok}/{a.num} byte-identical "
              f"(len={a.len}, raw={a.raw_density}, "
              f"load_use={a.load_use_density}, branch={a.branch_density}, "
              f"mul={a.mul_density}, bm={a.bm_density})")

        if failures:
            print("COSIM_FAIL: " + ", ".join(failures))
            return 1
        print("COSIM_OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
