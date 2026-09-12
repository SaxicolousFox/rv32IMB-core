#!/usr/bin/env python3
"""
Directed CSR, trap and minstret tests.

`a9_csr.S` is self-checking through riscv-tests' tohost protocol: half of
what it checks cannot agree with Spike, whose mcycle advances once per
instruction and whose minstret counts the five bootrom instructions this core
never executes.

`a9_minstret.S` compares minstret after a known program against Spike's
commit count at or above the load address (not Spike's own minstret, for the
bootrom reason).  The body is a loop, so a counter wired to the fetch stream
would fail here.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import spike_asm                   # noqa: E402
import test_core_verilator as t4   # noqa: E402
import test_riscv_tests as rvt     # noqa: E402

BASE = 0x80000000


def compile_s(path, tmp, name):
    elf = os.path.join(tmp, name + ".elf")
    r = subprocess.run(
        # a9_minstret.S puts a multiply and a divide in its loop so that
        # minstret has to count a multi-cycle instruction once.
        ["riscv-none-elf-gcc", "-march=" + spike_asm.MARCH, "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", t4.LD, "-o", elf, path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError(f"assembling {path} failed")
    return elf


def spike_commits_before(elf, symbol):
    """How many instructions at or above BASE Spike commits before `symbol`."""
    target = spike_asm.symbol(elf, symbol)
    if target is None:
        raise RuntimeError(f"no {symbol} symbol in {elf}")
    _rc, trace, text = spike_asm.run(elf, isa=rvt.ISA, log_commits=True)
    if not trace:
        raise RuntimeError("Spike produced no commit trace:\n" + text[-2000:])
    n = 0
    for pc, _insn, _writes in trace:
        if pc == target:
            return n
        if pc >= BASE:
            n += 1
    raise RuntimeError(f"Spike never reached {symbol}")


def run(exe, elf, tmp, image, extra):
    hexf, _n = t4.elf_to_hex(elf, tmp)
    shutil.copy(hexf, image)
    tohost = spike_asm.symbol(elf, "tohost")
    r = subprocess.run([exe, "--tohost", "0x%08x" % tohost,
                        "--expect-tohost", "1",
                        "--max-cycles", "2000000"] + extra,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return (r.returncode == 0), r.stdout.decode("utf-8", "replace").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.parse_args()

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")
        exe = rvt.build_sim(tmp, image)

        # ---- 1. the directed CSR / trap program -------------------------
        elf = compile_s(os.path.join(ROOT, "sw/tests/a9_csr.S"), tmp, "a9_csr")
        ok, out = run(exe, elf, tmp, image, [])
        print(f"  a9_csr      {'pass' if ok else 'FAIL'}")
        if not ok:
            print("    " + out.replace("\n", "\n    "))
            failures.append("a9_csr")

        # ---- 2. minstret against Spike's instruction count --------------
        elf = compile_s(os.path.join(ROOT, "sw/tests/a9_minstret.S"),
                        tmp, "a9_minstret")
        expect = spike_commits_before(elf, "probe")
        ok, out = run(exe, elf, tmp, image,
                      ["--expect", str(expect), "--reg", "9"])
        print(f"  a9_minstret {'pass' if ok else 'FAIL'}  "
              f"(spike counted {expect} instructions before the probe)")
        if not ok:
            print("    " + out.replace("\n", "\n    "))
            print("    minstret disagrees with Spike's instruction count. "
                  "Every IPC number downstream depends on this.")
            failures.append("a9_minstret")

    if failures:
        print("CSR_TRAPS_FAIL: " + ", ".join(failures))
        return 1
    print("CSR_TRAPS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
