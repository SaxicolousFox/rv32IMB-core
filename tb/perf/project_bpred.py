#!/usr/bin/env python3
"""
A19's projection, and later A19's check: run docs/a19-bpred-spec.md over a
retired control-transfer trace and say what the predictor would do.

This exists BEFORE the RTL, on purpose.  MODS_A A19 quotes a table of expected
gains that was computed from A13's RV32I counts and warns, in its own words,
that "after A14 the instruction mix changes and the branch fraction with it, so
retake the baseline and recompute the projection before believing the delta".
This is that recomputation, and afterwards it is the independent model the
implementation is measured against.

The arithmetic is A18's identity with one term replaced:

    cycles       = retired + load-use + multi-cycle EX + 2 x redirects
    cycles(pred) = retired + load-use + multi-cycle EX + 2 x MISPREDICTS

Nothing else moves.  The predictor changes when instructions are fetched, never
which ones retire, so `retired` and both stall terms are invariants of the
change -- and if a measured run ever shows one of them moving, that is a bug in
the predictor and not a result.
"""
import argparse, csv, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
import bpred                                             # noqa: E402


def load_trace(path):
    """Yields (fetch_cycle, pc, insn, next_pc).

    FETCH, not retire: the visibility rule is about when a lookup happened, and
    `retire - fetch` is not constant -- an instruction held in ID by a load-use
    interlock has already been predicted.  See model/bpred.py's VISIBILITY_GAP.
    Older traces without a `fetch` column cannot be used; regenerate them.
    """
    with open(path) as f:
        rdr = csv.DictReader(f)
        if "fetch" not in (rdr.fieldnames or []):
            raise SystemExit("%s predates the fetch-cycle column; regenerate it "
                             "with tb/perf/run_stall_profile.py --trace" % path)
        for row in rdr:
            yield (int(row["fetch"]), int(row["pc"], 16),
                   int(row["insn"], 16), int(row["next_pc"], 16))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--profile", required=True, help="the JSON from run_stall_profile.py")
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    prof = json.load(open(a.profile))
    events = list(load_trace(a.trace))
    # The region bounds in the profile JSON are retire cycles; the trace is now
    # dated by fetch.  A transfer's fetch precedes its retirement by at least
    # four cycles, so widening the window by a few cycles on each side cannot
    # include a transfer from outside it -- and the region-boundary csrr reads
    # are not control transfers, so nothing sits exactly on the edge.
    SLACK = 8

    out, problems = {}, []
    for name, d in prof["regions"].items():
        lo, hi = d["lo_cycle"], d["hi_cycle"]
        # The predictor is WARMED on everything that ran before the region and
        # then measured only inside it.  Starting it cold at the region boundary
        # would credit the design with a cold-start penalty it does not pay on
        # the board, where the same code has been running since reset.
        warm = [e for e in events if e[0] <= lo - SLACK]
        seg  = [e for e in events if lo - SLACK < e[0] <= hi - SLACK]
        if not seg:
            problems.append("%s: no control transfers in the trace window" % name)
            continue

        # DelayedBPred, not BPred: an update takes four retire cycles to reach
        # a lookup (spec section 8), and the trace carries the cycle of every
        # transfer, so the projection can honour the rule exactly rather than
        # assume the predictor learns instantly.  It matters -- a tight loop
        # mispredicts on alternate iterations because of it.
        b = bpred.DelayedBPred()
        for c, pc, insn, nxt in warm:
            taken = nxt != (pc + 4) & 0xFFFFFFFF
            b.predict(pc, c)                       # drains what is now visible
            b.update(c, pc, insn, taken, nxt if taken else 0)
        n = _run_warm(b, seg)

        if n["transfers"] != d["xfer"] + d["br_ntaken"]:
            problems.append("%s: the trace holds %d control transfers but A18 "
                            "counted %d -- the trace and the profile disagree"
                            % (name, n["transfers"], d["xfer"] + d["br_ntaken"]))

        base_cycles = d["cycle"]
        fixed = d["retired"] + d["id_stall"] + d["ex_stall"]
        new_cycles = fixed + 2 * n["mispredicts"]
        o = dict(n)
        o["base_cycles"]   = base_cycles
        o["new_cycles"]    = new_cycles
        o["retired"]       = d["retired"]
        o["base_ipc"]      = d["retired"] / base_cycles
        o["new_ipc"]       = d["retired"] / new_cycles
        o["speedup"]       = base_cycles / new_cycles
        o["saved"]         = 2 * (d["redirect"] - n["mispredicts"])
        o["regression"]    = 2 * n["ntaken_predicted_taken"]
        out[name] = o

    w = ("region", "xfers", "taken", "n-taken", "mispred", "saved", "regress",
         "cycles", "->", "IPC", "->", "speedup")
    print("%-11s %8s %8s %8s %8s %9s %8s %10s %10s %7s %7s %8s" % w)
    print("-" * 118)
    for name, o in out.items():
        print("%-11s %8d %8d %8d %8d %9d %8d %10d %10d %7.4f %7.4f %7.3fx"
              % (name, o["transfers"], o["taken"], o["not_taken"],
                 o["mispredicts"], o["saved"], o["regression"],
                 o["base_cycles"], o["new_cycles"], o["base_ipc"],
                 o["new_ipc"], o["speedup"]))
    print("-" * 118)
    for name, o in out.items():
        print("%-11s  taken predicted %5.1f%%  |  returns predicted %5.1f%% of %d  "
              "|  not-taken wrongly predicted taken %d of %d"
              % (name, 100.0 * o["hit_taken"] / max(o["taken"], 1),
                 100.0 * o["ret_correct"] / max(o["ret"], 1), o["ret"],
                 o["ntaken_predicted_taken"], o["not_taken"]))

    # ---- fault injection ---------------------------------------------------
    # Every one of these is ARCHITECTURALLY INVISIBLE -- the core still retires
    # the same instructions in the same order, so no commit-log diff, no
    # riscv-formal check and no compliance test can see any of them.  They show
    # up here or nowhere, which is MODS_A A19's reason for requiring that at
    # least one fault be caught by the cycle model rather than by a correctness
    # check.
    if a.selftest:
        print("\n--- selftest: each fault must move the mispredict count ---")
        name = next(iter(out))
        d = prof["regions"][name]
        lo, hi = d["lo_cycle"], d["hi_cycle"]
        seg = [(pc, insn, nxt) for c, pc, insn, nxt in events if lo < c <= hi]
        why = {1: "the BTB tag comparison is ignored",
               2: "the 2-bit counter wraps instead of saturating",
               3: "the RAS push condition is dropped",
               4: "a cold entry is predicted taken"}
        # Cold on both sides, and INSTANT-update on both sides: the warmed run
        # above starts from a predictor that has already seen the program and
        # honours the visibility window, so comparing a cold instant-update
        # fault against it would report the warm-up and the window as if they
        # were the fault.  What this selftest asks is only whether each fault
        # moves the count, and it asks it of two identically-configured runs.
        cold = seg
        base = bpred.run(cold, fault=0)["mispredicts"]
        for f in (1, 2, 3, 4):
            n = bpred.run(cold, fault=f)
            moved = n["mispredicts"] != base
            print("  fault %d (%-42s) mispredicts %8d vs %8d  %s"
                  % (f, why[f], n["mispredicts"], base,
                     "CAUGHT" if moved else "ESCAPED"))
            if not moved:
                problems.append("fault %d did not change the mispredict count" % f)

    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
        print("\nwrote %s" % a.json)

    if problems:
        print("\n=== %d PROBLEM(S) ===" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    return 0


def _run_warm(b, seg):
    """The shared scorer, so this driver and the profiler's closure cannot
    disagree about the suppression rule -- see model/bpred.score_trace."""
    return bpred.score_trace(b, seg)


if __name__ == "__main__":
    sys.exit(main())
