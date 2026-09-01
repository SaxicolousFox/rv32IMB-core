#!/usr/bin/env python3
"""
Verilator lint over the whole RTL tree.

Two passes, because they catch different things:
  1. Each file standalone -- catches unused signals, width bugs and bad style in
     modules that nothing instantiates yet.
  2. Each complete design elaborated from its top -- catches port mismatches,
     parameter width problems and cross-module issues that per-file linting
     cannot see.
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tb"))
from rtl_deps import with_deps   # noqa: E402
INC  = ["-I" + os.path.join(ROOT, d)
        for d in ("rtl/common", "rtl/core", "rtl/ntt", "rtl/soc", "fpga/generated")]

# Files that cannot be linted standalone because they need an include that only
# makes sense once elaborated (e.g. the generated bram_expected.svh).
SKIP_STANDALONE = {"rvntt_blinky_top.sv"}


# Code behind an `ifdef is code, and lint has to see it.  rvntt_core's RVFI port
# and rvntt_rvfi.sv are only compiled when RISCV_FORMAL is set -- by
# riscv-formal's generated .sby files, never by the simulation build -- so
# without a pass that sets it, the one file whose whole job is verification
# would be the one file nothing lints.  A5's own experience applies: an
# unexercised checking mechanism rots silently.
DEFINE_PASSES = [
    ("RISCV_FORMAL", ["rtl/core/rvntt_rvfi.sv", "rtl/core/rvntt_core.sv"]),
]

DESIGNS = [
    ("rvntt_soc_sim_top", [
        "rtl/soc/rvntt_soc_sim_top.sv", "rtl/soc/rvntt_soc_top.sv",
        "rtl/soc/rvntt_clkgen.sv", "rtl/soc/rvntt_uart_tx.sv",
        "rtl/soc/rvntt_uart_rx.sv", "rtl/soc/rvntt_mmio.sv",
        "rtl/soc/rvntt_ram.sv", "rtl/common/rvntt_sync_reset.sv",
        "rtl/core/rv32i_pkg.sv", "rtl/core/rvntt_core.sv",
        "rtl/core/rvntt_alu.sv", "rtl/core/rvntt_branch.sv",
        "rtl/core/rvntt_csr.sv", "rtl/core/rvntt_decode.sv",
        "rtl/core/rvntt_forward.sv", "rtl/core/rvntt_hazard.sv",
        "rtl/core/rvntt_immgen.sv", "rtl/core/rvntt_regfile.sv"]),
    ("rvntt_blinky_top", [
        "rtl/soc/rvntt_blinky_top.sv", "rtl/soc/rvntt_clkgen.sv",
        "rtl/soc/rvntt_uart_tx.sv", "rtl/soc/rvntt_uart_report.sv",
        "rtl/soc/rvntt_bram_selftest.sv", "rtl/common/rvntt_sync_reset.sv"]),
]


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def main() -> int:
    fails = []

    sv = []
    for d, _, files in os.walk(os.path.join(ROOT, "rtl")):
        for f in sorted(files):
            if f.endswith(".sv"):
                sv.append(os.path.join(d, f))

    if not sv:
        print("no RTL found"); return 0

    for f in sorted(sv):
        if os.path.basename(f) in SKIP_STANDALONE:
            continue
        # with_deps() puts any imported package ahead of the file.  Verilator
        # auto-finds packages from -I but appends them AFTER the importer, so a
        # module whose port list uses a package type otherwise fails with
        # "Reference to 'alu_op_e' before declaration".
        rc, out = run(["verilator", "--lint-only", "-Wall"] + INC + with_deps(f))
        if rc != 0:
            fails.append(os.path.relpath(f, ROOT))
            print(out.rstrip()[-1500:])

    for define, files in DEFINE_PASSES:
        for f in files:
            rc, out = run(["verilator", "--lint-only", "-Wall",
                           "+define+" + define] + INC
                          + with_deps(os.path.join(ROOT, f)))
            if rc != 0:
                fails.append(f"{f} (+define+{define})")
                print(out.rstrip()[-1500:])

    for top, files in DESIGNS:
        rc, out = run(["verilator", "--lint-only", "-Wall", "--top-module", top]
                      + INC + [os.path.join(ROOT, f) for f in files])
        if rc != 0:
            fails.append(f"design:{top}")
            print(out.rstrip()[-1500:])

    n = len(sv) - len(SKIP_STANDALONE & {os.path.basename(x) for x in sv})
    if fails:
        print(f"LINT_FAIL: {', '.join(fails)}")
        return 1
    n_def = sum(len(files) for _, files in DEFINE_PASSES)
    print(f"LINT_OK ({n} file(s) standalone + {n_def} with defines "
          f"+ {len(DESIGNS)} elaborated design(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
