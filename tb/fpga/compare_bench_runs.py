#!/usr/bin/env python3
"""
Compare two benchmark captures and say which fields were allowed to move.

A17's done-when is that **not one cycle count may change**: every lever in it is
combinational or physical restructuring, so the only thing the benchmark output
is allowed to differ in is the clock frequency and the wall-clock rates derived
from it.  That is a mechanical claim and it is checked mechanically here rather
than by reading two tables side by side -- which is how A16's twelve identical
report blocks were checked, and how a single transposed digit would have got
through.

Three classes of field:

  INVARIANT   cycle counts, instruction counts, CRCs, iteration counts, and every
              ratio computed from a pair of counters (DMIPS/MHz, CoreMark/MHz,
              IPC).  These are integer counts or exact ratios of them and do not
              depend on the clock at all.  ANY difference is a failure.
  SCALES      Dhrystones/sec, DMIPS, the raw CoreMark score, and the run times.
              These are counts divided by a frequency, so they must move by
              EXACTLY the frequency ratio -- checked to a relative tolerance,
              not merely allowed to differ.
  IGNORED     the clock itself, and free text.

Run with --selftest to see it reject perturbed inputs.
"""
import argparse, json, math, sys

INVARIANT = [
    "dhry_runs", "dhry_cycles", "dhry_stat_cycles", "dhry_stat_instret",
    "dhry_check", "cm_iterations", "cm_cycles", "cm_instret",
    "seedcrc", "crclist", "crcmatrix", "crcstate", "crcfinal",
    "ntt_cycles_rv32i", "ntt_instret_rv32i",
    "ntt_cycles_rv32im", "ntt_instret_rv32im", "ntt_check", "ntt_sum",
    "dhry_window_skew_cycles",
    "dhry_cycles_per_run", "dmips_per_mhz", "dhry_ipc",
    "coremark_per_mhz", "cm_ipc",
    "ntt_cycle_ratio", "ntt_instret_ratio", "ntt_ipc_rv32i", "ntt_ipc_rv32im",
]

SCALES = ["dhrystones_per_sec", "dmips", "coremark", "dhry_secs", "cm_secs"]

REL_TOL = 1e-9


def compare(a, b, fault=0):
    """`a` is the reference block, `b` the new one.  Returns a list of problems."""
    probs = []
    fa, fb = a.get("clk_hz"), b.get("clk_hz")
    if not fa or not fb:
        return ["one of the captures has no clk_hz"]
    ratio = fb / fa

    for k in INVARIANT:
        if k not in a or k not in b:
            continue
        va, vb = a[k], b[k]
        if fault == 1 and k == "dhry_cycles":
            vb = vb + 1                       # one cycle
        if fault == 2 and k == "crcfinal":
            vb = (vb ^ 1) if isinstance(vb, int) else vb
        if isinstance(va, float) or isinstance(vb, float):
            same = math.isclose(va, vb, rel_tol=REL_TOL)
        else:
            same = va == vb
        if not same:
            probs.append("INVARIANT %s moved: %s -> %s" % (k, va, vb))

    for k in SCALES:
        if k not in a or k not in b:
            continue
        va, vb = a[k], b[k]
        want = va * ratio if k not in ("dhry_secs", "cm_secs") else va / ratio
        if fault == 3 and k == "coremark":
            vb = vb * 1.01                    # 1% off the frequency ratio
        if fault == 4:
            want = va                          # forget to scale at all
        if not math.isclose(vb, want, rel_tol=1e-6):
            probs.append("SCALED %s is %.6g, but %.6g x %.9g = %.6g"
                         % (k, vb, va, ratio, want))
    return probs


def block(path):
    d = json.load(open(path))
    return d["blocks"][0] if "blocks" in d else d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    ref, new = block(a.ref), block(a.new)
    probs = compare(ref, new)

    print("reference %.6f MHz   new %.6f MHz   ratio %.9f"
          % (ref["clk_hz"] / 1e6, new["clk_hz"] / 1e6,
             new["clk_hz"] / ref["clk_hz"]))
    print("%d invariant field(s), %d scaled field(s) checked"
          % (sum(1 for k in INVARIANT if k in ref and k in new),
             sum(1 for k in SCALES if k in ref and k in new)))

    if a.selftest:
        # The selftest runs against a SYNTHETIC pair, not against whichever two
        # files were passed.  Fault 4 -- "a scaled field was not scaled" -- is
        # vacuous when the two captures share a clock, and comparing two runs of
        # the same bitstream is the normal case.  A check that silently cannot
        # fail on the inputs it was given is exactly what this tool exists to
        # stop, so it does not get to do it to itself.
        print("\n--- selftest: each perturbation must be rejected ---")
        print("  (against a synthetic 1.2x-clock copy, so every fault is live)")
        fast = dict(ref)
        fast["clk_hz"] = ref["clk_hz"] * 1.2
        for k in SCALES:
            if k in fast:
                fast[k] = ref[k] * 1.2 if k not in ("dhry_secs", "cm_secs") \
                          else ref[k] / 1.2
        base = compare(ref, fast)
        if base:
            probs.append("the synthetic scaled copy does not compare clean: %s"
                         % base[0])
        why = {1: "one cycle added to dhry_cycles",
               2: "one bit flipped in crcfinal",
               3: "the CoreMark score off by 1% of the frequency ratio",
               4: "a scaled field not scaled at all"}
        for f in (1, 2, 3, 4):
            hit = compare(ref, fast, fault=f)
            print("  fault %d (%-52s) %s" % (f, why[f], "CAUGHT" if hit else "ESCAPED"))
            if not hit:
                probs.append("selftest fault %d escaped" % f)

    if probs:
        print("\n=== %d PROBLEM(S) ===" % len(probs))
        for p in probs:
            print("  " + p)
        return 1
    print("\nBENCH_COMPARE_OK: every cycle count identical, every rate scaled "
          "exactly by the clock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
