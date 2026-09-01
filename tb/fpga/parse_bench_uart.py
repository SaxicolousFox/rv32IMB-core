#!/usr/bin/env python3
"""
Check an A13 capture and compute the scores from it.

Every rate is computed HERE and nothing is computed on the target, for two
reasons.  The first is arithmetic: Dhrystone's own Microseconds and
Dhrystones_Per_Second overflow 32-bit long at these cycle counts, so the numbers
it would print are wrong in a way that looks plausible.  The second is that the
two headline figures the plan asks for do not depend on the clock at all --

    DMIPS/MHz     = runs * 1e6 / (cycles * 1757)
    CoreMark/MHz  = iterations * 1e6 / cycles

-- so they are exact integers-over-integers from the cycle counters and inherit
none of the +-0.4 ns uncertainty in the measured Fmax.  Dhrystones/sec and the
raw CoreMark score do need the frequency, and are reported separately for that
reason.

The same parser reads a Verilator capture, a build-host capture and a capture off
the board, which is what makes fault-injecting it (--selftest) cover all three.
A host capture carries `host=1` and is REFUSED unless --functional-only, because
substituting a host result for a hardware one is the specific mistake this file
exists to make impossible.
"""
import argparse, json, os, re, sys

BEGIN = "=== rvntt A13 ==="
END   = "=== end A13 ==="

# Dhrystone's definition: 1757 Dhrystones/sec is one VAX 11/780 MIPS.
VAX_DHRY_PER_SEC = 1757.0

# The bit assignments in dhry_verify(), so a failure names the expectation that
# broke rather than printing a bare mask.
DHRY_BITS = [
    "snapshot not taken (setStats never called with 0)",
    "Int_Glob != 5", "Bool_Glob != 1", "Ch_1_Glob != 'A'", "Ch_2_Glob != 'B'",
    "Arr_1_Glob[8] != 7", "Arr_2_Glob[8][7] != runs + 10",
    "Ptr_Glob->Discr != Ident_1", "Ptr_Glob->Enum_Comp != Ident_3",
    "Ptr_Glob->Int_Comp != 17", "Ptr_Glob->Str_Comp wrong",
    "Next_Ptr_Glob->Discr != Ident_1", "Next_Ptr_Glob->Enum_Comp != Ident_2",
    "Next_Ptr_Glob->Int_Comp != 18", "Next_Ptr_Glob->Str_Comp wrong",
]

# seedcrc values core_main.c recognises as a PERFORMANCE run.  A validation or
# profile run would still print correct CRCs but is not a score, so pinning this
# stops a differently-configured build being reported as a CoreMark result.
PERF_SEEDCRC = {0x8a02: "6K performance (TOTAL_DATA_SIZE=6000)",
                0xe9f5: "2K performance (TOTAL_DATA_SIZE=2000)"}


class Fail(Exception):
    pass


def split_blocks(text):
    """Complete BEGIN..END blocks, in order.  A partial leading or trailing block
    is dropped rather than parsed: a capture almost always starts mid-block."""
    blocks, cur = [], None
    for line in text.replace("\r", "").split("\n"):
        if line.strip() == BEGIN:
            cur = []
            continue
        if line.strip() == END:
            if cur is not None:
                blocks.append(cur)
            cur = None
            continue
        if cur is not None:
            cur.append(line)
    return blocks


def kv(lines):
    d = {}
    for line in lines:
        if "=" in line and not line.startswith(" "):
            k, _, v = line.partition("=")
            k = k.strip()
            if re.fullmatch(r"[a-z_][a-z0-9_]*", k):
                d.setdefault(k, v.strip())
    return d


def need(d, key, blk):
    if key not in d:
        raise Fail("block %d has no `%s=` line" % (blk, key))
    return d[key]


def num(d, key, blk):
    v = need(d, key, blk)
    try:
        return int(v, 0)
    except ValueError:
        raise Fail("block %d: `%s=%s` is not a number" % (blk, key, v))


