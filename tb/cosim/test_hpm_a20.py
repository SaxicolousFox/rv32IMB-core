#!/usr/bin/env python3
"""
Directed Zihpm hardware performance counter tests.

Runs sw/tests/a20_hpm.S on the RTL under Verilator.  Self-checking through
riscv-tests' tohost protocol: it checks counter values, which Spike has no
model of.  This covers the CSR-level contract -- the registers exist, are
WARL where the spec says, read zero for the unimplemented indices without
trapping, and mcountinhibit inhibits.  Event attribution is checked by
tb/perf/run_stall_profile.py against the simulation instrument.
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

# The case numbers in a20_hpm.S, so a failure names the property rather than a
# number.  tohost on failure is (case << 1) | 1.
CASES = {
    2:  "the six implemented counters are readable and writable",
    3:  "mhpmcounter9..31 are decoded, read zero, and do not trap",
    4:  "mcountinhibit is WARL and its TM bit reads back zero",
    5:  "mcountinhibit actually inhibits",
    6:  "a programmed event actually counts",
    7:  "event 0 counts nothing",
    8:  "mhpmevent is WARL over 0..HPM_EV_MAX",
    9:  "the counter halves are independent and carry",
    10: "the user-mode shadows read through and are read-only",
}


def compile_s(path, tmp, name):
    elf = os.path.join(tmp, name + ".elf")
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=" + spike_asm.MARCH, "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-T", t4.LD, "-o", elf, path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError(f"assembling {path} failed")
    return elf


def explain(out):
    """Turn the simulator's tohost value back into the case that failed."""
    for tok in out.replace(",", " ").replace("=", " ").split():
        try:
            v = int(tok, 0)
        except ValueError:
            continue
        if v > 1 and (v & 1):
            case = (v & 0xFF) >> 1
            if case in CASES:
                extra = " (wrong mcause)" if v & 0x100 else ""
                return "case %d failed%s: %s" % (case, extra, CASES[case])
    return None


def run(exe, elf, tmp, image):
    hexf, _n = t4.elf_to_hex(elf, tmp)
    shutil.copy(hexf, image)
    tohost = spike_asm.symbol(elf, "tohost")
    r = subprocess.run([exe, "--tohost", "0x%08x" % tohost,
                        "--expect-tohost", "1",
                        "--max-cycles", "2000000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return (r.returncode == 0), r.stdout.decode("utf-8", "replace").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")
        exe = rvt.build_sim(tmp, image)

        elf = compile_s(os.path.join(ROOT, "sw/tests/a20_hpm.S"), tmp, "a20_hpm")
        ok, out = run(exe, elf, tmp, image)
        print("  a20_hpm     %s  (%d cases)" % ("pass" if ok else "FAIL", len(CASES)))
        if not ok:
            print("    " + out.replace("\n", "\n    "))
            why = explain(out)
            if why:
                print("    " + why)
            print("HPM_A20_FAIL")
            return 1
        if a.verbose:
            print("    " + out.replace("\n", "\n    "))

    print("HPM_A20_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
