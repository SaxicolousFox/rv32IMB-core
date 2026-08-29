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
INC  = ["-I" + os.path.join(ROOT, d)
        for d in ("rtl/common", "rtl/core", "rtl/ntt", "rtl/soc", "fpga/generated")]

# Files that cannot be linted standalone because they need an include that only
# makes sense once elaborated (e.g. the generated bram_expected.svh).
SKIP_STANDALONE = {"rvntt_blinky_top.sv"}

# Narrowly-scoped per-file waivers for the STANDALONE pass only.  Each one needs
# a reason; a bare skip would be worse, because it would silence every other
# check on the file too.
STANDALONE_WAIVERS = {
    # A package is a library: by construction its members are consumed by OTHER
    # files, so linting it alone reports every localparam as unused.  That is a
    # property of linting a package in isolation, not a defect.  Every other
    # -Wall check (widths, enums, syntax) stays enabled here, and the constants
    # are covered for real by the elaborated-design pass below once a decoder
    # consumes them.
    "rv32i_pkg.sv": ["-Wno-UNUSEDPARAM"],
}

DESIGNS = [
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
        waiv = STANDALONE_WAIVERS.get(os.path.basename(f), [])
        rc, out = run(["verilator", "--lint-only", "-Wall"] + waiv + INC + [f])
        if rc != 0:
            fails.append(os.path.relpath(f, ROOT))
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
    print(f"LINT_OK ({n} file(s) standalone + {len(DESIGNS)} elaborated design(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
