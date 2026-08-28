#!/usr/bin/env python3
"""Run the cocotb testbenches under Verilator. Exits nonzero on failure."""
import os, sys, tempfile
from pathlib import Path
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[2]

def main() -> int:
    runner = get_runner("verilator")
    runner.build(
        verilog_sources=[ROOT / "rtl/common/rvntt_sync_reset.sv"],
        hdl_toplevel="rvntt_sync_reset",
        build_dir=Path(tempfile.gettempdir()) / "rvntt_cocotb_build",
        build_args=["--trace"],
        always=True,
    )
    runner.test(
        hdl_toplevel="rvntt_sync_reset",
        test_module="test_sync_reset_cocotb",
        test_dir=Path(__file__).parent,
        build_dir=Path(tempfile.gettempdir()) / "rvntt_cocotb_build",
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
