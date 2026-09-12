#!/usr/bin/env python3
"""
Compare two benchmark captures (parse_bench_uart.py --json output).

INVARIANT fields -- cycle and instruction counts, CRCs, and the ratios derived
from pairs of counters -- must be identical.  SCALED fields -- Dhrystones/sec,
DMIPS, CoreMark, run times -- must move by exactly the clock ratio.  Two runs of
one design at different clocks therefore compare clean; any cycle-count change
is reported.  --selftest perturbs a synthetic pair and requires each to be
rejected; without --ref it runs against a built-in block.
"""
import argparse, json, math, sys

INVARIANT = [
    "dhry_runs", "dhry_cycles", "dhry_stat_cycles", "dhry_stat_instret",
    "dhry_check", "cm_iterations", "cm_cycles", "cm_instret",
    "seedcrc", "crclist", "crcmatrix", "crcstate", "crcfinal",
    "dhry_window_skew_cycles",
    "dhry_cycles_per_run", "dmips_per_mhz", "dhry_ipc",
    "coremark_per_mhz", "cm_ipc",
]

SCALES = ["dhrystones_per_sec", "dmips", "coremark", "dhry_secs", "cm_secs"]

REL_TOL = 1e-9

# A plausible block, for --selftest without a reference capture.
SYNTHETIC = {
    "clk_hz": 96250000, "dhry_runs": 2000000, "dhry_cycles": 1218000000,
    "dhry_stat_cycles": 1218000040, "dhry_stat_instret": 1063000000,
    "dhry_check": 0, "cm_iterations": 3300, "cm_cycles": 975000000,
    "cm_instret": 847000000, "seedcrc": 0xe9f5, "crclist": 0xe714,
    "crcmatrix": 0x1fd7, "crcstate": 0x8e3a, "crcfinal": 0x33ff,
    "dhry_window_skew_cycles": 40, "dhry_cycles_per_run": 609.0,
    "dmips_per_mhz": 0.9346, "dhry_ipc": 0.8728, "coremark_per_mhz": 3.3846,
    "cm_ipc": 0.8687, "dhrystones_per_sec": 158046.0, "dmips": 89.95,
    "coremark": 325.77, "dhry_secs": 12.65, "cm_secs": 10.13,
}


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
    ap.add_argument("--ref", default=None)
    ap.add_argument("--new", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if not a.selftest and (a.ref is None or a.new is None):
        ap.error("--ref and --new are required (or --selftest)")
    ref = block(a.ref) if a.ref else dict(SYNTHETIC)
    new = block(a.new) if a.new else ref
    probs = compare(ref, new)

    print("reference %.6f MHz   new %.6f MHz   ratio %.9f"
          % (ref["clk_hz"] / 1e6, new["clk_hz"] / 1e6,
             new["clk_hz"] / ref["clk_hz"]))
    print("%d invariant field(s), %d scaled field(s) checked"
          % (sum(1 for k in INVARIANT if k in ref and k in new),
             sum(1 for k in SCALES if k in ref and k in new)))

    if a.selftest:
        # Against a synthetic 1.2x-clock copy, so fault 4 cannot be vacuous.
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
