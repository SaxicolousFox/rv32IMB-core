#!/usr/bin/env python3
"""
MODS_A2 A24 -- the Tier-1 timing probe's frequency search.

WHAT QUESTION THIS ANSWERS.  §3.3 established that Tier-1 `Xkntt` shares the
core's clock, because `docs/isa-spec.md` freezes `kmm` at 4 cycles of EX
occupancy and a clock-domain crossing costs more than that before any
arithmetic happens.  So the core's Fmax is a hard FLOOR the Tier-1 butterfly
must meet, and A26/A27 must not commit the core to a number Track B cannot
reach.  This measures the ceiling before the commitment is made.

METHOD -- the same one A12 froze for the SoC, and for the same reason:

    binary search on the CONSTRAINT, verdict from POST-ROUTE WNS.

Do NOT report 1/(T - WNS) from a passing run.  The router optimises to the
constraint and stops; a run that passed with slack to spare says only that the
router had no reason to try harder.  The reported number is the fastest
constraint OBSERVED TO PASS, and nothing is extrapolated.

WHAT IS NOT MEASURED HERE, stated so the number is not over-claimed.  The
operands arrive through the core's forwarding mux and the result leaves through
the core's writeback mux.  Both are the CORE's paths; both are measured by the
SoC implementation run and neither is in this OOC netlist.  The reg-to-reg
DATAPATH_DELAY is printed beside the frequency so an interconnect budget can be
applied by arithmetic instead of being invented here.
"""
import argparse, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RE_TIMING = re.compile(r"^OOC_TIMING: (.*)$", re.M)
RE_ENDPT  = re.compile(r"^OOC_ENDPOINT: \S+ (.*)$", re.M)
RE_CELLS  = re.compile(r"^OOC_CELLS: (.*)$", re.M)
RE_GEN    = re.compile(r"^OOC_GENERICS: \S+ (.*)$", re.M)


