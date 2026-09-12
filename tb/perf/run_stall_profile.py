#!/usr/bin/env python3
"""
The stall-attribution instrument.

Runs the benchmark image on the RTL under Verilator with tb/perf/tb_profile.cpp
watching the core, and reports where every cycle of each timed region went:

    cycles = retired + load-use stalls + multi-cycle EX stalls + 2 x redirects

The residual must be exactly zero.  Regions are located by matching the mcycle
values the benchmark printed against the values tb_profile.cpp recorded at each
`csrr mcycle`.  Three further checks: the RTL's redirect count equals the
mispredicts model/bpred.py predicts over the same trace; the core's Zihpm
counters equal the instrument's counts to the event; and, with --hpm, the
counters as software reads them exceed the instrument's by at most a bounded
snapshot footprint.  --selftest breaks the accounting six ways.
"""
import argparse, csv, json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "model"))
import bpred                                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN  = os.path.join(ROOT, "fpga", "generated")

SOC = ("rvntt_soc_sim_top.sv", "rvntt_soc_top.sv", "rvntt_clkgen.sv",
       "rvntt_uart_tx.sv", "rvntt_uart_rx.sv", "rvntt_mmio.sv", "rvntt_ram.sv")

SIM_CORE_HZ = 4_000_000       # must match rvntt_soc_sim_top's CORE_HZ parameter

# region name -> (cycles key, instret key) in parse_bench_uart.py's JSON
REGIONS = [
    ("dhrystone",  "dhry_stat_cycles",  "dhry_stat_instret"),
    ("coremark",   "cm_cycles",         "cm_instret"),
]

# The core's own Zihpm counters, captured by tb_profile.cpp at each mcycle read.
HPM_FIELDS = ("hpm_loaduse", "hpm_exstall", "hpm_redirect",
              "hpm_mispredict", "hpm_btbhit", "hpm_xfertaken")

FIELDS = ("cycle", "retired", "id_stall", "ex_stall", "redirect", "redirect_raw",
          "br_taken", "br_ntaken", "jal", "jalr")


