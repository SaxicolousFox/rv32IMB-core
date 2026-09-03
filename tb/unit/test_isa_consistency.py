#!/usr/bin/env python3
"""
H1 (MODS_A2): the ISA string, in every place it is written, must agree.

WHY THIS EXISTS.  `CLAUDE.md` says "THE ISA STRING AND THE misa RESET VALUE
MUST MOVE TOGETHER", and until now exactly one pair was checked: run_riscof.py
compares its YAML against rvntt_csr.sv before every compliance run.  The string
is written in SIX places, and the other four were on the honour system:

    rtl/core/rvntt_csr.sv                MISA_VALUE
    tb/riscof/rvntt/rvntt_isa.yaml       ISA:, misa reset-val, misa bitmask
    tb/cosim/spike_asm.py                MARCH, ISA_BASE, ISA_XKNTT
    tb/cosim/test_riscv_tests.py         ISA
    fpga/scripts/build_bench_image.py    ARCH_ALIASES["rv32imb"]

A21 moved all six at once and every one of them had to be found by hand.  What
happens when one is missed is not a failure -- it is a SILENT NARROWING: Spike
is told a smaller ISA than the DUT, takes an illegal-instruction trap on the
first instruction the DUT can execute and it cannot, and SPINS.  That has
happened twice in this project (A14 with `mul`, A21 with `clz`), both times in
riscof_spike_ref.py, both times costing a run that reported nothing at all.

This check costs about a tenth of a second and is registered in the regression
next to `mutation_anchors`, which exists for the same reason: a cheap pre-flight
that fails in seconds beats an expensive run that hangs.

WHAT IT DOES NOT CHECK.  That the ISA string is CORRECT -- that is RISCOF's job,
and riscv-config's, and the decoder equivalence sweep's.  This checks only that
the six copies say the same thing, which is the failure mode that has actually
occurred.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Extensions a given source is ALLOWED to carry beyond the common core, with
# the reason.  Anything else present in one source and absent from another is
# a disagreement.
ALLOWED_EXTRA = {
    "spike_asm.ISA_BASE":   {"zicntr"},
    "spike_asm.ISA_XKNTT":  {"zicntr", "xkntt0p1"},
    "test_riscv_tests.ISA": {"zicntr"},
    # The image's -march never names zicntr: nothing in the benchmark reads the
    # user-mode shadows, and adding it would change no code generation.
    "build_bench_image.rv32imb": set(),
    "spike_asm.MARCH":      set(),
    "rvntt_isa.yaml":       set(),
}


def exts(s):
    """An ISA string -> the set of extensions it names, lower-cased.

    Handles both spellings this project uses: gcc/Spike's
    `rv32im_zba_zbb...` and riscv-config's `RV32IMZicsr_Zicond_Zba...`, where
    the single letters run together and the Z groups may or may not be
    underscore-separated.
    """
    s = s.strip().lower()
    m = re.match(r"^rv(?:32|64)([a-wy]*)(.*)$", s)
    if not m:
        raise SystemExit("ISA_FAIL: cannot parse ISA string %r" % s)
    out = set(m.group(1))
    for z in re.findall(r"[zx][a-z0-9]+", m.group(2)):
        out.add(z)
    return out


def read(path):
    return io.open(os.path.join(ROOT, path), encoding="utf-8").read()


def grab(path, pattern, what):
    m = re.search(pattern, read(path), re.M)
    if not m:
        raise SystemExit("ISA_FAIL: no %s found in %s -- the check cannot be "
                         "vacuous, so this is a failure, not a skip"
                         % (what, path))
    return m.group(1)


def main():
    found = {}
    found["spike_asm.MARCH"] = grab(
        "tb/cosim/spike_asm.py", r'^MARCH\s*=\s*"([^"]+)"', "MARCH")
    found["spike_asm.ISA_BASE"] = grab(
        "tb/cosim/spike_asm.py", r'^ISA_BASE\s*=\s*"([^"]+)"', "ISA_BASE")
    found["spike_asm.ISA_XKNTT"] = grab(
        "tb/cosim/spike_asm.py", r'^ISA_XKNTT\s*=\s*"([^"]+)"', "ISA_XKNTT")
    found["test_riscv_tests.ISA"] = grab(
        "tb/cosim/test_riscv_tests.py", r'^ISA\s*=\s*"([^"]+)"', "ISA")
    found["rvntt_isa.yaml"] = grab(
        "tb/riscof/rvntt/rvntt_isa.yaml", r'^\s*ISA:\s*(\S+)', "ISA:")
    found["build_bench_image.rv32imb"] = grab(
        "fpga/scripts/build_bench_image.py",
        r'"rv32imb":\s*"([^"]+)"', 'ARCH_ALIASES["rv32imb"]')

    # The image's rv32imb alias has _zicsr appended by arch_flags(), so add it
    # here rather than pretending the alias carries it.
    found["build_bench_image.rv32imb"] += "_zicsr"

    sets = {k: exts(v) for k, v in found.items()}
    core = {k: v - ALLOWED_EXTRA[k] for k, v in sets.items()}

    print("the ISA string, in every place it is written:")
    for k in sorted(found):
        print("  %-28s %s" % (k, found[k]))

    ref_name = "rvntt_isa.yaml"
    ref = core[ref_name]
    bad = []
    for k, v in sorted(core.items()):
        if v != ref:
            bad.append((k, sorted(v - ref), sorted(ref - v)))
    # An extension present but NOT declared allowed is also a disagreement.
    for k, v in sorted(sets.items()):
        stray = v - core[k] - ALLOWED_EXTRA[k]
        if stray:
            bad.append((k, sorted(stray), []))

    print("\ncommon core: %s" % " ".join(sorted(ref)))
    if bad:
        print("\nISA_FAIL: %d source(s) disagree with %s" % (len(bad), ref_name))
        for k, extra, missing in bad:
            print("  %-28s extra=%s missing=%s" % (k, extra or "-", missing or "-"))
        print("\nA source naming a SMALLER ISA than the DUT does not fail -- it "
              "hangs, on the first instruction it does not know.  Move all six "
              "together; see this file's header.")
        return 1

    # misa: the RTL constant, the YAML reset value and the YAML bitmask.
    misa_rtl = int(grab("rtl/core/rvntt_csr.sv",
                        r"MISA_VALUE\s*=\s*32'h([0-9A-Fa-f_]+)",
                        "MISA_VALUE").replace("_", ""), 16)
    misa_yaml = int(grab("tb/riscof/rvntt/rvntt_isa.yaml",
                         r"reset-val:\s*(0x[0-9A-Fa-f]+)", "misa reset-val"), 16)
    mask_yaml = int(grab("tb/riscof/rvntt/rvntt_isa.yaml",
                         r"bitmask\s*\[(0x[0-9A-Fa-f]+)", "misa bitmask"), 16)
    print("misa: rtl=0x%08X yaml=0x%08X bitmask=0x%07X" % (misa_rtl, misa_yaml, mask_yaml))
    if misa_rtl != misa_yaml:
        print("ISA_FAIL: rvntt_csr.sv MISA_VALUE 0x%08X != rvntt_isa.yaml "
              "reset-val 0x%08X -- RISCOF would select suites for one machine "
              "and test another" % (misa_rtl, misa_yaml))
        return 1
    if (misa_yaml & 0x03FFFFFF) != mask_yaml:
        print("ISA_FAIL: the yaml's misa bitmask 0x%07X does not match its own "
              "reset-val's extension bits 0x%07X"
              % (mask_yaml, misa_yaml & 0x03FFFFFF))
        return 1

    # misa bit 1 is B, and this core deliberately does NOT set it while
    # implementing B -- riscv-config 3.18.3 cannot express the letter.  The
    # boundary is recorded in rvntt_csr.sv; assert the state it describes so
    # that setting the bit without revisiting that note fails here.
    b_impl = "zba" in ref and "zbb" in ref and "zbs" in ref
    b_bit = bool(misa_rtl & 0x2)
    print("B implemented=%s   misa.B set=%s" % (b_impl, b_bit))
    if b_impl and b_bit:
        print("ISA_FAIL: misa.B is set.  riscv-config 3.18.3 cannot express "
              "the B letter and derives misa from single-letter extensions "
              "only, so this makes the RISCOF config invalid and the whole "
              "compliance run vanishes.  See rvntt_csr.sv's note; if "
              "riscv-config has since gained the letter, update BOTH.")
        return 1

    print("\nISA_OK: %d sources agree" % len(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