def run_one(top, mhz, stages, param="STAGES"):
    period = 1000.0 / mhz
    t0 = time.time()
    r = subprocess.run(["bash", os.path.join(ROOT, "fpga/scripts/synth_ooc.sh"),
                        top, "%.4f" % period, "%s=%d" % (param, stages)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    dt = time.time() - t0
    m = RE_TIMING.search(out)
    if not m:
        sys.stderr.write(out[-4000:])
        return None
    d = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
    for k in ("period", "wns", "whs", "r2r_slack", "r2r_delay"):
        try:
            d[k] = float(d[k])
        except (KeyError, ValueError):
            d[k] = None
    for k in ("lut", "ff", "dsp"):
        d[k] = int(d[k])
    e = RE_ENDPT.search(out)
    d["endpoint"] = e.group(1).strip() if e else "?"
    c = RE_CELLS.search(out)
    d["cells"] = c.group(1).strip() if c else "?"
    g = RE_GEN.search(out)
    d["generics"] = g.group(1).strip() if g else "?"
    d["requested_mhz"] = mhz
    d["stages"] = stages
    d["seconds"] = round(dt, 1)
    return d


def fmt(d):
    return ("%8.3f MHz (period %6.3f ns): WNS %+0.3f  r2r %+0.3f  "
            "delay %.3f ns  %s  [%.0fs]" %
            (d["requested_mhz"], d["period"], d["wns"], d["r2r_slack"],
             d["r2r_delay"], "PASS" if d["verdict"] == "PASS" else "fail",
             d["seconds"]))


def search(top, stages, lo, hi, iters, param="STAGES"):
    hist = []
    # AUTO-WIDEN DOWNWARD RATHER THAN GIVE UP.  A lower bound that fails is not
    # an error, it is a bound -- and the first version of this threw away the
    # configurations that HAD converged, along with the JSON, because one of
    # three started below its bracket.  Two halvings, then stop: a design that
    # cannot close at 0.36x the requested floor is telling you something the
    # search is not the right tool for.
    tried = 0
    while True:
        d = run_one(top, lo, stages, param)
        if d is None:
            return None, hist
        hist.append(d); print("  lo  " + fmt(d), flush=True)
        if d["verdict"] == "PASS":
            break
        tried += 1
        if tried > 2:
            print("PROBE_FAIL: %s=%s does not meet timing even at %.3f MHz."
                  % (param, stages, lo))
            return None, hist
        hi, lo = lo, lo * 0.6
        print("  ... lower bound failed; widening the search down to "
              "%.3f MHz" % lo, flush=True)

    d = run_one(top, hi, stages, param)
    if d is None:
        return None, hist
    hist.append(d); print("  hi  " + fmt(d), flush=True)
    if d["verdict"] == "PASS":
        print("PROBE_NOTE: the upper bound %.3f MHz PASSED -- the true ceiling "
              "is above the searched range, and %.3f MHz is a LOWER BOUND on "
              "it, not a measurement of it." % (hi, hi))
        return d, hist

    best = hist[0]
    for i in range(iters):
        mid = (lo + hi) / 2.0
        d = run_one(top, mid, stages, param)
        if d is None:
            break
        hist.append(d)
        print("  it%d %s" % (i + 1, fmt(d)), flush=True)
        if d["verdict"] == "PASS":
            best = d; lo = mid
        else:
            hi = mid
    return best, hist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", default="rvntt_tier1_probe")
    ap.add_argument("--stages", "--value", dest="stages", type=int,
                    action="append",
                    help="value of --param to sweep; repeatable; default 4, 3, 2")
    # A25 probes rvntt_muldiv's product-pipeline depth with the same machinery:
    # the question ("what does one register level cost in nanoseconds") and the
    # method (binary search on the constraint, verdict from post-route WNS) are
    # identical, and a second copy of this file would drift.
    ap.add_argument("--param", default="STAGES",
                    help="the generic to sweep (default STAGES)")
    ap.add_argument("--lo", type=float, default=110.0)
    ap.add_argument("--hi", type=float, default=280.0)
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--probe", type=float, default=None,
                    help="single point: implement once at this MHz and stop")
    ap.add_argument("--out", default=os.path.join(ROOT, "fpga/build/tier1_probe.json"))
    a = ap.parse_args()
    stages_list = a.stages or [4, 3, 2]

    results = {}
    failed = []
    for st in stages_list:
        # `kmm` taps out one segment early, so a STAGES-deep pipeline gives it
        # STAGES cycles of occupancy and gives kbfct/kbfgs STAGES+1.  STAGES=4
        # is therefore the frozen table exactly: 4 and 5.
        if a.param == "STAGES":
            print("=== STAGES=%d  (EX occupancy %d for kmm, %d for "
                  "kbfct/kbfgs) ===" % (st, st, st + 1), flush=True)
        else:
            print("=== %s=%d ===" % (a.param, st), flush=True)
        if a.probe is not None:
            d = run_one(a.top, a.probe, st, a.param)
            if d is None:
                return 1
            print("  " + fmt(d), flush=True)
            print("  THIS IS NOT AN Fmax.  One implementation run against one "
                  "constraint is a BOUND: PASS means the ceiling is at or above "
                  "%.3f MHz, fail means it is below." % a.probe)
            results[st] = {"probe": d}
            continue
        best, hist = search(a.top, st, a.lo, a.hi, a.iters, a.param)
        if best is None:
            results[st] = {"history": hist, "best": None,
                           "no_pass_above_mhz": min(h["requested_mhz"]
                                                    for h in hist)}
            failed.append(st)
            continue
        fastest_fail = min((h["requested_mhz"] for h in hist
                            if h["verdict"] != "PASS"), default=None)
        print("  Fmax(%s=%d) = %.3f MHz   LUT %d  FF %d  DSP %d" %
              (a.param, st, best["requested_mhz"], best["lut"], best["ff"],
               best["dsp"]),
              flush=True)
        print("  worst reg-to-reg endpoint: %s" % best["endpoint"], flush=True)
        results[st] = {"best": best, "history": hist,
                       "fastest_fail_mhz": fastest_fail}

    # ------------------------------------------------------------------
    # THE GUARD THAT THIS SCRIPT EXISTS TO HAVE.
    # ------------------------------------------------------------------
    # A sweep whose parameter never reached the tool reports one measurement
    # three times and looks exactly like three measurements that happen to
    # agree.  That is what happened on A24's first run: `cmd.exe /c` ate the
    # `=` in `STAGES=2`, synth_design got no generic, and STAGES 4, 3 and 2 came
    # back with byte-identical WNS at every search point.  The transport is
    # fixed; this is the check that says so every time rather than once.
    #
    # A different pipeline depth MUST change the netlist -- fewer register
    # levels is fewer flip-flops.  Identical (lut, ff, dsp, endpoint) across two
    # different parameter values is therefore evidence the parameter did
    # nothing, and it is reported as a FAILURE, not as a result.
    have = {st: r for st, r in results.items()
            if (r.get("best") or r.get("probe"))}
    if len(have) > 1:
        prints = {}
        for st, r in have.items():
            d = r.get("best") or r.get("probe")
            prints[st] = (d["lut"], d["ff"], d["dsp"], d["endpoint"])
        print("\nnetlist fingerprint per configuration "
              "(LUT, FF, DSP, worst endpoint):")
        for st in sorted(prints):
            print("  %s=%-3s %s" % (a.param, st, prints[st]))
        dup = [(x, y) for i, x in enumerate(sorted(prints))
               for y in sorted(prints)[i + 1:] if prints[x] == prints[y]]
        if dup:
            print("\nPROBE_FAIL: %s=%s produced the SAME netlist as %s=%s.  A "
                  "different pipeline depth cannot have the same flop count, so "
                  "the generic did not reach synth_design and these are not "
                  "independent measurements."
                  % (a.param, dup[0][0], a.param, dup[0][1]))
            return 1
        gens = {st: (r.get("best") or r.get("probe"))["generics"]
                for st, r in have.items()}
        for st, g in sorted(gens.items()):
            if ("%s=%s" % (a.param, st)) not in g:
                print("\nPROBE_FAIL: the run for %s=%s reports generics %r, "
                      "which does not contain it." % (a.param, st, g))
                return 1

    # MERGE RATHER THAN OVERWRITE, so a follow-up run of one configuration does
    # not delete the other two's data.
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    doc = {"part": "xc7a100tcsg324-1", "mode": "out_of_context",
           "top": a.top, "param": a.param, "results": {}}
    if os.path.exists(a.out):
        try:
            old = json.load(open(a.out))
            if old.get("top") == a.top and old.get("param") == a.param:
                doc["results"] = old.get("results", {})
        except (ValueError, OSError):
            pass
    doc["results"].update({str(k): v for k, v in results.items()})
    with open(a.out, "w") as fh:
        json.dump(doc, fh, indent=2)
    print("wrote %s" % os.path.relpath(a.out, ROOT))
    if failed:
        print("PROBE_PARTIAL: %s did not close anywhere in the searched range; "
              "the others are recorded." % ", ".join("%s=%s" % (a.param, f)
                                                     for f in failed))
        return 1
    print("PROBE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
