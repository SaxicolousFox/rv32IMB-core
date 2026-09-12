#!/usr/bin/env python3
"""
The ISA string, in every place it is written, must agree.

The executable copies:
    rtl/core/rvntt_csr.sv                MISA_VALUE
    tb/riscof/rvntt/rvntt_isa.yaml       ISA:, misa reset-val, misa bitmask
    tb/cosim/spike_asm.py                MARCH, ISA_BASE
    tb/cosim/test_riscv_tests.py         ISA
    fpga/scripts/build_bench_image.py    ARCH_ALIASES["rv32imb"]

A source naming a SMALLER ISA than the DUT does not fail: Spike traps on the
first instruction it does not know and spins.  The documented copies (DOC_COPIES)
are checked against the same set, ignoring only zicsr/zicntr, so a README that
quotes a stale string fails here rather than misleading whoever builds against
it.  Also checks misa.B is not set (riscv-config cannot express the letter) and
that rvntt_muldiv's MUL_CYCLES parameter is never overridden in rtl/.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Extensions a source may carry beyond the common core.
ALLOWED_EXTRA = {
    "spike_asm.ISA_BASE":   {"zicntr"},
    "test_riscv_tests.ISA": {"zicntr"},
    # The image's -march never names zicntr: nothing in the benchmark reads the
    # user-mode shadows.
    "build_bench_image.rv32imb": set(),
    "spike_asm.MARCH":      set(),
    "rvntt_isa.yaml":       set(),
}

# Files that state the current ISA string in prose or comments.
DOC_COPIES = [
    "README.md",
    "rtl/core/rvntt_decode.sv",
    "model/rv32i_ref.py",
]
DOC_ISA_RE = re.compile(r"\brv32im_zba[a-z0-9_]*\b")
DOC_IGNORE = {"zicsr", "zicntr"}


def exts(s):
    """An ISA string -> the set of extensions it names, lower-cased.  Accepts
    both gcc/Spike's `rv32im_zba_zbb...` and riscv-config's
    `RV32IMZicsr_Zicond_Zba...` spellings."""
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
    found["test_riscv_tests.ISA"] = grab(
        "tb/cosim/test_riscv_tests.py", r'^ISA\s*=\s*"([^"]+)"', "ISA")
    found["rvntt_isa.yaml"] = grab(
        "tb/riscof/rvntt/rvntt_isa.yaml", r'^\s*ISA:\s*(\S+)', "ISA:")
    found["build_bench_image.rv32imb"] = grab(
        "fpga/scripts/build_bench_image.py",
        r'"rv32imb":\s*"([^"]+)"', 'ARCH_ALIASES["rv32imb"]')

    # arch_flags() appends _zicsr to the alias.
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
              "hangs, on the first instruction it does not know.  Move all "
              "copies together; see this file's header.")
        return 1

    # The documented copies, against ISA_BASE.
    want = sets["spike_asm.ISA_BASE"] - DOC_IGNORE
    doc_bad = []
    n_doc = 0
    for path in DOC_COPIES:
        hits = DOC_ISA_RE.findall(read(path))
        if not hits:
            doc_bad.append((path, "no rv32im_zba... string found -- the "
                            "check cannot be vacuous"))
        for s in hits:
            n_doc += 1
            got = exts(s) - DOC_IGNORE
            if got != want:
                doc_bad.append((path, "%s  extra=%s missing=%s" % (
                    s, sorted(got - want) or "-", sorted(want - got) or "-")))
    print("documented copies: %d string(s) in %d file(s)"
          % (n_doc, len(DOC_COPIES)))
    if doc_bad:
        print("\nISA_FAIL: %d documented copy/copies disagree with "
              "spike_asm.ISA_BASE (%s)" % (len(doc_bad), found["spike_asm.ISA_BASE"]))
        for path, why in doc_bad:
            print("  %-26s %s" % (path, why))
        print("\nThese do not hang anything; they mislead whoever builds "
              "against them.  Update the text, not the constant.")
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

    # misa.B is deliberately NOT set while B is implemented: riscv-config
    # 3.18.3 cannot express the letter, and setting it invalidates the RISCOF
    # config.
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

    # The multiply latency is a contract shared by rv32i_pkg, model/rv32i_ref.py
    # and tb/cosim/cycle_model.py.  rvntt_muldiv's MUL_CYCLES parameter exists
    # only for out-of-context sweeps: its default must be the package constant
    # and nothing in rtl/ may override it.
    muldiv = read("rtl/core/rvntt_muldiv.sv")
    if not re.search(r"parameter\s+int\s+MUL_CYCLES\s*=\s*"
                     r"rv32i_pkg::MULDIV_MUL_CYCLES", muldiv):
        print("ISA_FAIL: rvntt_muldiv's MUL_CYCLES parameter no longer defaults "
              "to rv32i_pkg::MULDIV_MUL_CYCLES.  The package is the contract "
              "model/rv32i_ref.py and tb/cosim/cycle_model.py both read.")
        return 1
    overrides = []
    for d, _, files in os.walk(os.path.join(ROOT, "rtl")):
        for f in sorted(files):
            if not f.endswith(".sv"):
                continue
            path = os.path.join(d, f)
            body = io.open(path, encoding="utf-8").read()
            if re.search(r"\.\s*MUL_CYCLES\s*\(", body):
                overrides.append(os.path.relpath(path, ROOT))
    print("MUL_CYCLES: default from package, %d override(s) in rtl/"
          % len(overrides))
    if overrides:
        print("ISA_FAIL: MUL_CYCLES is overridden at instantiation in %s.  The "
              "RTL would then retire MUL at a different cycle from the one "
              "rv32i_pkg declares, and cycle_model.py predicts spans from the "
              "package.  Change rv32i_pkg (and model/rv32i_ref.py) instead."
              % ", ".join(overrides))
        return 1

    print("\nISA_OK: %d sources and %d documented copies agree"
          % (len(found), n_doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
