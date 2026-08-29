#!/usr/bin/env python3
"""
Lockstep commit-log differ: Spike vs. the RTL, on the same ELF (plan A5).

Plan A5 calls this "the highest-return item in Track A", and the reason is that
it turns every future bug into a localised report instead of a wrong number at
the end of a program.

HOW EQUALITY IS DEFINED.  Both sides are parsed into records of
(pc, insn, [(rd, value)]) and then rendered back out through ONE formatter,
`render()`.  The comparison is on those rendered strings, so "byte-identical
commit log" is literally true -- but it cannot be defeated by a formatting
difference, and there is exactly one place to teach about a new annotation.

Three asymmetries between the two logs are handled here rather than by relaxing
the comparison, because each is a real property of Spike and would otherwise
either mask bugs or produce a spurious mismatch on line one:

  1. SPIKE'S BOOTROM.  Spike executes 5 instructions at 0x1000 before jumping
     to 0x80000000.  They are dropped by ADDRESS (pc < load address), not by a
     hardcoded count of 5, so a change in Spike's bootrom cannot silently shift
     the alignment.

  2. TRAPPING INSTRUCTIONS ARE NOT LOGGED.  Spike prints no commit line at all
     for an instruction that traps.  The ECALL that ends every test program is
     therefore absent from Spike's log while the RTL retires it.  Both sides are
     truncated at the program's stop point, and the ECALL itself is EXCLUDED
     from the RTL side -- it is the stop marker, not a compared instruction.
     Including it makes every otherwise-identical program fail with a
     one-line length difference at the very end.

  3. `mem` AND CSR ANNOTATIONS.  Spike appends `mem 0x<addr>` to loads,
     `mem 0x<addr> 0x<data>` to stores, and `c<n>_<name> 0x<val>` to CSR writes.
     The A5 format does not include them and the core has no CSR file until A9.
     `render()` drops them from both sides; teaching it about CSRs later is a
     one-place change.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
import spike_asm   # noqa: E402

BASE = 0x80000000
ECALL_WORD = 0x00000073

# The RTL monitor's own output, re-parsed rather than trusted: this proves
# rvntt_trace.sv really is emitting Spike's format, instead of the differ
# quietly accepting whatever it wrote.
RTL_LINE_RE = re.compile(
    r"^core\s+\d+:\s+\d+\s+0x([0-9a-f]{8})\s+\(0x([0-9a-f]{8})\)"
    r"(?:\s+x(\d+)\s+0x([0-9a-f]{8}))?\s*$")


def render(pc, insn, writes):
    """
    The one and only commit-line formatter.

    Spike left-justifies the register name in three columns (`x5 `, `x11`), so a
    single-digit register gets two spaces before its value.  Taken from real
    Spike output; guessing this produces a log that looks right and differs on
    every line involving x0..x9.
    """
    line = "core   0: 3 0x%08x (0x%08x)" % (pc, insn)
    for rd, val in writes:
        line += " %-3s 0x%08x" % ("x%d" % rd, val)
    return line


def spike_records(elf, isa=None):
    """Spike's commits, bootrom dropped, truncated at the trap handler."""
    isa = isa or spike_asm.ISA_BASE
    rc, trace, text = spike_asm.run(elf, isa=isa, log_commits=True)
    if not trace:
        raise RuntimeError("Spike produced no commit trace:\n" + text[-2000:])
    handler = spike_asm.symbol(elf, "trap_handler")

    out = []
    for pc, insn, writes in trace:
        if pc < BASE:                      # bootrom, by address not by count
            continue
        if handler is not None and pc == handler:
            break
        # Spike reports at most one architectural register write per commit;
        # `mem` and CSR annotations never match WRITE_RE's x<n> form, so they
        # are already excluded by spike_asm's parser.
        out.append((pc, insn, [(rd, v) for rd, v in writes if rd != 0]))
    return out


def rtl_records(path, stop_at_ecall=True):
    """
    Parse the RTL monitor's log, truncated BEFORE the ECALL it retires.

    Exclusive, not inclusive: Spike prints no commit line for a trapping
    instruction, so its log ends one entry earlier.  The ECALL is the stop
    marker, not part of the program under comparison.

    `stop_at_ecall=False` keeps it, which the cycle model needs: the ECALL is
    the last instruction the RTL retires, so it is the one whose cycle ends the
    measured span.  Dropping it there would make the prediction short by one on
    every program.
    """
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            m = RTL_LINE_RE.match(line)
            if not m:
                raise RuntimeError(
                    f"{path}:{n}: RTL trace line does not match Spike's format:\n"
                    f"  {line!r}\n"
                    "  rvntt_trace.sv and commit_diff.render() have diverged.")
            pc = int(m.group(1), 16)
            insn = int(m.group(2), 16)
            if stop_at_ecall and insn == ECALL_WORD:
                break
            writes = []
            if m.group(3) is not None:
                writes.append((int(m.group(3)), int(m.group(4), 16)))
            out.append((pc, insn, writes))
    return out


def diff(spike, rtl, context=6):
    """
    Compare two record lists.  Returns None if identical, else a report string
    naming the first divergence with surrounding context from both sides.
    """
    n = min(len(spike), len(rtl))
    first = None
    for i in range(n):
        if render(*spike[i]) != render(*rtl[i]):
            first = i
            break
    if first is None:
        if len(spike) == len(rtl):
            return None
        first = n

    lo = max(0, first - context)
    hi = min(max(len(spike), len(rtl)), first + context + 1)

    def side(recs, i):
        return render(*recs[i]) if i < len(recs) else "<end of log>"

    lines = []
    if first < n:
        lines.append(f"FIRST DIVERGENCE at commit #{first} "
                     f"(pc 0x{spike[first][0]:08x})")
    else:
        lines.append(f"LOGS AGREE for {n} commits but differ in LENGTH: "
                     f"spike {len(spike)}, rtl {len(rtl)}")
    lines.append("")
    lines.append(f"  {'':<4}  {'SPIKE':<50}  RTL")
    for i in range(lo, hi):
        s, r = side(spike, i), side(rtl, i)
        mark = "  " if s == r else ">>"
        lines.append(f"{mark}{i:<4}  {s:<50}  {r}")
    lines.append("")

    # Say what actually differs, so the reader does not have to spot it.
    if first < n:
        sp, sr = spike[first], rtl[first]
        if sp[0] != sr[0]:
            lines.append(f"  pc differs:   spike 0x{sp[0]:08x}  rtl 0x{sr[0]:08x}")
        elif sp[1] != sr[1]:
            lines.append(f"  insn differs: spike 0x{sp[1]:08x}  rtl 0x{sr[1]:08x}"
                         "  (the RTL fetched a different word at the same pc)")
        else:
            lines.append(f"  same pc and insn; the register writeback differs:")
            lines.append(f"    spike {sp[2]}")
            lines.append(f"    rtl   {sr[2]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--rtl-trace", required=True,
                    help="log file written by rvntt_trace.sv (+trace_file=...)")
    ap.add_argument("--isa", default=spike_asm.ISA_BASE)
    ap.add_argument("--context", type=int, default=6)
    a = ap.parse_args()

    sp = spike_records(a.elf, a.isa)
    rt = rtl_records(a.rtl_trace)
    report = diff(sp, rt, a.context)
    if report is None:
        print(f"COMMIT_DIFF_OK  {len(sp)} commits byte-identical")
        return 0
    print(report)
    print("COMMIT_DIFF_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