def parse_block(lines, idx, args):
    d = kv(lines)
    text = "\n".join(lines)
    b = {"index": idx, "host": "host" in d}

    b["flags"] = need(d, "flags", idx)
    if not b["flags"].strip():
        raise Fail("block %d: empty `flags=` line -- the build flags are part of "
                   "the result and must be recorded" % idx)
    b["clk_hz"] = num(d, "clk_hz", idx)

    b["dhry_runs"]    = num(d, "dhry_runs", idx)
    b["dhry_cycles"]  = num(d, "dhry_cycles", idx)
    b["dhry_stat_cycles"]  = num(d, "dhry_stat_cycles", idx)
    b["dhry_stat_instret"] = num(d, "dhry_stat_instret", idx)
    b["dhry_check"]   = num(d, "dhry_check", idx)

    b["cm_iterations"] = num(d, "cm_iterations", idx)
    b["cm_cycles"]     = num(d, "cm_cycles", idx)
    b["cm_instret"]    = num(d, "cm_instret", idx)

    if b["dhry_check"] != 0:
        broken = [DHRY_BITS[i] for i in range(len(DHRY_BITS))
                  if b["dhry_check"] & (1 << i)]
        raise Fail("block %d: Dhrystone final values wrong (0x%08x): %s"
                   % (idx, b["dhry_check"], "; ".join(broken) or "unknown bit"))

    # CoreMark validates its own CRCs and prints one ERROR line per mismatch.
    crc_errs = [l.strip() for l in lines if "ERROR!" in l and "crc" in l]
    if crc_errs:
        raise Fail("block %d: CoreMark CRC mismatch -- %s" % (idx, crc_errs[0]))

    m = re.search(r"^seedcrc\s*:\s*0x([0-9a-fA-F]+)", text, re.M)
    if not m:
        raise Fail("block %d: no CoreMark seedcrc line -- CoreMark did not run"
                   % idx)
    b["seedcrc"] = int(m.group(1), 16)
    if b["seedcrc"] not in PERF_SEEDCRC:
        raise Fail("block %d: seedcrc 0x%04x is not a CoreMark performance run "
                   "(known: %s)" % (idx, b["seedcrc"],
                                    ", ".join("0x%04x" % k for k in PERF_SEEDCRC)))
    b["cm_config"] = PERF_SEEDCRC[b["seedcrc"]]
    for name in ("crclist", "crcmatrix", "crcstate", "crcfinal"):
        m = re.search(r"^\[\d+\]%s\s*:\s*0x([0-9a-fA-F]+)" % name, text, re.M)
        if not m:
            raise Fail("block %d: CoreMark printed no %s" % (idx, name))
        b[name] = int(m.group(1), 16)

    if args.dhry_runs is not None and b["dhry_runs"] != args.dhry_runs:
        raise Fail("block %d: dhry_runs=%d, expected %d -- the capture is from a "
                   "different build" % (idx, b["dhry_runs"], args.dhry_runs))
    if args.iterations is not None and b["cm_iterations"] != args.iterations:
        raise Fail("block %d: cm_iterations=%d, expected %d -- the capture is "
                   "from a different build"
                   % (idx, b["cm_iterations"], args.iterations))
    return b


