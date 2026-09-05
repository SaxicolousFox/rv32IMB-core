#!/usr/bin/env python3
"""
A12's Fmax measurement -- binary search, by the plan's method and only by it.

    1. constrain the target period T
    2. synthesise and implement to routed
    3. read WNS from the POST-ROUTE timing report
    4. binary search T until WNS is barely non-negative
    5. report Fmax = 1/T_min with the Vivado version, strategy and speed grade

The rule that makes the number mean anything: DO NOT report 1/(T - WNS) from a
passing run.  The router optimises to the constraint and stops there, so a run
that passes with +2 ns of slack says nothing about whether it would pass 2 ns
faster -- it says the router had no reason to try.  Only a run that was actually
CONSTRAINED at T and met it is evidence about T.  This script therefore reports
the fastest constraint that passed, and prints the extrapolated number nowhere.

The constraint is set through the MMCM divider (fpga/scripts/gen_soc_clk.py),
because with an MMCM-generated clock Vivado derives the period from the MMCM
rather than from a create_clock; build_soc.tcl re-reads the derived period and
prints it, so intent and implementation are compared every iteration.  The MMCM
grid is about 1.4 MHz wide near 70 MHz, so the search resolution is bounded
below by the hardware, not by patience.

The memory image is held constant across iterations: the only variable is the
clock constraint.
"""
import argparse, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RE_RESULT = re.compile(r"^SOC_RESULT (.*)$", re.M)


