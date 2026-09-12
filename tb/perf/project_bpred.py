#!/usr/bin/env python3
"""
Run model/bpred.py over a retired control-transfer trace and say what the
predictor would do, independently of the RTL:

    cycles       = retired + load-use + multi-cycle EX + 2 x redirects
    cycles(pred) = retired + load-use + multi-cycle EX + 2 x mispredicts

The predictor changes when instructions are fetched, never which ones retire,
so `retired` and both stall terms are invariants.
"""
import argparse, csv, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
import bpred                                             # noqa: E402


def load_trace(path):
    """Yields (fetch_cycle, pc, insn, next_pc).

    Fetch, not retire: `retire - fetch` is not constant (an instruction held
    in ID has already been predicted); see model/bpred.py's VISIBILITY_GAP.
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
    # The region bounds in the profile JSON are retire cycles and the trace is
    # dated by fetch; a transfer's fetch precedes its retirement by at least
    # four cycles, so widening the window slightly cannot admit an outsider.
    SLACK = 8

    out, problems = {}, []
    for name, d in prof["regions"].items():
        lo, hi = d["lo_cycle"], d["hi_cycle"]
        # Warmed on everything before the region, measured only inside it, as
        # on the board.
        warm = [e for e in events if e[0] <= lo - SLACK]
        seg  = [e for e in events if lo - SLACK < e[0] <= hi - SLACK]
        if not seg:
            problems.append("%s: no control transfers in the trace window" % name)
            continue

        # DelayedBPred, not BPred: an update takes four cycles to reach a
        # lookup, and the trace carries the cycle of every transfer.
        b = bpred.DelayedBPred()
        for c, pc, insn, nxt in warm:
            taken = nxt != (pc + 4) & 0xFFFFFFFF
            b.predict(pc, c)                       # drains what is now visible
            b.update(c, pc, insn, taken, nxt if taken else 0)
        n = _run_warm(b, seg)

        if n["transfers"] != d["xfer"] + d["br_ntaken"]:
            problems.append("%s: the trace holds %d control transfers but the profile "
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
    # Every one of these is architecturally invisible: they show up here or
    # nowhere.
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
        # Cold and instant-update on both sides, so only the fault moves the
        # count.
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