def derive(b, args):
    """Scores.  Kept separate from parsing so --functional-only can skip it."""
    if b["clk_hz"] <= 0:
        raise Fail("block %d: clk_hz=%d" % (b["index"], b["clk_hz"]))
    for k in ("dhry_cycles", "dhry_stat_cycles", "dhry_stat_instret",
              "cm_cycles", "cm_instret"):
        if b[k] <= 0:
            raise Fail("block %d: %s=%d -- the counter did not advance"
                       % (b["index"], k, b[k]))

    mhz = b["clk_hz"] / 1e6

    # setStats brackets Start_Timer/Stop_Timer, so its window is a few
    # instructions WIDER.  Anything more than that means the two are not
    # measuring the same region and IPC cannot be attributed to Dhrystone.
    if b["dhry_stat_cycles"] < b["dhry_cycles"]:
        raise Fail("block %d: setStats window (%d) is SHORTER than Dhrystone's "
                   "own timer (%d) -- they cannot both be right"
                   % (b["index"], b["dhry_stat_cycles"], b["dhry_cycles"]))
    # The gap is a FIXED number of instructions -- the call and return around
    # Start_Timer/Stop_Timer -- so the invariant is absolute cycles, not a
    # percentage.  A relative bound would be a different test at every run
    # length: 100 cycles is 0.1% of a 30-run sanity check and 0.00006% of the
    # real one, and only the first would ever fail.
    skew_cycles = b["dhry_stat_cycles"] - b["dhry_cycles"]
    if skew_cycles > 4096:
        raise Fail("block %d: setStats window is %d cycles wider than "
                   "Dhrystone's own timer -- they are not bracketing the same "
                   "region" % (b["index"], skew_cycles))
    b["dhry_window_skew_cycles"] = skew_cycles
    b["dhry_window_skew_pct"] = 100.0 * skew_cycles / b["dhry_cycles"]

    b["dhry_cycles_per_run"] = b["dhry_cycles"] / b["dhry_runs"]
    b["dhrystones_per_sec"]  = b["dhry_runs"] * b["clk_hz"] / b["dhry_cycles"]
    b["dmips"]               = b["dhrystones_per_sec"] / VAX_DHRY_PER_SEC
    b["dmips_per_mhz"]       = b["dmips"] / mhz
    b["dhry_ipc"]            = b["dhry_stat_instret"] / b["dhry_stat_cycles"]
    b["dhry_secs"]           = b["dhry_cycles"] / b["clk_hz"]

    b["coremark"]            = b["cm_iterations"] * b["clk_hz"] / b["cm_cycles"]
    b["coremark_per_mhz"]    = b["coremark"] / mhz
    b["cm_ipc"]              = b["cm_instret"] / b["cm_cycles"]
    b["cm_secs"]             = b["cm_cycles"] / b["clk_hz"]

    # A scalar in-order pipeline retires at most one instruction per cycle.
    # IPC above 1 is not an optimistic result, it is a broken counter -- and
    # minstret is counted in EX on this core, which is exactly the kind of place
    # that goes wrong.  See the header of rtl/core/rvntt_csr.sv.
    for name in ("dhry_ipc", "cm_ipc"):
        if not (0.0 < b[name] <= 1.0):
            raise Fail("block %d: %s = %.4f -- impossible for a single-issue "
                       "in-order core; minstret or mcycle is wrong"
                       % (b["index"], name, b[name]))

    if not args.allow_short and b["cm_secs"] < 10.0:
        raise Fail("block %d: CoreMark ran %.2f s; its run rules require at "
                   "least 10 s.  Raise ITERATIONS (--allow-short to override "
                   "for a calibration run)." % (b["index"], b["cm_secs"]))
    return b


def check_reproducible(blocks, args):
    """The plan asks for results reproducible across three runs.  On this SoC
    there is no cache, no DRAM, no interrupt source and no other master, so
    consecutive blocks should be EXACTLY equal -- not merely close.  Requiring
    equality makes any source of variation a failure that has to be explained
    rather than averaged away."""
    if len(blocks) < 2:
        return {}
    keys = ("dhry_cycles", "dhry_stat_cycles", "dhry_stat_instret",
            "cm_cycles", "cm_instret", "crcfinal")
    spread = {}
    for k in keys:
        vals = [b[k] for b in blocks]
        lo, hi = min(vals), max(vals)
        spread[k] = {"min": lo, "max": hi,
                     "ppm": 0.0 if lo == 0 else (hi - lo) * 1e6 / lo}
        spread[k]["range"] = hi - lo
        if hi != lo and spread[k]["ppm"] > args.tolerance_ppm:
            raise Fail("%s varies across %d blocks by %d (%.4f ppm, limit "
                       "%.4f): %s" % (k, len(blocks), hi - lo, spread[k]["ppm"],
                                      args.tolerance_ppm, vals))
    return spread


def report(blocks, spread, args):
    b = blocks[0]
    out = []
    out.append("blocks parsed        : %d (iter %s)"
               % (len(blocks), ", ".join(str(x["index"]) for x in blocks)))
    out.append("flags                : %s" % b["flags"])
    out.append("CoreMark config      : %s  (seedcrc 0x%04x)"
               % (b["cm_config"], b["seedcrc"]))
    out.append("CoreMark CRCs        : list 0x%04x  matrix 0x%04x  state 0x%04x"
               "  final 0x%04x"
               % (b["crclist"], b["crcmatrix"], b["crcstate"], b["crcfinal"]))
    if args.functional_only:
        out.append("timing               : NOT MEASURED (host functional run)")
        return "\n".join(out)
    out.append("clock                : %.6f MHz" % (b["clk_hz"] / 1e6))
    out.append("")
    out.append("Dhrystone  runs      : %d in %d cycles (%.2f s, %.1f cycles/run)"
               % (b["dhry_runs"], b["dhry_cycles"], b["dhry_secs"],
                  b["dhry_cycles_per_run"]))
    out.append("           Dhry/sec  : %.1f" % b["dhrystones_per_sec"])
    out.append("           DMIPS     : %.4f" % b["dmips"])
    out.append("           DMIPS/MHz : %.4f" % b["dmips_per_mhz"])
    out.append("           instret   : %d  ->  IPC %.4f"
               % (b["dhry_stat_instret"], b["dhry_ipc"]))
    out.append("           timer skew: %d cycles between Dhrystone's window and "
               "setStats's" % b["dhry_window_skew_cycles"])
    out.append("")
    out.append("CoreMark   iterations: %d in %d cycles (%.2f s)"
               % (b["cm_iterations"], b["cm_cycles"], b["cm_secs"]))
    out.append("           CoreMark  : %.4f iterations/s" % b["coremark"])
    out.append("           CoreMark/MHz: %.4f" % b["coremark_per_mhz"])
    out.append("           instret   : %d  ->  IPC %.4f"
               % (b["cm_instret"], b["cm_ipc"]))
    if spread:
        worst = max(spread.values(), key=lambda s: s["ppm"])
        out.append("")
        out.append("reproducibility      : %d blocks, worst spread %d counts "
                   "(%.4f ppm)" % (len(blocks), worst["range"], worst["ppm"]))
    return "\n".join(out)