def run_one(mhz, keep_dir, strategy="default"):
    """Implement at `mhz` and return the parsed SOC_RESULT dict (or None)."""
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "fpga/scripts/gen_soc_clk.py"),
                        "--mhz", "%.4f" % mhz],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    gen = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        print(gen)
        return None

    env = dict(os.environ)
    env["OUT"] = keep_dir
    t0 = time.time()
    r = subprocess.run(["bash", os.path.join(ROOT, "fpga/scripts/build_soc.sh"),
                        "0", "0", strategy],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    out = r.stdout.decode("utf-8", "replace")
    dt = time.time() - t0

    m = RE_RESULT.search(out)
    if not m:
        sys.stderr.write(out[-4000:])
        return None
    d = dict(kv.split("=", 1) for kv in m.group(1).split())
    for k in ("period", "mhz", "wns", "whs"):
        d[k] = float(d[k])
    for k in ("luts", "ffs", "bram"):
        d[k] = int(d[k])
    d["seconds"] = round(dt, 1)
    d["requested_mhz"] = mhz
    d["gen"] = gen.strip().splitlines()[0]
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", type=float, default=55.0, help="known-good MHz")
    ap.add_argument("--hi", type=float, default=75.0, help="known-bad MHz")
    ap.add_argument("--tol", type=float, default=1.0, help="stop when hi-lo < tol")
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(ROOT, "fpga/build/fmax"))
    # A17 lever 3.  The strategy is part of the MEASUREMENT, not of the tooling:
    # A12's number, A16's and A17 lever 1's were all taken under "default", and a
    # number taken under anything else is comparable only to other numbers taken
    # under the same thing.  It is recorded in the summary for that reason.
    ap.add_argument("--strategy", default="default",
                    choices=["default", "explore_postroute"])
    # H1.  ONE implementation run at one constraint, and no search.
    #
    # The question "did this lever help?" does not need a converged Fmax -- it
    # needs one point compared against a known one, and a search spends six to
    # eight runs answering it.  A23 used this to test whether registering the
    # HPM event bus recovered the clock (it did, by 3.58 MHz) before committing
    # to a three-hour search, and that instinct is worth making a flag rather
    # than a remembered trick with --max-iters 1.
    #
    # What it does NOT produce is an Fmax.  A single passing point is a lower
    # bound and a single failing point is an upper bound; the plan's method is
    # a binary search on post-route WNS and this does not replace it.  The
    # output says so, so a probe result cannot be quoted as a measurement.
    ap.add_argument("--probe", type=float, default=None, metavar="MHZ",
                    help="implement once at this frequency and report "
                         "PASS/fail with WNS.  Not a search and not an Fmax.")
    a = ap.parse_args()

    # ABSOLUTE, always.  build_soc.sh cds into its Windows staging directory
    # before it copies products to $OUT, and it wipes that directory at the
    # start of every run -- so a RELATIVE --out silently writes each iteration's
    # reports somewhere that the next iteration deletes.  The search still
    # produces the right number, because WNS is parsed from stdout, but the
    # post-route timing report that says WHERE the critical path went is gone.
    # Found the hard way at A16.
    a.out = os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)

    if a.probe is not None:
        d = run_one(a.probe, os.path.join(a.out, "mhz_%.2f" % a.probe),
                    a.strategy)
        if d is None:
            print("PROBE_FAIL: implementation did not complete at %.2f MHz"
                  % a.probe)
            return 1
        ok = d["wns"] >= 0 and d["whs"] >= 0
        print("  probe %7.3f MHz (period %.3f ns): WNS %+.3f  WHS %+.3f  %s  "
              "[%.0fs]" % (d["mhz"], d["period"], d["wns"], d["whs"],
                          "PASS" if ok else "fail", d["seconds"]))
        print("  LUTs %d  FFs %d  BRAM %d  DSP %s  strategy %s"
              % (d["luts"], d["ffs"], d["bram"], d.get("dsp", "?"), a.strategy))
        json.dump(d, open(os.path.join(a.out, "probe.json"), "w"), indent=1)
        print("\nPROBE_%s at %.3f MHz -- THIS IS NOT AN Fmax.  A passing probe "
              "is a lower bound and a failing one is an upper bound; the "
              "plan's method is a binary search on post-route WNS.  Use it to "
              "decide whether a lever helped, then search."
              % ("PASS" if ok else "FAIL", d["mhz"]))
        return 0 if ok else 1

    history, best = [], None
    lo, hi = a.lo, a.hi

    # The endpoints are measured, not assumed.  Taking `lo` on trust is how a
    # search converges neatly onto a number that was never verified.
    for label, mhz in (("lo", lo), ("hi", hi)):
        d = run_one(mhz, os.path.join(a.out, "mhz_%.2f" % mhz), a.strategy)
        if d is None:
            print("FMAX_FAIL: implementation did not complete at %.2f MHz" % mhz)
            return 1
        d["label"] = label
        history.append(d)
        ok = d["wns"] >= 0 and d["whs"] >= 0
        print("  %-3s %7.3f MHz (period %.3f ns): WNS %+.3f  WHS %+.3f  %s  [%.0fs]"
              % (label, d["mhz"], d["period"], d["wns"], d["whs"],
                 "PASS" if ok else "fail", d["seconds"]))
        if label == "lo":
            if not ok:
                print("FMAX_FAIL: the lower bound %.2f MHz does not meet timing; "
                      "restart the search below it." % mhz)
                return 1
            best = d
        else:
            if ok:
                print("FMAX_FAIL: the upper bound %.2f MHz already meets timing; "
                      "restart the search above it." % mhz)
                return 1

    it = 0
    while hi - lo >= a.tol and it < a.max_iters:
        it += 1
        mid = (lo + hi) / 2.0
        d = run_one(mid, os.path.join(a.out, "mhz_%.2f" % mid), a.strategy)
        if d is None:
            print("FMAX_FAIL: implementation did not complete at %.2f MHz" % mid)
            return 1
        d["label"] = "it%d" % it
        history.append(d)
        ok = d["wns"] >= 0 and d["whs"] >= 0
        print("  %-4s %7.3f MHz (period %.3f ns): WNS %+.3f  WHS %+.3f  %s  [%.0fs]"
              % (d["label"], d["mhz"], d["period"], d["wns"], d["whs"],
                 "PASS" if ok else "fail", d["seconds"]))
        # Bisect on the ACHIEVED frequency, not the requested one: the MMCM grid
        # means a request of 68.0 may land on 68.18, and bisecting on the request
        # would let the search stall between two indistinguishable points.
        if ok:
            lo = d["mhz"]
            if best is None or d["mhz"] > best["mhz"]:
                best = d
        else:
            hi = d["mhz"]

    summary = {
        "fmax_mhz": best["mhz"],
        "fmax_period_ns": best["period"],
        "wns_at_fmax": best["wns"],
        "whs_at_fmax": best["whs"],
        "first_failing_mhz": min((h["mhz"] for h in history if h["wns"] < 0),
                                 default=None),
        "luts": best["luts"], "ffs": best["ffs"], "bram": best["bram"],
        "part": "xc7a100tcsg324-1", "speed_grade": "-1",
        "vivado": "2025.2",
        "strategy": (
            "default (synth_design + opt/place/phys_opt/route_design, "
            "no directive overrides)" if a.strategy == "default" else
            "explore_postroute (opt/place/phys_opt/route -directive Explore, "
            "plus a second post-route phys_opt_design)"),
        "history": history,
    }
    with open(os.path.join(a.out, "fmax.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print()
    print("Fmax = %.3f MHz  (period %.3f ns, WNS %+.3f ns, WHS %+.3f ns)"
          % (best["mhz"], best["period"], best["wns"], best["whs"]))
    if summary["first_failing_mhz"]:
        print("fastest constraint that FAILED: %.3f MHz"
              % summary["first_failing_mhz"])
    print("Vivado 2025.2, xc7a100tcsg324-1 (-1 speed grade), strategy %s"
          % a.strategy)
    print("LUTs %d  FFs %d  BRAM tiles %d" % (best["luts"], best["ffs"], best["bram"]))
    print("wrote %s" % os.path.relpath(os.path.join(a.out, "fmax.json"), ROOT))
    print("FMAX_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