def build_and_run(a, build):
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
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return None

    if not os.path.exists(os.path.join(GEN, "soc_clk.svh")):
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "fpga/scripts/gen_soc_clk.py"),
                            "--mhz", "75"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace")); return None

    core = [f for f in sorted(os.listdir(os.path.join(ROOT, "rtl/core")))
            if f.endswith(".sv") and f != "rvntt_rvfi.sv"]
    core = ["rv32i_pkg.sv"] + [f for f in core if f != "rv32i_pkg.sv"]
    srcs  = [os.path.join(ROOT, "rtl/core", f) for f in core]
    srcs += [os.path.join(ROOT, "rtl/soc", f) for f in SOC]
    srcs += [os.path.join(ROOT, "rtl/common/rvntt_sync_reset.sv")]

    # --public-flat-rw makes the core's internals reachable from C++ at the cost
    # of optimisation, which is why this is a separate build from bench_sim.
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "-O3", "-CFLAGS", "-O2", "--public-flat-rw",
           "--top-module", "rvntt_soc_sim_top",
           "-I" + os.path.join(ROOT, "rtl/soc"),
           "-I" + os.path.join(ROOT, "rtl/core"),
           "-I" + os.path.join(ROOT, "rtl/common"),
           "-I" + GEN,
           "--Mdir", build, "--prefix", "Vrvntt_soc_sim_top",
           os.path.join(ROOT, "tb/perf/tb_profile.cpp")] + srcs
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace")); return None

    cap  = os.path.join(build, "bench_sim.log")
    prof = os.path.join(build, "prof.csv")
    argv = [os.path.join(build, "Vrvntt_soc_sim_top"),
            "--out", cap, "--prof", prof,
            "--redir-delay", str(a.redir_delay),
            "--max", str(a.max_cycles), "--blocks", "1"]
    # The trace is always produced: check_predictor needs it.
    trace = os.path.abspath(a.trace) if a.trace else os.path.join(build, "xfer.csv")
    argv += ["--trace", trace]
    r = subprocess.run(argv,
                       cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    print(out.strip())
    if r.returncode != 0 or "PROF_TB_OK" not in out:
        return None

    js = os.path.join(build, "bench_sim.json")
    p = subprocess.run([sys.executable,
                        os.path.join(ROOT, "tb/fpga/parse_bench_uart.py"), cap,
                        "--allow-short", "--min-blocks", "1",
                        "--dhry-runs", str(a.dhry_runs),
                        "--iterations", str(a.iterations),
                        "--json", js],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        print(p.stdout.decode("utf-8", "replace")); return None
    return json.load(open(js))["blocks"][0], prof, trace


def load_snaps(path):
    with open(path) as f:
        return [{k: int(v) for k, v in row.items()} for row in csv.DictReader(f)]


def check_mcycle_alignment(snaps):
    """The instrument's cycle count and the core's mcycle must differ by a constant."""
    offs = {(s["mcycle"] - s["cycle"]) & 0xFFFFFFFF for s in snaps}
    return (len(offs) == 1), sorted(offs)[:5]


def find_region(snaps, want_cycles):
    """Locate the pair of mcycle reads whose difference is `want_cycles`."""
    hits = []
    for i in range(len(snaps)):
        for j in range(i + 1, len(snaps)):
            if ((snaps[j]["mcycle"] - snaps[i]["mcycle"]) & 0xFFFFFFFF) == want_cycles:
                hits.append((i, j))
    return hits


def attribute(lo, hi, fault=0):
    d = {k: hi[k] - lo[k] for k in FIELDS}
    flush = 2 * d["redirect"]
    if fault == 1: flush = d["redirect"]                 # forget that a flush costs two
    if fault == 2: d["id_stall"] = 0                     # ignore the load-use interlock
    if fault == 3: d["ex_stall"] = 0                     # ignore the multi-cycle unit
    if fault == 4: d["retired"] += 1                     # off by one instruction
    d["flush"] = flush
    d["residual"] = d["cycle"] - (d["retired"] + d["id_stall"] + d["ex_stall"] + flush)

    # With a predictor, `redirects == taken + JAL + JALR` no longer holds; the
    # classification counts are kept as data and check_predictor replaces the closure.
    d["xfer"] = d["br_taken"] + d["jal"] + d["jalr"]
    d["xfer_residual"] = 0
    d["lo_cycle"], d["hi_cycle"] = lo["cycle"], hi["cycle"]
    return d


# Hardware counter vs instrument counter, required to agree to the event.
# hpm_btbhit has no counterpart in the retired stream and is reported, not checked.
HPM_PAIRS = (
    ("hpm_loaduse",    "id_stall", "load-use interlock cycles"),
    ("hpm_exstall",    "ex_stall", "multi-cycle EX stall cycles"),
    # redirect_raw, not redirect: the instrument delays its redirect count so
    # the flush cost lands in the right region; silicon counts the pulse.
    ("hpm_redirect",   "redirect_raw", "fetch redirects"),
    ("hpm_xfertaken",  "xfer",     "taken control transfers"),
)


def check_hpm(lo, hi, d, fault=0):
    """(label, hardware, instrument) for every pair that DISAGREES."""
    bad = []
    for hk, sk, label in HPM_PAIRS:
        hw = hi[hk] - lo[hk]
        if fault == 6 and hk == "hpm_loaduse":
            hw += 1                                      # off by one event
        if hw != d[sk]:
            bad.append((label, hw, d[sk]))
    return bad


def check_predictor(trace_path, lo, hi, measured_redirects):
    """Drive model/bpred.py over the region's control-transfer trace and compare
    its mispredict count with the RTL's redirect count."""
    warm, seg = [], []
    with open(trace_path) as f:
        for row in csv.DictReader(f):
            c = int(row["cycle"])
            rec = (int(row["fetch"]), int(row["pc"], 16),
                   int(row["insn"], 16), int(row["next_pc"], 16))
            if c <= lo:
                warm.append(rec)
            elif c <= hi:
                seg.append(rec)

    bp = bpred.DelayedBPred()
    for c, pc, insn, nxt in warm:
        taken = nxt != (pc + 4) & 0xFFFFFFFF
        bp.predict(pc, c)
        bp.update(c, pc, insn, taken, nxt if taken else 0)

    mis = bpred.score_trace(bp, seg)["mispredicts"]
    return mis, mis - measured_redirects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--dhry-runs", type=int, default=2000)
    ap.add_argument("--iterations", type=int, default=6)
    ap.add_argument("--max-cycles", type=int, default=400_000_000)
    ap.add_argument("--arch", choices=["rv32i", "rv32im", "rv32imzb", "rv32imb"],
                    default="rv32im")
    ap.add_argument("--hpm", action="store_true",
                    help="build the image so software arms and reads the six "
                         "counters, and compare what it reports against this "
                         "instrument")
    ap.add_argument("--json", default=None)
    ap.add_argument("--redir-delay", type=int, default=3,
                    help="cycles between an ex_redirect pulse and the cycles it "
                         "costs (measured: 3)")
    ap.add_argument("--trace", default=None,
                    help="write the retired control-transfer trace here")
    ap.add_argument("--selftest", action="store_true",
                    help="break the accounting six ways and require each to be caught")
    a = ap.parse_args()

    build = a.build_dir or os.path.join(tempfile.gettempdir(), "rv32imb_core_obj_prof")
    os.makedirs(build, exist_ok=True)

    got = build_and_run(a, build)
    if got is None:
        print("PROFILE_FAIL: the instrumented run did not complete")
        return 1
    blk, prof_path, trace_path = got
    snaps = load_snaps(prof_path)
    print("\n%d mcycle reads observed" % len(snaps))

    ok, offs = check_mcycle_alignment(snaps)
    if not ok:
        print("PROFILE_FAIL: the instrument's cycle count and the core's mcycle "
              "do not differ by a constant (%s ...) -- the instrument is not "
              "counting this machine's cycles" % offs)
        return 1
    print("mcycle - instrument cycle = %d, constant over every read" % offs[0])

    out, problems = {}, []
    for name, ck, ik in REGIONS:
        if ck not in blk or blk[ck] is None:
            continue
        hits = find_region(snaps, blk[ck])
        if len(hits) != 1:
            problems.append("%s: %d mcycle-read pairs match the reported %d "
                            "cycles (want exactly 1)" % (name, len(hits), blk[ck]))
            continue
        i, j = hits[0]
        d = attribute(snaps[i], snaps[j])
        d["reported_cycles"]  = blk[ck]
        d["reported_instret"] = blk.get(ik)
        mis, mis_resid = check_predictor(trace_path, snaps[i]["cycle"],
                                         snaps[j]["cycle"], d["redirect"])
        d["model_mispredicts"] = mis
        d["mispredict_residual"] = mis_resid
        out[name] = d
        if d["residual"] != 0:
            problems.append("%s: residual %+d, must be exactly 0" % (name, d["residual"]))
        if mis_resid != 0:
            problems.append("%s: the RTL redirected %d times, model/bpred.py "
                            "predicts %d mispredicts (%+d) -- the implementation "
                            "and the predictor model disagree"
                            % (name, d["redirect"], mis, mis_resid))
        for hk in HPM_FIELDS:
            d[hk] = snaps[j][hk] - snaps[i][hk]
        for label, hw, sw in check_hpm(snaps[i], snaps[j], d):
            problems.append("%s: the hardware counter for %s says %d, the "
                            "instrument says %d (%+d) -- one of the two "
                            "predicates is wrong"
                            % (name, label, hw, sw, hw - sw))

    if not out:
        problems.append("no region was located at all")

    if out:
        print("\nZihpm counters against this instrument")
        print("%-11s %-28s %12s %12s %7s" %
              ("region", "event", "hardware", "instrument", "agree"))
        print("-" * 74)
        for name, d in out.items():
            for hk, sk, label in HPM_PAIRS:
                print("%-11s %-28s %12d %12d %7s" %
                      (name, label, d[hk], d[sk],
                       "yes" if d[hk] == d[sk] else "NO"))
            print("%-11s %-28s %12d %12s %7s" %
                  (name, "BTB hits (no counterpart)", d["hpm_btbhit"], "-", "-"))

    # The counters as SOFTWARE reads them, through csrr.  The software window
    # contains the timed one, so software reads high by the snapshot code's own
    # footprint -- a constant, hence an ABSOLUTE bound (measured: +6/+0/+16/+23
    # events, independent of region size).  A miscounting predicate would be
    # proportional and shows up in the exact table above instead.
    OVERHEAD_LIMIT = 256              # events, absolute
    if out and blk.get("has_hpm"):
        print("\nthe counters as SOFTWARE reads them, against this instrument")
        print("(software reads high by the snapshot code's own footprint)")
        print("%-11s %-28s %12s %12s %8s %6s" %
              ("region", "event", "software", "instrument", "overhead", "of"))
        print("-" * 82)
        SW = {"dhrystone": "dhry", "coremark": "cm"}
        for name, d in out.items():
            if name not in SW:
                continue
            for hk, sk, label in HPM_PAIRS:
                key = "%s_hpm_%s" % (SW[name], hk[len("hpm_"):])
                if key not in blk:
                    continue
                sw, inst = blk[key], d[sk]
                over = sw - inst
                frac = (over / inst) if inst else 0.0
                print("%-11s %-28s %12d %12d %+8d %5.2f%%" %
                      (name, label, sw, inst, over, 100.0 * frac))
                if over < 0:
                    problems.append("%s: software read %d for %s but the "
                                    "instrument says %d -- the software window "
                                    "must CONTAIN the timed one, so it can only "
                                    "read high" % (name, sw, label, inst))
                elif over > OVERHEAD_LIMIT:
                    problems.append("%s: software reads %+d high for %s, over "
                                    "the %d-event bound -- too large to be the "
                                    "snapshot code's footprint, and a footprint "
                                    "does not grow with the region"
                                    % (name, over, label, OVERHEAD_LIMIT))

    hdr = ("region", "cycles", "retired", "IPC", "ld-use", "muldiv", "flush",
           "resid", "mispred", "model", "br-tk", "br-nt", "jal", "jalr")
    print("\n%-11s %10s %10s %6s %8s %8s %9s %6s %8s %6s %8s %8s %7s %7s" % hdr)
    print("-" * 124)
    for name, d in out.items():
        print("%-11s %10d %10d %6.4f %8d %8d %9d %6d %8d %6d %8d %8d %7d %7d"
              % (name, d["cycle"], d["retired"], d["retired"] / d["cycle"],
                 d["id_stall"], d["ex_stall"], d["flush"], d["residual"],
                 d["redirect"], d["mispredict_residual"], d["br_taken"],
                 d["br_ntaken"], d["jal"], d["jalr"]))
    print("-" * 124)
    for name, d in out.items():
        t = d["cycle"]
        print("%-11s  flushes %5.1f%% of cycles, load-use %4.1f%%, multi-cycle EX %4.1f%%"
              % (name, 100.0 * d["flush"] / t, 100.0 * d["id_stall"] / t,
                 100.0 * d["ex_stall"] / t))

    # ---- fault injection ---------------------------------------------------
    if a.selftest:
        print("\n--- selftest: both checks must catch what they are for ---")
        name = next(iter(out))
        i, j = find_region(snaps, blk[dict((n, c) for n, c, _ in REGIONS)[name]])[0]
        base_mis = out[name]["model_mispredicts"]
        why = {1: "a flush is charged one cycle instead of two",
               2: "load-use interlock cycles are not counted",
               3: "multi-cycle EX stall cycles are not counted",
               4: "the retired count is off by one",
               5: "the redirect count is off by one",
               6: "a hardware counter disagrees by one event"}
        for f in (1, 2, 3, 4, 5, 6):
            d = attribute(snaps[i], snaps[j], fault=f)
            # Fault 5 leaves the cycle identity closing and is visible only to
            # the predictor closure; fault 6 only to the hardware comparison.
            mis_resid = base_mis - (d["redirect"] + (1 if f == 5 else 0))
            hpm_bad = check_hpm(snaps[i], snaps[j], d, fault=f)
            hit = d["residual"] != 0 or mis_resid != 0 or bool(hpm_bad)
            print("  fault %d (%-46s) residual %+8d  mispredict %+6d  hpm %-3s %s"
                  % (f, why[f], d["residual"], mis_resid,
                     "bad" if hpm_bad else "ok",
                     "CAUGHT" if hit else "ESCAPED"))
            if not hit:
                problems.append("selftest fault %d escaped all three checks" % f)

    if a.json:
        json.dump({"regions": out, "block": blk,
                   "mcycle_offset": offs[0], "snaps": len(snaps)},
                  open(a.json, "w"), indent=1)
        print("\nwrote %s" % a.json)

    if problems:
        print("\n=== %d PROBLEM(S) ===" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("\nPROFILE_OK: the accounting closes with residual zero in every region")
    return 0


if __name__ == "__main__":
    sys.exit(main())
