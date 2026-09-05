#!/usr/bin/env python3
"""
A18 -- the stall attribution instrument.

MODS_A A18, in one sentence: you cannot honestly report a predictor's payoff
without an instrument that measured the baseline first.  This is that
instrument.  It runs the benchmark image on the RTL under Verilator with
tb/perf/tb_profile.cpp watching the core, and reports where every cycle of each
timed region went:

    cycles = retired + load-use stalls + multi-cycle EX stalls + 2 x redirects

THE RESIDUAL MUST BE EXACTLY ZERO.  If it is not, either the instrument or the
core is wrong and neither number below means anything until you know which.  It
is checked here rather than eyeballed, and --selftest breaks the accounting four
ways to prove the check can fail.

WHAT MAKES THE REGIONS TRUSTWORTHY.  The software reads mcycle at the start and
end of every timed region.  tb_profile.cpp records the value the core returned
at each of those reads together with its own counters, so a region is located by
matching the number the benchmark PRINTED -- not by guessing at a PC, and not by
a marker that would have changed the cycle counts it exists to measure.  The
instrument's own cycle counter and the core's mcycle are then compared: they
must differ by a constant, which is what "the simulation reproduces the board to
the cycle" actually asserts.
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
    ("ntt_rv32i",  "ntt_cycles_rv32i",  "ntt_instret_rv32i"),
    ("ntt_rv32im", "ntt_cycles_rv32im", "ntt_instret_rv32im"),
]

# A20.  The core's own Zihpm counters, captured by tb_profile.cpp at the same
# mcycle reads that bracket each region.  They are compared against this
# instrument's independently-written counters below -- see check_hpm.
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
                        "--arch", a.arch] + (["--ntt"] if a.ntt else [])
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

    # --public-flat-rw is what makes the core's internals reachable from C++.
    # It is also why this is not folded into bench_sim: it costs optimisation.
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
    # The trace is ALWAYS produced, not only when asked for: check_predictor
    # needs it, and a check that depends on an optional flag is a check that is
    # off by default.
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
    """The instrument's cycle counter vs the core's own mcycle.

    They are two independent counts of the same thing, offset by the EX-to-WB
    distance of the csrr that read one of them.  A constant difference is the
    whole of the claim that this instrument counts the machine's cycles; a
    drifting one would mean it does not, and every number after it would be
    quietly wrong rather than obviously wrong.
    """
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

    # THE SECOND CLOSURE, and A19 changed what it has to say.
    #
    # Before the predictor, every taken transfer redirected, so
    # `redirects == taken + JAL + JALR` held exactly and was worth checking:
    # the cycle identity is blind to the CLASSIFICATION -- it would still close
    # if every branch were labelled taken, because it only counts redirects.
    #
    # After the predictor, a correctly predicted taken transfer does not
    # redirect at all, so that identity is simply false, and MODS_A section 3.3
    # said so in advance: "A19 changes flush_penalties from 2 x redirects to
    # 2 x mispredicts, and a mispredict count cannot be derived from the retired
    # stream alone."  Its answer was to model the predictor from its
    # specification, and that is what replaces this check -- see check_predictor
    # below.  The classification counts are kept because they are the data A19's
    # report is built from; they are no longer a closure on their own.
    d["xfer"] = d["br_taken"] + d["jal"] + d["jalr"]
    d["xfer_residual"] = 0
    # The absolute window, so the control-transfer trace can be sliced to this
    # region without re-running the simulation.
    d["lo_cycle"], d["hi_cycle"] = lo["cycle"], hi["cycle"]
    return d


# A20's ACTUAL done-when.  Four independent equalities between the hardware
# counters and this instrument's software ones, over the same region of the same
# run, required to hold TO THE COUNT rather than to a tolerance.
#
# Why to the count and not approximately: the two sides are counting the same
# events by different means -- the hardware from EX-stage predicates, the
# instrument from a cycle-by-cycle mirror of the pipeline's load conditions --
# and any disagreement at all means one of them has the wrong predicate.  The
# most likely such disagreement is the load-use / multi-cycle tie: a cycle where
# both stalls assert is ONE lost cycle, and if the two sides break the tie
# differently they disagree by exactly the number of cycles where a load feeds a
# multiply.  That is a small number that looks like a rounding error.  A
# tolerance would hide it; an exact match cannot.
#
# hpm_btbhit has no counterpart here -- there is nothing in the retired stream
# that says whether the BTB had an entry -- so it is reported, not checked.
HPM_PAIRS = (
    ("hpm_loaduse",    "id_stall", "load-use interlock cycles"),
    ("hpm_exstall",    "ex_stall", "multi-cycle EX stall cycles"),
    # redirect_raw, NOT redirect: the instrument delays its redirect count by
    # redir_delay cycles so the flush cost lands in the right region, and a
    # counter in silicon cannot do that.  See tb_profile.cpp -- the two differ
    # per region by the pulses in flight across a boundary, and comparing
    # against the delayed one would have made this check fail by 1 on Dhrystone
    # and pass on CoreMark, which is exactly what it did the first time it ran.
    ("hpm_redirect",   "redirect_raw", "fetch redirects"),
    ("hpm_xfertaken",  "xfer",     "taken control transfers"),
)


def check_hpm(lo, hi, d, fault=0):
    """Compare the core's counters against the instrument's.  Returns a list of
    (label, hardware, instrument) for every pair that DISAGREES."""
    bad = []
    for hk, sk, label in HPM_PAIRS:
        hw = hi[hk] - lo[hk]
        if fault == 6 and hk == "hpm_loaduse":
            hw += 1                                      # off by one event
        if hw != d[sk]:
            bad.append((label, hw, d[sk]))
    return bad


def check_predictor(trace_path, lo, hi, measured_redirects):
    """The closure that replaces `redirects == taken + JAL + JALR`.

    MODS_A section 3.3's answer to "a mispredict count cannot be derived from
    the retired stream alone" was: model the predictor from its specification
    and simulate it against the dynamic branch stream.  model/bpred.py is that
    model, written from docs/a19-bpred-spec.md and reading nothing out of the
    RTL, and this drives it over the SAME region the counters bracket.

    It is a strictly stronger check than the one it replaces.  The old one
    compared two things the hardware counted; this one compares what the
    hardware counted against what the SPECIFICATION says it should have
    counted, over a real workload with hundreds of thousands of transfers --
    which is the whole benchmark, not the eight loops of a directed test.
    """
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
    ap.add_argument("--ntt", action="store_true")
    ap.add_argument("--hpm", action="store_true",
                    help="A20: build the image so SOFTWARE arms and reads the "
                         "six counters, and compare what it reports against "
                         "this instrument.  A third independent path: the "
                         "counters are then read the way the board reads them.")
    ap.add_argument("--json", default=None)
    ap.add_argument("--redir-delay", type=int, default=3,
                    help="cycles between an ex_redirect pulse and the cycles it "
                         "costs; 3 is measured, see docs/a18-stalls.md")
    ap.add_argument("--trace", default=None,
                    help="write the retired control-transfer trace here, for "
                         "A19's predictor model to be driven by")
    ap.add_argument("--selftest", action="store_true",
                    help="break the accounting four ways and require each to be caught")
    a = ap.parse_args()

    build = a.build_dir or os.path.join(tempfile.gettempdir(), "rvntt_obj_prof")
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
                            "and docs/a19-bpred-spec.md disagree"
                            % (name, d["redirect"], mis, mis_resid))
        # A20's done-when: the core's own counters and this instrument agree.
        for hk in HPM_FIELDS:
            d[hk] = snaps[j][hk] - snaps[i][hk]
        for label, hw, sw in check_hpm(snaps[i], snaps[j], d):
            problems.append("%s: the hardware counter for %s says %d, the "
                            "instrument says %d (%+d) -- one of the two "
                            "predicates is wrong"
                            % (name, label, hw, sw, hw - sw))

    if not out:
        problems.append("no region was located at all")

    # A20.  Printed as its own table rather than as more columns on the one
    # below, because the point is the COMPARISON: every pair here must be
    # identical, and a table where the interesting thing is that two numbers
    # match reads better than one where they are twenty columns apart.
    if out:
        print("\nA20 -- the core's Zihpm counters against this instrument")
        print("%-11s %-28s %12s %12s %7s" %
              ("region", "event", "hardware", "instrument", "agree"))
        print("-" * 74)
        for name, d in out.items():
            for hk, sk, label in HPM_PAIRS:
                print("%-11s %-28s %12d %12d %7s" %
                      (name, label, d[hk], d[sk],
                       "yes" if d[hk] == d[sk] else "NO"))
            # No counterpart in the retired stream; reported, not checked.
            print("%-11s %-28s %12d %12s %7s" %
                  (name, "BTB hits (no counterpart)", d["hpm_btbhit"], "-", "-"))

    # A20, third path.  The same counters, read by SOFTWARE on the core through
    # csrr -- the way the board will read them -- against the instrument.  The
    # two paths above both sample the register array from the testbench; this
    # one goes through the CSR read port, the decoder and the pipeline, which is
    # the part the board actually exercises and a simulation-only check would
    # never touch.
    #
    # THIS ONE IS NOT AN EXACT MATCH, AND CANNOT BE.  sw/bench reads the six
    # counters immediately OUTSIDE the cycle window on entry and outside it on
    # exit -- the same convention minstret already uses here, and dhry_glue.c
    # says so in its own comment: "minstret first on entry and last on exit, so
    # the instruction window strictly contains the cycle window".  So the six
    # csrr's, their loop, and the mcycle/minstret reads themselves all fall
    # inside the counted window and outside the timed one.  The counters
    # therefore read HIGH by the snapshot code's own footprint.
    #
    # That is not an error to be tuned away; it is what reading a counter from
    # software costs, and it is exactly why A18 built a non-perturbing observer
    # in the first place.  What IS checkable, and is checked:
    #
    #   * the excess is non-negative -- the window contains, never truncates;
    #   * the excess is BOUNDED IN ABSOLUTE TERMS, and that is the whole test.
    #
    # Absolute, not relative, and the distinction is the point.  A snapshot
    # footprint is a constant: the same handful of instructions runs whether the
    # region is a thousand cycles or a billion.  A miscounting predicate is
    # proportional: it is wrong once per occurrence, so it scales with the
    # region.  Measured, the first time this ran: load-use is +6 in a 5,200-event
    # region and +6 in a 62,139-event one -- a 12x change in region size and no
    # change at all in the excess -- and multi-cycle EX is +0 in both.  Then the
    # same region was run at 200 and 400 Dhrystone runs, doubling every event
    # count, and all four excesses came back IDENTICAL TO THE EVENT:
    #
    #     dhry-runs   load-use    EX stall   redirects   transfers
    #       200       5206/5200   7200/7200  2052/2036   18227/18204
    #       400      10406/10400 14400/14400 4052/4036   36427/36404
    #       excess       +6          +0         +16         +23
    #
    # That is the constant signature, measured rather than assumed.  A relative limit
    # would have called the +6 a failure on the small region and a pass on the
    # large one, which is precisely backwards.
    #
    # The bound is generous on purpose.  bench_hpm_read is a six-iteration loop
    # around a six-way switch, called twice per region, plus the mcycle and
    # minstret reads: a few hundred instructions at the very most.  256 events
    # is comfortably above that and orders of magnitude below anything a real
    # counting error could produce at these region sizes.
    #
    # A miscounting event predicate cannot hide here anyway: it would show up in
    # the hardware-against-instrument table above, which IS exact.
    OVERHEAD_LIMIT = 256              # events, absolute
    if out and blk.get("has_hpm"):
        print("\nA20 -- the counters as SOFTWARE reads them, against this instrument")
        print("(software reads high by the snapshot code's own footprint -- see"
              " the comment above)")
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
    # The check above is "residual == 0".  A check that has never been observed
    # to fail is not evidence of anything, so break the accounting on purpose
    # and require each break to show up.
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
            # Fault 5 is the reason there are two checks.  It perturbs the
            # redirect count ONLY where the predictor closure reads it, leaving
            # the flush term -- and therefore the cycle identity -- closing
            # perfectly.  A single redirect too many or too few is exactly what
            # a predictor bug looks like from outside, and the cycle identity
            # is structurally unable to see it.
            mis_resid = base_mis - (d["redirect"] + (1 if f == 5 else 0))
            # Fault 6 is the reason there are THREE checks.  It perturbs neither
            # the cycle identity nor the predictor closure -- both are computed
            # entirely from the instrument's own counters -- and is visible only
            # to the hardware-against-instrument comparison.  A miscounting
            # performance counter is exactly this shape: every number the
            # instrument produces stays perfect and the board reports a lie.
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
