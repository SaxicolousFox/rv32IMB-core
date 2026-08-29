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
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="sync_reset", choices=sorted(DESIGNS))
    a = ap.parse_args()

    d = DESIGNS[a.design]
    build_dir = Path(tempfile.gettempdir()) / f"rvntt_cocotb_{a.design}"

    runner = get_runner("verilator")
    runner.build(
        verilog_sources=d["sources"],
        hdl_toplevel=d["toplevel"],
        build_dir=build_dir,
        build_args=d["build_args"],
        always=True,
    )
    runner.test(
        hdl_toplevel=d["toplevel"],
        test_module=d["module"],
        test_dir=Path(__file__).parent,
        build_dir=build_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
