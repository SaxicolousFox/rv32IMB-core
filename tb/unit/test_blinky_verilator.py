#!/usr/bin/env python3
"""Build and run the blinky/UART/BRAM top-level testbench under Verilator."""
import os, subprocess, sys, shutil, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN  = os.path.join(ROOT, "fpga", "generated")

def main() -> int:
    build = os.path.join(tempfile.gettempdir(), "rvntt_obj_blinky")
    os.makedirs(build, exist_ok=True)
    # $readmemh resolves relative to the simulator's cwd.
    shutil.copy(os.path.join(GEN, "bram_init.mem"), build)

    srcs = [os.path.join(ROOT, "rtl/soc", f) for f in
            ("rvntt_blinky_top.sv", "rvntt_clkgen.sv", "rvntt_uart_tx.sv",
             "rvntt_uart_report.sv", "rvntt_bram_selftest.sv")]
    srcs.append(os.path.join(ROOT, "rtl/common/rvntt_sync_reset.sv"))

    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_blinky_top",
           "-I" + os.path.join(ROOT, "rtl/soc"),
           "-I" + os.path.join(ROOT, "rtl/common"),
           "-I" + GEN,
           "--Mdir", build, "--prefix", "Vrvntt_blinky_top",
           os.path.join(ROOT, "tb/unit/tb_blinky_uart.cpp")] + srcs
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return 1

    r = subprocess.run([os.path.join(build, "Vrvntt_blinky_top")],
                       cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    return 0 if (r.returncode == 0 and "BLINKY_UART_TB_OK" in out) else 1

if __name__ == "__main__":
    sys.exit(main())
