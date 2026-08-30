#!/usr/bin/env python3
"""
Run the riscv-tests ISA suite on the RTL (plan A9).

This is the first EXTERNALLY AUTHORED test suite the core has faced.  Everything
before it -- the random generator, the directed programs, the mutation manifest
-- was written alongside the design, by the same hand, and shares its blind
spots.  riscv-tests does not.

HOW A TEST REPORTS ITS RESULT.  Each program ends by storing to the `tohost`
symbol: 1 for pass, and otherwise `(failing_case << 1) | 1`, so a failure names
the numbered TEST_CASE that broke.  The testbench watches the store bus for a
write to that address, which works because a store is issued from EX and nothing
past EX is ever squashed (rvntt_core's trap invariant).

WHY THE p-ENVIRONMENT NEEDS A9 AT ALL.  Its reset vector is not a formality: it
reads mhartid, writes mtvec, mie, mstatus, mepc and mscratch, and returns to the
test body through MRET.  Every one of those is A9's.  It also deliberately
touches CSRs an M-only core does not have -- satp, pmpaddr0, pmpcfg0, medeleg,
mideleg -- having first pointed mtvec at the label just after each one, so that
an illegal-instruction trap lands on the next line and the test carries on.
Passing therefore requires the traps to be RIGHT, not merely absent.

EVERY TEST IS RUN ON SPIKE FIRST.  A test that does not pass on the reference
model is a broken build or a wrong ISA string, and reporting it as an RTL
failure would send the reader to the wrong place entirely.
"""
import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import spike_asm                   # noqa: E402
import test_core_verilator as t4   # noqa: E402

TESTS_DIR = os.path.join(ROOT, "toolchain/riscv-tests")
ISA = "rv32i_zicsr_zicntr"

# The RV32I user-level suite, minus the two that are outside this core's ISA.
RV32UI = [
    "add", "addi", "and", "andi", "auipc", "beq", "bge", "bgeu", "blt", "bltu",
    "bne", "jal", "jalr", "lb", "lbu", "lh", "lhu", "lui", "lw", "or", "ori",
    "sb", "sh", "simple", "sll", "slli", "slt", "slti", "sltiu", "sltu", "sra",
    "srai", "srl", "srli", "sub", "sw", "xor", "xori", "ld_st", "st_ld",
]

# Machine mode.  See SKIPPED below for the ones deliberately absent.
RV32MI = [
    "csr", "mcsr", "illegal", "ma_fetch", "ma_addr", "scall", "sbreak",
    "shamt", "lw-misaligned", "lh-misaligned", "sh-misaligned",
    "sw-misaligned", "zicntr", "instret_overflow",
]

# Each entry is (suite, name, why).  These are not failures being hidden: each
# names a feature the plan's §1.5 subset explicitly excludes, and a core that
# passed them would be implementing something it deliberately does not have.
SKIPPED = [
    ("rv32ui", "fence_i",
     "Zifencei. The target ISA is rv32i_zicsr_zicntr; FENCE.I is not in it, "
     "and the decoder rejects it by design (rvntt_decode.sv)."),
    ("rv32ui", "ma_data",
     "requires misaligned load/store to SUCCEED. This core traps on them, "
     "which the rv32mi *-misaligned tests check instead."),
    ("rv32mi", "breakpoint",
     "the debug trigger module (tselect/tdata1-3). Plan 1.5 excludes debug."),
    ("rv32mi", "pmpaddr",
     "physical memory protection. Plan 1.5 excludes PMP."),
]