def check(text, args):
    blocks = split_blocks(text)
    if not blocks:
        raise Fail("no complete `%s` ... `%s` block in the capture" % (BEGIN, END))
    if len(blocks) < args.min_blocks:
        raise Fail("only %d complete block(s); --min-blocks %d required"
                   % (len(blocks), args.min_blocks))

    parsed = []
    for i, lines in enumerate(blocks):
        b = parse_block(lines, i, args)
        if b["host"] and not args.functional_only:
            raise Fail("block %d carries `host=1`: this capture came from the "
                       "build host, not from hardware, and has no cycle counts. "
                       "It cannot be reported as a measurement." % i)
        if not b["host"] and args.functional_only:
            raise Fail("block %d has no `host=1` but --functional-only was "
                       "given: refusing to discard timing from a real capture"
                       % i)
        if not args.functional_only:
            derive(b, args)
        parsed.append(b)

    spread = {} if args.functional_only else check_reproducible(parsed, args)
    return parsed, spread


# ---------------------------------------------------------------------------
# Fault injection.  Every check above is exercised by breaking exactly the thing
# it watches; a check that cannot be made to fail is not a check.
# ---------------------------------------------------------------------------
GOOD_CM = """2K performance run parameters for coremark.
CoreMark Size    : 666
Total ticks      : 733000000
Total time (secs): 10
Iterations/Sec   : 35
Iterations       : 350
Compiler version : GCC15.2.0
Compiler flags   : -march=rv32i_zicsr -mabi=ilp32 -O2
Memory location  : BRAM
seedcrc          : 0xe9f5
[0]crclist       : 0xe714
[0]crcmatrix     : 0x1fd7
[0]crcstate      : 0x8e3a
[0]crcfinal      : 0x33ff
Correct operation validated. See README.md for run and reporting rules.
"""


def good_block(iter_no=0):
    return (BEGIN + "\n"
            "iter=0x%08x\n" % iter_no +
            "clk_hz=70129870\n"
            "flags=-march=rv32i_zicsr -mabi=ilp32 -O2\n"
            "--- dhrystone ---\n"
            "dhry_runs=50000\n"
            "dhry_cycles=180000000\n"
            "dhry_stat_cycles=180000040\n"
            "dhry_stat_instret=153000000\n"
            "dhry_check=0x00000000\n"
            "--- coremark ---\n" + GOOD_CM +
            "cm_iterations=350\n"
            "cm_cycles=733000000\n"
            "cm_instret=620000000\n"
            + END + "\n")


