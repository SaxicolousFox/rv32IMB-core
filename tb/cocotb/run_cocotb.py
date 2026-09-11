#!/usr/bin/env python3
"""
Run the cocotb testbenches under Verilator. Exits nonzero on failure.

One design per invocation, selected with --design, so the regression table shows
each as its own row: a single lumped "cocotb" entry would hide which testbench
broke, and would make a slow test slow down every other one's feedback loop.

Sources are listed package-first.  Verilator can auto-find a package from an
include directory but appends it AFTER the file that imports it, which fails for
any module whose port list uses a package type.
"""
from __future__ import annotations
import argparse, sys, tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "rtl/core/rv32i_pkg.sv"

DESIGNS = {
    "sync_reset": dict(
        sources=[ROOT / "rtl/common/rvntt_sync_reset.sv"],
        toplevel="rvntt_sync_reset",
        module="test_sync_reset_cocotb",
        build_args=["--trace"],
    ),
    # No --trace on these two: they push hundreds of thousands of vectors
    # through a combinational block, and a VCD of that is gigabytes of writes
    # for no diagnostic value.  A failure here is reproduced from the printed
    # operands, not from a waveform.
    "alu": dict(
        sources=[PKG, ROOT / "rtl/core/rvntt_alu.sv"],
        toplevel="rvntt_alu",
        module="test_alu_cocotb",
        build_args=[],
    ),
    "immgen": dict(
        sources=[PKG, ROOT / "rtl/core/rvntt_immgen.sv"],
        toplevel="rvntt_immgen",
        module="test_immgen_cocotb",
        build_args=[],
    ),
    # The toplevel is a testbench-only wrapper that flattens ctrl_t into scalar
    # ports, because Verilator gives cocotb no member access into a packed
    # struct.  -Wall so the wrapper is linted here -- it lives under tb/ and so
    # is not covered by tb/lint_all.py, which walks rtl/ only.
    "decode": dict(
        sources=[PKG, ROOT / "rtl/core/rvntt_decode.sv",
                 Path(__file__).resolve().parent / "rvntt_decode_flat.sv"],
        toplevel="rvntt_decode_flat",
        module="test_decode_cocotb",
        build_args=["-Wall"],
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="sync_reset", choices=sorted(DESIGNS))
    # A21.  The mutation harness runs this against a MIRRORED, deliberately
    # broken copy of the RTL tree, the same way run_riscof.py and the formal
    # runner already do.  Without it the decoder equivalence sweep would always
    # read the pristine sources and report a pass over a mutated core -- which
    # is the failure this project keeps finding in other harnesses.
    ap.add_argument("--rtl-dir", default=None,
                    help="read RTL from this mirrored tree instead of ROOT")
    a = ap.parse_args()

    d = dict(DESIGNS[a.design])
    if a.rtl_dir:
        # ONLY the rtl/ sources are remapped.  The mutation harness mirrors
        # rtl/ and nothing else, so tb/cocotb/rvntt_decode_flat.sv -- a
        # testbench wrapper, never mutated -- keeps coming from the real tree.
        # Remapping it too would look for a file that is not there and turn a
        # working check into a build error, which reads as a caught mutation
        # and is not one.
        mirror = Path(a.rtl_dir)
        def remap(src):
            rel = Path(src).resolve().relative_to(ROOT)
            return (mirror / rel) if rel.parts[0] == "rtl" else Path(src)
        d["sources"] = [remap(src) for src in d["sources"]]
        for src in d["sources"]:
            if not Path(src).exists():
                raise SystemExit("COCOTB_FAIL: %s does not exist in the "
                                 "mirrored tree -- reading the pristine source "
                                 "instead would make this test meaningless"
                                 % src)
    # Keep mutated builds out of the pristine build cache.
    suffix = "_mut" if a.rtl_dir else ""
    build_dir = Path(tempfile.gettempdir()) / f"rv32imb_core_cocotb_{a.design}{suffix}"

    runner = get_runner("verilator")
    runner.build(
        verilog_sources=d["sources"],
        hdl_toplevel=d["toplevel"],
        build_dir=build_dir,
        build_args=d["build_args"],
        always=True,
    )
    results = runner.test(
        hdl_toplevel=d["toplevel"],
        test_module=d["module"],
        test_dir=Path(__file__).parent,
        build_dir=build_dir,
    )

    # ---- THE RESULT IS READ.  IT USED NOT TO BE. ---------------------------
    #
    # This function ended in a bare `return 0` from A1 until A21 (MODS_A2)
    # found it: cocotb_tools' runner.test() runs the tests, writes results.xml
    # and RETURNS NORMALLY whether they passed or failed, so every cocotb test
    # in this project reported PASS unconditionally.  It was found because a
    # deliberately-broken wrapper produced "TESTS=6 PASS=1 FAIL=5" on stdout
    # and a green row in the regression table on the same run.
    #
    # SIXTH TIME THIS SHAPE HAS APPEARED HERE -- after A10's RISCOF exit code,
    # A11's sby exit code, A14's synth_ooc.sh DSP=0, A19's stale bench_hardware
    # fixture and A20's stale mutation anchors.  Every one of them was a report
    # whose green was not about the thing it named.  The lesson is the same each
    # time and is worth writing at the point of the fix: A TOOL'S EXIT CODE IS
    # NOT ITS VERDICT UNLESS YOU HAVE CHECKED THAT IT IS.
    #
    # results.xml is parsed rather than trusted to a return value, and the
    # absence of the file is itself a failure -- "no results" and "no failures"
    # must never be the same outcome, which is precisely the bug being fixed.
    xml = Path(results) if results else (build_dir / "results.xml")
    if not xml.exists():
        print(f"COCOTB_FAIL: {xml} was not written -- the tests did not run, "
              f"which is NOT the same as their having passed")
        return 1

    tree = ET.parse(xml)
    ncase = nbad = 0
    for case in tree.iter("testcase"):
        ncase += 1
        bad = [e for e in case if e.tag in ("failure", "error")]
        if bad:
            nbad += 1
            print("COCOTB_FAIL: %s.%s -- %s"
                  % (case.get("classname"), case.get("name"),
                     (bad[0].get("message") or "")[:200]))

    # A results.xml with no testcases in it is the vacuous pass this whole
    # block exists to prevent.
    if ncase == 0:
        print(f"COCOTB_FAIL: {xml} contains no testcases at all")
        return 1
    if nbad:
        print("COCOTB_FAIL: %d of %d cocotb tests failed" % (nbad, ncase))
        return 1
    print("COCOTB_OK: %d test(s) passed" % ncase)
    return 0


if __name__ == "__main__":
    sys.exit(main())
