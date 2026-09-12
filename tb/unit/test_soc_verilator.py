#!/usr/bin/env python3
"""Build and run the SoC top-level testbench under Verilator."""
import argparse, os, subprocess, sys, shutil, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN  = os.path.join(ROOT, "fpga", "generated")
sys.path.insert(0, os.path.join(ROOT, "tb"))

SOC = ("rvntt_soc_sim_top.sv", "rvntt_soc_top.sv", "rvntt_clkgen.sv",
       "rvntt_uart_tx.sv", "rvntt_uart_rx.sv", "rvntt_mmio.sv", "rvntt_ram.sv")


def main() -> int:
    ap = argparse.ArgumentParser()
    # The mutation harness copies the RTL tree and points this at the copy:
    # one source list joined onto a base directory.
    ap.add_argument("--rtl-dir", default=None)
    ap.add_argument("--build-dir", default=None)
    a = ap.parse_args()
    base = a.rtl_dir or ROOT

    build = a.build_dir or os.path.join(tempfile.gettempdir(), "rv32imb_core_obj_soc")
    os.makedirs(build, exist_ok=True)

    # The simulation image: same C source, short inter-block delay.  Built here
    # rather than checked in so it cannot drift from sw/soc/hello.c.
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "fpga/scripts/build_soc_image.py"),
                        "--out", os.path.join(build, "soc_sim.mem"),
                        "--elf", os.path.join(build, "soc_sim.elf"),
                        "--delay-cycles", "2000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(r.stdout.decode("utf-8", "replace").strip())
    if r.returncode != 0:
        return 1

    # The generated clock header has to exist; it is the same one synthesis uses.
    clk_svh = os.path.join(GEN, "soc_clk.svh")
    if not os.path.exists(clk_svh):
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "fpga/scripts/gen_soc_clk.py"),
                            "--mhz", "75"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace")); return 1

    core  = [f for f in sorted(os.listdir(os.path.join(ROOT, "rtl/core")))
             if f.endswith(".sv") and f != "rvntt_rvfi.sv"]
    core  = ["rv32i_pkg.sv"] + [f for f in core if f != "rv32i_pkg.sv"]
    srcs  = [os.path.join(base, "rtl/core", f) for f in core]
    srcs += [os.path.join(base, "rtl/soc", f) for f in SOC]
    srcs += [os.path.join(base, "rtl/common/rvntt_sync_reset.sv")]

    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_soc_sim_top",
           "-I" + os.path.join(base, "rtl/soc"),
           "-I" + os.path.join(base, "rtl/core"),
           "-I" + os.path.join(base, "rtl/common"),
           "-I" + GEN,
           "--Mdir", build, "--prefix", "Vrvntt_soc_sim_top",
           os.path.join(ROOT, "tb/unit/tb_soc.cpp")] + srcs
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return 1

    r = subprocess.run([os.path.join(build, "Vrvntt_soc_sim_top")],
                       cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    return 0 if (r.returncode == 0 and "SOC_TB_OK" in out) else 1


if __name__ == "__main__":
    sys.exit(main())
