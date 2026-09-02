#!/usr/bin/env python3
"""
Directed cosimulation tests: hand-written programs, diffed against Spike.

The random generator in gen_random_prog.py covers hazards by volume; these
cover the ones that are too specific to appear by chance, or whose distances
have to be exact for the test to mean anything.  Both kinds go through the SAME
build-and-diff path as the random programs (tb/cosim/test_cosim_a5.py), so a
directed test cannot accidentally be checked more loosely than a random one.

No expected values live here.  Spike's commit log is the reference, and the
whole log is compared -- every writeback of every instruction, not a final
answer.  What each program has to get right is its hazard DISTANCES, which is
why each case in the .S files names the distance it exercises.

A directed test that does not actually contain the hazard it claims would pass
vacuously, and nothing in this file could tell.  That is what the mutation
harness in tb/mutate/ is for: it breaks the RTL each of these is meant to catch
and requires the matching test to fail.
"""
import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import test_cosim_a5 as a5          # noqa: E402
import test_core_verilator as t4    # noqa: E402

# (name, source) -- name is what the mutation harness and the regression report.
PROGRAMS = [
    ("a6_forward", os.path.join(ROOT, "sw/tests/a6_forward.S")),
    ("a7_loaduse", os.path.join(ROOT, "sw/tests/a7_loaduse.S")),
    ("a8_control", os.path.join(ROOT, "sw/tests/a8_control.S")),
    ("a14_muldiv", os.path.join(ROOT, "sw/tests/a14_muldiv.S")),
]


def compile_s(path, tmp, name):
    elf = os.path.join(tmp, name + ".elf")
    r = subprocess.run(
        # rv32im as of A14: a14_muldiv.S is written in M instructions, and the
        # other three programs assemble identically either way.
        ["riscv-none-elf-gcc", "-march=rv32im_zicsr", "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", t4.LD, "-o", elf, path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError(f"assembling {path} failed")
    return elf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="run just this program by name")
    a = ap.parse_args()

    progs = [p for p in PROGRAMS if a.only in (None, p[0])]
    if not progs:
        print(f"DIRECTED_FAIL: no program named {a.only!r}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")
        exe = a5.build_sim(tmp, image)

        failures = []
        for name, src in progs:
            if not os.path.exists(src):
                print(f"  {name}: MISSING {src}")
                failures.append(name)
                continue
            elf = compile_s(src, tmp, name)
            if not a5.run_one(exe, elf, tmp, image, name, verbose=True):
                failures.append(name)

        if failures:
            print("DIRECTED_FAIL: " + ", ".join(failures))
            return 1
        print(f"DIRECTED_OK  ({len(progs)} program(s))")
        return 0


if __name__ == "__main__":
    sys.exit(main())
