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

    # A20's Zihpm counters, if this image carries them.  OPTIONAL, and for
    # exactly the reason the dual baseline below is: A13's, A16's and A19's
    # images do not have them, and their captures must keep parsing unchanged.
    # Present or absent as a GROUP -- a capture carrying three of the six is a
    # truncated or corrupted capture, not a partial feature, and saying so here
    # is cheaper than discovering it as a KeyError three functions away.
    hpm_names = ("loaduse", "exstall", "redirect",
                 "mispredict", "btbhit", "xfertaken")
    for region in ("dhry", "cm"):
        keys = ["%s_hpm_%s" % (region, n) for n in hpm_names]
        present = [k for k in keys if k in d]
        if present and len(present) != len(keys):
            raise Fail("block %d: %d of the %d %s_hpm_* counters are present "
                       "(%s) -- a capture with some of the group is truncated, "
                       "not a different build"
                       % (idx, len(present), len(keys), region,
                          ", ".join(sorted(set(keys) - set(present)))))
        for k in keys:
            if k in d:
                b[k] = num(d, k, idx)
    b["has_hpm"] = "dhry_hpm_loaduse" in b

    # A16's dual baseline, if this image carries it.  OPTIONAL, because A13's
    # image does not have it and its captures must keep parsing unchanged -- the
    # RV32I numbers are preserved rather than overwritten (MODS_A A16), and a
    # parser that could no longer read them would defeat that.
    b["has_ntt"] = "ntt_cycles_rv32i" in d
    if b["has_ntt"]:
        for k in ("ntt_cycles_rv32i", "ntt_instret_rv32i",
                  "ntt_cycles_rv32im", "ntt_instret_rv32im",
                  "ntt_check", "ntt_sum"):
            b[k] = num(d, k, idx)
        # THE TWO BUILDS MUST HAVE COMPUTED THE SAME POLYNOMIAL.  Without this
        # the "ratio" could be between two different transforms -- a -march that
        # changed the arithmetic, or a symbol rename that left one variant
        # calling the other's helpers, would read as a speedup rather than as an
        # error.  ntt_check counts differing coefficients out of 256.
        if b["ntt_check"] != 0:
            raise Fail("block %d: the rv32i and rv32im NTT builds disagree on "
                       "%d of 256 coefficients -- they are not computing the "
                       "same transform, so the ratio below would be meaningless"
                       % (idx, b["ntt_check"]))
        if b["ntt_sum"] == 0:
            raise Fail("block %d: ntt_sum is zero -- the transform produced an "
                       "all-zero polynomial, which the seed cannot" % idx)

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

    if b.get("has_ntt"):
        for k in ("ntt_cycles_rv32i", "ntt_instret_rv32i",
                  "ntt_cycles_rv32im", "ntt_instret_rv32im"):
            if b[k] <= 0:
                raise Fail("block %d: %s=%d -- the counter did not advance"
                           % (b["index"], k, b[k]))
        b["ntt_cycle_ratio"]   = b["ntt_cycles_rv32i"] / b["ntt_cycles_rv32im"]
        b["ntt_instret_ratio"] = b["ntt_instret_rv32i"] / b["ntt_instret_rv32im"]
        b["ntt_ipc_rv32i"]  = b["ntt_instret_rv32i"] / b["ntt_cycles_rv32i"]
        b["ntt_ipc_rv32im"] = b["ntt_instret_rv32im"] / b["ntt_cycles_rv32im"]
        for name in ("ntt_ipc_rv32i", "ntt_ipc_rv32im"):
            if not (0.0 < b[name] <= 1.0):
                raise Fail("block %d: %s = %.4f -- impossible for a "
                           "single-issue in-order core"
                           % (b["index"], name, b[name]))
        # The rv32im build must be FASTER.  If it is not, either the M unit is
        # not being selected or the rename crossed the two variants over, and
        # both look like a plausible number rather than like an error.
        if b["ntt_cycle_ratio"] <= 1.0:
            raise Fail("block %d: the rv32im NTT is not faster than the rv32i "
                       "one (%d vs %d cycles).  Either the multiplier is not "
                       "being used or the two builds are crossed over."
                       % (b["index"], b["ntt_cycles_rv32im"],
                          b["ntt_cycles_rv32i"]))

    if not args.allow_short and b["cm_secs"] < 10.0:
        raise Fail("block %d: CoreMark ran %.2f s; its run rules require at "
                   "least 10 s.  Raise ITERATIONS (--allow-short to override "
                   "for a calibration run)." % (b["index"], b["cm_secs"]))
    return b


