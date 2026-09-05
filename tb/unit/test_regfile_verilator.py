#!/usr/bin/env python3
"""Build and run the rvntt_regfile Verilator testbench. Exits nonzero on failure."""
import os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RTL  = os.path.join(ROOT, "rtl/core/rvntt_regfile.sv")
TB   = os.path.join(ROOT, "tb/unit/tb_regfile.cpp")

def main():
    build = os.path.join(tempfile.gettempdir(), "rvntt_obj_regfile")
    cmd = ["verilator", "--cc", RTL, "--exe", TB, "--build", "-j", "4",
           "-Wall", "--Mdir", build, "--prefix", "Vrvntt_regfile"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return 1
    exe = os.path.join(build, "Vrvntt_regfile")
    r = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    return 0 if (r.returncode == 0 and "REGFILE_TB_OK" in out) else 1

if __name__ == "__main__":
    sys.exit(main())