def compile_test(suite, name, tmp):
    src = os.path.join(TESTS_DIR, "isa", suite, name + ".S")
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    elf = os.path.join(tmp, f"{suite}-p-{name}.elf")
    r = subprocess.run(
        ["riscv-none-elf-gcc", "-march=rv32i_zicsr", "-mabi=ilp32",
         "-nostdlib", "-nostartfiles", "-fno-pie",
         "-I", os.path.join(TESTS_DIR, "isa/macros/scalar"),
         "-I", os.path.join(TESTS_DIR, "env/p"),
         "-I", os.path.join(TESTS_DIR, "env"),
         "-T", os.path.join(TESTS_DIR, "env/p/link.ld"),
         "-o", elf, src],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        return None, r.stdout.decode("utf-8", "replace").strip()
    return elf, None


def build_sim(tmp, image_path):
    build = os.path.join(tmp, "obj")
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_core_sim_top",
           "--Mdir", build, "--prefix", "Vrvntt_core_sim_top",
           '-GINIT_FILE="%s"' % image_path,
           "-GWORDS=16384"] + t4.RTL + [t4.TB]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        raise RuntimeError("building the simulator failed")
    return os.path.join(build, "Vrvntt_core_sim_top")


def run_spike(elf):
    """True if the reference model passes the test."""
    r = subprocess.run(["spike", "--isa=" + ISA, elf],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120)
    return r.returncode == 0, r.stdout.decode("utf-8", "replace").strip()


def run_rtl(exe, elf, tmp, image_path):
    import shutil
    hexf, _n = t4.elf_to_hex(elf, tmp)
    shutil.copy(hexf, image_path)
    tohost = spike_asm.symbol(elf, "tohost")
    if tohost is None:
        return False, "no tohost symbol"
    r = subprocess.run([exe, "--tohost", "0x%08x" % tohost,
                        "--expect-tohost", "1", "--max-cycles", "2000000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=300)
    out = r.stdout.decode("utf-8", "replace").strip()
    return (r.returncode == 0 and "CORE_TB_OK" in out), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["all", "rv32ui", "rv32mi"], default="all")
    ap.add_argument("--only", default=None, help="one test by name")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(TESTS_DIR):
        print("RVTESTS_SKIP: toolchain/riscv-tests is not checked out.\n"
              "  git clone --depth 1 --recurse-submodules \\\n"
              "      https://github.com/riscv-software-src/riscv-tests.git \\\n"
              "      toolchain/riscv-tests")
        return 0

    plan = []
    if a.suite in ("all", "rv32ui"):
        plan += [("rv32ui", n) for n in RV32UI]
    if a.suite in ("all", "rv32mi"):
        plan += [("rv32mi", n) for n in RV32MI]
    if a.only:
        plan = [p for p in plan if p[1] == a.only]
        if not plan:
            print(f"no test named {a.only!r}")
            return 2

    failures, spike_failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "image.hex")
        open(image, "w").write("00000000\n")
        exe = build_sim(tmp, image)

        for suite, name in plan:
            label = f"{suite}-p-{name}"
            elf, err = compile_test(suite, name, tmp)
            if elf is None:
                print(f"  {label:<26} BUILD-FAIL")
                print("    " + err.replace("\n", "\n    "))
                failures.append(label)
                continue

            ok_spike, sout = run_spike(elf)
            if not ok_spike:
                # The reference model disagrees, so the RTL result would mean
                # nothing.  Report it as its own category.
                print(f"  {label:<26} SPIKE-FAIL   {sout.splitlines()[-1] if sout else ''}")
                spike_failures.append(label)
                continue

            ok, out = run_rtl(exe, elf, tmp, image)
            if ok:
                if a.verbose:
                    print(f"  {label:<26} pass")
            else:
                print(f"  {label:<26} FAIL")
                print("    " + out.replace("\n", "\n    "))
                failures.append(label)

    print()
    for suite, name, why in SKIPPED:
        print(f"  {suite}-p-{name:<20} SKIPPED  {why}")
    print()
    total = len(plan)
    print(f"riscv-tests: {total - len(failures) - len(spike_failures)}/{total} "
          f"passed, {len(failures)} failed, {len(spike_failures)} not "
          f"reproducible on Spike, {len(SKIPPED)} skipped by design")
    if spike_failures:
        print("SPIKE_DISAGREES: " + ", ".join(spike_failures))
    if failures:
        print("RVTESTS_FAIL: " + ", ".join(failures))
        return 1
    print("RVTESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