def check_reproducible(blocks, args):
    """What "reproducible" means on a machine with a branch predictor.

    Before A19 this required consecutive report blocks to be EXACTLY equal --
    no cache, no DRAM, no interrupt source, no other master, so any variation
    was a failure to be explained rather than averaged away.  A19 added the
    first piece of state that survives a timed region: the BTB and the return
    stack are not cleared between blocks, so block 1 runs on a cold predictor
    and later blocks run on progressively warmer ones.  Dhrystone settles after
    one block; CoreMark, with 216 distinct transfer sites and data-dependent
    branches, was still moving by 4 cycles in 833,259 between blocks 2 and 3.

    The wrong fix is a tolerance.  A tolerance would have absorbed this and the
    next source of variation with it, which is the exact failure the original
    comment was written against.  So the check is SPLIT instead:

      ARCHITECTURAL keys -- instruction counts, CRCs, checksums -- must be
      EXACTLY equal across every block, cold one included.  The predictor cannot
      touch them, and any variation there is a real bug.

      TIMING keys -- cycle counts -- have their spread MEASURED and reported,
      and are not required to be equal within one capture, because the machine
      genuinely carries state between blocks now.

    The exact-determinism claim does not disappear; it moves to where it is
    still true and is now stronger for it.  `tb/fpga/compare_bench_runs.py`
    requires every cycle count to be identical between PROGRAMMING PASSES, which
    is the claim the plan actually asks for -- "reproducible across three runs"
    -- and which a within-capture comparison never tested.
    """
    if len(blocks) < 2:
        return {}

    arch = ["dhry_stat_instret", "cm_instret", "crcfinal", "crclist",
            "crcmatrix", "crcstate", "dhry_check", "dhry_runs", "cm_iterations"]
    time = ["dhry_cycles", "dhry_stat_cycles", "cm_cycles"]
    if blocks[0].get("has_ntt"):
        arch += ["ntt_instret_rv32i", "ntt_instret_rv32im", "ntt_sum", "ntt_check"]
        time += ["ntt_cycles_rv32i", "ntt_cycles_rv32im"]

    for k in arch:
        vals = [b[k] for b in blocks if k in b]
        if vals and min(vals) != max(vals):
            raise Fail("ARCHITECTURAL key %s varies across %d blocks: %s -- the "
                       "predictor cannot change this, so something else did"
                       % (k, len(blocks), vals))

    cold = blocks[0] if args.warmup_blocks else None
    warm = blocks[args.warmup_blocks:] if args.warmup_blocks else blocks
    if len(warm) < 2:
        # A CHECK THAT CANNOT FAIL IS NOT A CHECK.  Fewer than two blocks left
        # after discarding warm-up means the caller captured fewer than it asked
        # for, and reporting "no variation" would be a vacuous pass.
        raise Fail("only %d block(s) left after discarding %d warm-up block(s); "
                   "at least 2 are needed to compare"
                   % (len(warm), args.warmup_blocks))

    spread = {}
    for k in time:
        vals = [b[k] for b in warm if k in b]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        spread[k] = {"min": lo, "max": hi, "range": hi - lo,
                     "ppm": 0.0 if lo == 0 else (hi - lo) * 1e6 / lo}
        if hi != lo and spread[k]["ppm"] > args.tolerance_ppm:
            raise Fail("%s varies across %d warm blocks by %d (%.4f ppm, limit "
                       "%.4f): %s" % (k, len(warm), hi - lo, spread[k]["ppm"],
                                      args.tolerance_ppm, vals))

    # The warm-up cost, reported rather than absorbed.  It is what a cold branch
    # predictor costs the first time through -- and it is also the size of the
    # timing signal left behind for whatever runs next, which is a security
    # property and not only a performance one.
    if cold is not None:
        spread["_warmup"] = {k: cold[k] - warm[0][k]
                             for k in time if k in cold and k in warm[0]}
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
    if b.get("has_ntt"):
        out.append("")
        out.append("ML-KEM NTT (the dual baseline -- one core, one clock, one run)")
        out.append("           rv32i     : %d cycles, %d instructions  (IPC %.4f)"
                   % (b["ntt_cycles_rv32i"], b["ntt_instret_rv32i"],
                      b["ntt_ipc_rv32i"]))
        out.append("           rv32im    : %d cycles, %d instructions  (IPC %.4f)"
                   % (b["ntt_cycles_rv32im"], b["ntt_instret_rv32im"],
                      b["ntt_ipc_rv32im"]))
        out.append("           ratio     : %.3fx cycles, %.3fx instructions"
                   % (b["ntt_cycle_ratio"], b["ntt_instret_ratio"]))
        out.append("           agreement : all 256 coefficients identical "
                   "(sum 0x%08x)" % b["ntt_sum"])
    warmup = spread.pop("_warmup", None) if spread else None
    if warmup:
        moved = {k: v for k, v in warmup.items() if v}
        out.append("warm-up (block 1 vs 2): " +
                   (", ".join("%s %+d" % (k, v) for k, v in moved.items())
                    if moved else "no difference"))
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


# A16's section, as the board prints it.  The numbers are the shape A13's Spike
# measurement predicts -- 148,645 instructions against 23,795 -- so a selftest
# fault that inverts the ratio has something realistic to invert.
NTT_SECTION = (
    "--- ntt ---\n"
    "ntt_cycles_rv32i=201000\n"
    "ntt_instret_rv32i=148645\n"
    "ntt_cycles_rv32im=33000\n"
    "ntt_instret_rv32im=23795\n"
    "ntt_check=0x00000000\n"
    "ntt_sum=0x5a5a1234\n")