def selftest() -> int:
    class A:
        dhry_runs = None; iterations = None; allow_short = False
        functional_only = False; min_blocks = 1; tolerance_ppm = 0.0
    base = good_block(0) + good_block(1) + good_block(2)

    ok, _ = check(base, A())
    if len(ok) != 3:
        print("SELFTEST FAIL: the good capture did not parse as 3 blocks")
        return 1

    faults = [
        # Cut before the first terminator, so NO block is complete -- half a
        # capture that still contains a whole block is legitimate and must not
        # fail, which is why this truncates to less than one.
        ("truncated",        lambda t: t[:t.index(END)]),
        ("no_end_marker",    lambda t: t.replace(END, "=== end ===")),
        ("dhry_check_set",   lambda t: t.replace("dhry_check=0x00000000",
                                                 "dhry_check=0x00000040")),
        ("crc_error",        lambda t: t.replace(
            "[0]crclist",
            "[0]ERROR! list crc 0x0000 - should be 0xe714\n[0]crclist")),
        ("no_seedcrc",       lambda t: t.replace("seedcrc          : 0xe9f5", "")),
        ("wrong_seedcrc",    lambda t: t.replace("0xe9f5", "0x7b05")),
        ("no_crcfinal",      lambda t: re.sub(r"^\[0\]crcfinal.*$", "", t, flags=re.M)),
        ("cycles_zero",      lambda t: t.replace("cm_cycles=733000000",
                                                 "cm_cycles=0")),
        ("ipc_above_one",    lambda t: t.replace("cm_instret=620000000",
                                                 "cm_instret=800000000")),
        # Both counters scale together, so this is a SHORT run and not also an
        # impossible IPC -- otherwise it would be caught by the wrong check and
        # the 10-second rule would never be exercised.
        ("coremark_short",   lambda t: t.replace("cm_cycles=733000000",
                                                 "cm_cycles=70000000")
                                        .replace("cm_instret=620000000",
                                                 "cm_instret=59000000")),
        ("blocks_disagree",  lambda t: t.replace("cm_cycles=733000000",
                                                 "cm_cycles=733000001", 1)),
        ("host_marker",      lambda t: t.replace("clk_hz=70129870",
                                                 "clk_hz=70129870\nhost=1")),
        ("empty_flags",      lambda t: t.replace(
            "flags=-march=rv32i_zicsr -mabi=ilp32 -O2", "flags=")),
        ("no_flags",         lambda t: t.replace(
            "flags=-march=rv32i_zicsr -mabi=ilp32 -O2\n", "")),
        ("stat_window_short", lambda t: t.replace("dhry_stat_cycles=180000040",
                                                  "dhry_stat_cycles=179000000")),
        ("stat_window_wide", lambda t: t.replace("dhry_stat_cycles=180000040",
                                                 "dhry_stat_cycles=181000000")),
        ("clk_zero",         lambda t: t.replace("clk_hz=70129870", "clk_hz=0")),
        ("mangled_number",   lambda t: t.replace("dhry_cycles=180000000",
                                                 "dhry_cycles=18000?000")),
    ]
    bad = 0
    for name, mutate in faults:
        try:
            check(mutate(base), A())
        except Fail as e:
            print("  caught %-19s : %s" % (name, str(e).split(" -- ")[0][:72]))
            continue
        except Exception as e:                       # noqa: BLE001
            print("  caught %-19s : %s: %s" % (name, type(e).__name__, e))
            continue
        print("  ESCAPED %-18s : the parser accepted a broken capture" % name)
        bad += 1

    # ... and the two flags that are supposed to RELAX a check must actually
    # relax it, or the escape hatch is a second way to pass vacuously.
    class Short(A):
        allow_short = True
    short = (base.replace("cm_cycles=733000000", "cm_cycles=70000000")
                 .replace("cm_instret=620000000", "cm_instret=59000000"))
    try:
        check(short, Short())
    except Fail as e:
        print("  ESCAPED --allow-short      : still failed: %s" % e)
        bad += 1
    else:
        print("  caught --allow-short       : short run accepted, as intended")

    class Host(A):
        functional_only = True
    try:
        check(base, Host())
    except Fail:
        print("  caught --functional-only   : refuses a capture with no host=1")
    else:
        print("  ESCAPED --functional-only  : accepted a real capture as a host one")
        bad += 1

    print("SELFTEST %s: %d/%d injected faults caught"
          % ("OK" if bad == 0 else "FAIL", len(faults) + 2 - bad, len(faults) + 2))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--min-blocks", type=int, default=1)
    ap.add_argument("--dhry-runs", type=int, default=None)
    ap.add_argument("--iterations", type=int, default=None)
    ap.add_argument("--allow-short", action="store_true",
                    help="accept a CoreMark run under 10 s (calibration only)")
    ap.add_argument("--functional-only", action="store_true",
                    help="host run: check CRCs and Dhrystone values, no timing")
    ap.add_argument("--tolerance-ppm", type=float, default=0.0,
                    help="permitted spread between blocks; 0 means exact")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.capture:
        ap.error("a capture file is required (or --selftest)")

    with open(a.capture, "rb") as f:
        text = f.read().decode("utf-8", "replace")

    try:
        blocks, spread = check(text, a)
    except Fail as e:
        print("BENCH_FAIL: %s" % e)
        return 1

    print(report(blocks, spread, a))
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as f:
            json.dump({"blocks": blocks, "spread": spread}, f, indent=2)
        print("wrote %s" % a.json)
    print("BENCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
