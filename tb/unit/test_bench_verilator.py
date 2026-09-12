#!/usr/bin/env python3
"""
Run the benchmark image on the SoC RTL under Verilator.

A functional check with small iteration counts (the parser runs with
--allow-short); the measurement is the hardware run.  Also cross-checks mcycle
against Verilator's own clock-edge count, which nothing else does.
"""
import argparse, json, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN  = os.path.join(ROOT, "fpga", "generated")

SOC = ("rvntt_soc_sim_top.sv", "rvntt_soc_top.sv", "rvntt_clkgen.sv",
       "rvntt_uart_tx.sv", "rvntt_uart_rx.sv", "rvntt_mmio.sv", "rvntt_ram.sv")

SIM_CORE_HZ = 4_000_000       # must match rvntt_soc_sim_top's CORE_HZ parameter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtl-dir", default=None)
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--dhry-runs", type=int, default=30)
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--blocks", type=int, default=1)
    ap.add_argument("--max-cycles", type=int, default=400_000_000)
    ap.add_argument("--arch", choices=["rv32i", "rv32im", "rv32imzb", "rv32imb"],
                    default="rv32i")
    ap.add_argument("--hpm", action="store_true")
    # Cycle counts may differ between blocks (the predictor carries state);
    # architectural keys must still be exactly equal.
    ap.add_argument("--tolerance-ppm", type=float, default=50.0)
    a = ap.parse_args()
    base = a.rtl_dir or ROOT

    build = a.build_dir or os.path.join(tempfile.gettempdir(), "rv32imb_core_obj_bench")
    os.makedirs(build, exist_ok=True)

    # rvntt_soc_sim_top hard-codes INIT_FILE("soc_sim.mem"); the simulator runs
    # with cwd=build, so the image takes that name in its own build directory.
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "fpga/scripts/build_bench_image.py"),
                        "--out", os.path.join(build, "soc_sim.mem"),
                        "--elf", os.path.join(build, "bench_sim.elf"),
                        "--core-hz", str(SIM_CORE_HZ),
                        "--dhry-runs", str(a.dhry_runs),
                        "--iterations", str(a.iterations),
                        "--gap-cycles", "2000",
                        "--arch", a.arch]
                       + (["--hpm"] if a.hpm else []),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(r.stdout.decode("utf-8", "replace").strip())
    if r.returncode != 0:
        return 1

    clk_svh = os.path.join(GEN, "soc_clk.svh")
    if not os.path.exists(clk_svh):
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "fpga/scripts/gen_soc_clk.py"),
                            "--mhz", "75"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace")); return 1

    core = [f for f in sorted(os.listdir(os.path.join(ROOT, "rtl/core")))
            if f.endswith(".sv") and f != "rvntt_rvfi.sv"]
    core = ["rv32i_pkg.sv"] + [f for f in core if f != "rv32i_pkg.sv"]
    srcs = [os.path.join(base, "rtl/core", f) for f in core]
    srcs += [os.path.join(base, "rtl/soc", f) for f in SOC]
    srcs += [os.path.join(base, "rtl/common/rvntt_sync_reset.sv")]

    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "-O3", "-CFLAGS", "-O2",
           "--top-module", "rvntt_soc_sim_top",
           "-I" + os.path.join(base, "rtl/soc"),
           "-I" + os.path.join(base, "rtl/core"),
           "-I" + os.path.join(base, "rtl/common"),
           "-I" + GEN,
           "--Mdir", build, "--prefix", "Vrvntt_soc_sim_top",
           os.path.join(ROOT, "tb/unit/tb_bench.cpp")] + srcs
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return 1

    cap = os.path.join(build, "bench_sim.log")
    r = subprocess.run([os.path.join(build, "Vrvntt_soc_sim_top"),
                        "--out", cap, "--max", str(a.max_cycles),
                        "--blocks", str(a.blocks)],
                       cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    if r.returncode != 0 or "BENCH_TB_OK" not in out:
        sys.stdout.write(open(cap, errors="replace").read()[-2000:]
                         if os.path.exists(cap) else "(no capture)\n")
        return 1

    js = os.path.join(build, "bench_sim.json")
    p = subprocess.run([sys.executable,
                        os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"), cap,
                        "--allow-short", "--min-blocks", str(a.blocks),
                        # Block 1 runs on a cold predictor; discard it.
                        "--warmup-blocks", "1",
                        "--tolerance-ppm", str(a.tolerance_ppm),
                        "--dhry-runs", str(a.dhry_runs),
                        "--iterations", str(a.iterations),
                        "--json", js],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(p.stdout.decode("utf-8", "replace").strip())
    if p.returncode != 0:
        return 1

    # Is mcycle a real cycle counter?  Verilator counted the clock edges itself;
    # the timed regions plus the computable UART time must account for most of
    # the simulated run.
    m = re.search(r"^cycles=(\d+) bytes=(\d+)", out, re.M)
    if not m:
        print("BENCH_FAIL: the testbench printed no cycle count")
        return 1
    tb_cycles, uart_bytes = int(m.group(1)), int(m.group(2))
    with open(js) as f:
        blocks = json.load(f)["blocks"]
    # Every timed region must be listed here, or the accounted fraction drops
    # and reads exactly like "mcycle counts slower than the clock".
    REGION_CYCLE_KEYS = ("dhry_cycles", "cm_cycles")
    measured = sum(sum(b.get(k, 0) for k in REGION_CYCLE_KEYS) for b in blocks)
    # 34 cycles per bit, 10 bits per byte, at CORE_HZ/BAUD for the sim clock.
    uart_cycles = uart_bytes * 10 * (SIM_CORE_HZ // 115200)
    print("mcycle cross-check   : %d measured + %d UART = %d of %d simulated "
          "(%.1f%%)" % (measured, uart_cycles, measured + uart_cycles, tb_cycles,
                        100.0 * (measured + uart_cycles) / tb_cycles))
    if measured >= tb_cycles:
        print("BENCH_FAIL: the timed regions total %d cycles but the simulation "
              "only ran %d -- mcycle counts faster than the clock"
              % (measured, tb_cycles))
        return 1
    if measured + uart_cycles < 0.85 * tb_cycles:
        print("BENCH_FAIL: timed regions plus UART account for only %.1f%% of "
              "the simulated cycles -- mcycle counts slower than the clock"
              % (100.0 * (measured + uart_cycles) / tb_cycles))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