def good_block(iter_no=0, ntt=False):
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
            + (NTT_SECTION if ntt else "")
            + END + "\n")


def selftest() -> int:
    class A:
        dhry_runs = None; iterations = None; allow_short = False
        functional_only = False; min_blocks = 1; tolerance_ppm = 0.0
        # A19.  The selftest's captures are synthetic and identical, so no
        # warm-up block is discarded here -- the cold/warm split is a property
        # of the machine, not of the parser, and the parser's own checks are
        # what this exercises.
        warmup_blocks = 0
    base = good_block(0) + good_block(1) + good_block(2)

    ok, _ = check(base, A())
    if len(ok) != 3:
        print("SELFTEST FAIL: the good capture did not parse as 3 blocks")
        return 1
    if ok[0]["has_ntt"]:
        print("SELFTEST FAIL: a block with no `--- ntt ---` was read as having one")
        return 1

    # A16's section is OPTIONAL, so it needs its own good capture as well as its
    # own faults: a parser that silently ignored the whole section would pass
    # every fault below on the base capture and every check above on this one.
    ntt_base = "".join(good_block(i, ntt=True) for i in range(3))
    ok, _ = check(ntt_base, A())
    if len(ok) != 3 or not ok[0]["has_ntt"]:
        print("SELFTEST FAIL: the dual-baseline capture did not parse")
        return 1
    if abs(ok[0]["ntt_instret_ratio"] - 148645 / 23795) > 1e-9:
        print("SELFTEST FAIL: ntt_instret_ratio came out as %r"
              % ok[0]["ntt_instret_ratio"])
        return 1

    faults = [
        # A19 SPLIT THE REPRODUCIBILITY CHECK, so both halves of the split need
        # a fault of their own.  A branch predictor carries state between report
        # blocks, so cycle counts may legitimately differ; instruction counts
        # and CRCs may NOT, because the predictor cannot touch them.  Without
        # these two the split would be a comment rather than a check.
        # The blocks are identical, so `replace(..., count=1)` on the LAST one
        # perturbs exactly one block and leaves the others alone.
        ("arch_instret_varies",
         lambda t: t[::-1].replace("000000351=tertsni_tats_yrhd", "100000351=tertsni_tats_yrhd", 1)[::-1]),
        ("timing_key_varies",
         lambda t: t[::-1].replace("000000081=selcyc_yrhd", "100000081=selcyc_yrhd", 1)[::-1]),
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
    # A16's section has its own faults, injected into the capture that HAS one.
    # Aimed at the four things that could turn a non-measurement into a
    # plausible ratio.
    ntt_faults = [
        # The two builds computed different polynomials: the ratio would be
        # between two different transforms.
        ("ntt_disagree",     lambda t: t.replace("ntt_check=0x00000000",
                                                 "ntt_check=0x00000007")),
        # An all-zero result: a multiplier that returns zero is fast and wrong,
        # and the two builds would agree perfectly about it.
        ("ntt_all_zero",     lambda t: t.replace("ntt_sum=0x5a5a1234",
                                                 "ntt_sum=0x00000000")),
        # The two variants crossed over, or M never selected.  Either way the
        # rv32im build is not faster and the headline ratio is upside down.
        ("ntt_not_faster",   lambda t: t.replace("ntt_cycles_rv32im=33000",
                                                 "ntt_cycles_rv32im=250000")),
        # A counter that did not advance.
        ("ntt_zero_cycles",  lambda t: t.replace("ntt_cycles_rv32i=201000",
                                                 "ntt_cycles_rv32i=0")),
        # IPC above 1 in the NTT section specifically -- the Dhrystone and
        # CoreMark checks cannot see this one.
        ("ntt_ipc_above_one", lambda t: t.replace("ntt_instret_rv32im=23795",
                                                  "ntt_instret_rv32im=40000")),
        # Blocks disagree in the NTT section only, which the existing
        # reproducibility keys would not have looked at.
        ("ntt_blocks_differ", lambda t: t.replace("ntt_cycles_rv32im=33000",
                                                  "ntt_cycles_rv32im=33001", 1)),
    ]

    bad = 0
    for name, mutate in ntt_faults:
        try:
            check(mutate(ntt_base), A())
        except Fail as e:
            print("  caught %-19s : %s" % (name, str(e).split(" -- ")[0][:72]))
            continue
        except Exception as e:                       # noqa: BLE001
            print("  caught %-19s : %s: %s" % (name, type(e).__name__, e))
            continue
        print("  ESCAPED %-18s : the parser accepted a broken capture" % name)
        bad += 1

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

    total = len(faults) + len(ntt_faults) + 2
    print("SELFTEST %s: %d/%d injected faults caught"
          % ("OK" if bad == 0 else "FAIL", total - bad, total))
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
    ap.add_argument("--warmup-blocks", type=int, default=0,
                    help="discard this many leading blocks before comparing; "
                         "1 after A19, whose predictor is cold in the first")
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
