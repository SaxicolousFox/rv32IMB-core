#!/usr/bin/env python3
"""
An independent cycle model for the pipeline, and why one is needed at all.

A commit-log diff proves the pipeline computes the right values in the right
order.  It cannot see TIMING.  A stall that should not have happened changes no
architectural state, so a program with a phantom stall on every LUI produces a
byte-identical log and runs measurably slower -- and the first symptom is an IPC
number that disagrees with the LLVM scheduling model, at a point in the project
where nothing points back to the interlock.  Plan A7 asks for exactly this
guarantee ("a stalled cycle must not retire an instruction"); A9 will bind it to
`minstret`, but the property is checkable now and cheaper to keep honest from
the start than to reconstruct later.

WHAT IS PREDICTED.  The SPAN: the cycle distance from the first retirement to
the last.  Using the span rather than a total cycle count means the model does
not need to know how long reset is held or how many cycles the pipeline takes to
fill -- both are constants that cancel, and both are properties of the testbench
rather than of the design.

    span = (retired - 1) + stalls + flush_penalties

  * `retired - 1` because a five-stage pipeline with no hazards retires one
    instruction per cycle once it is full.
  * `stalls`: one cycle for each load-use hazard at distance 1 (plan A7).
  * `flush_penalties`: two cycles for each instruction that redirects the PC,
    because the branch resolves in EX with two younger instructions in flight
    (plan A8).

WHERE THE INDEPENDENCE COMES FROM.  The hazards are found by decoding the
retired instruction stream with `model/rv32i_ref.py`, which is derived from the
ISA spec and is the same frozen model the RTL decoder is checked against -- not
from the RTL, and not from `rvntt_hazard.sv`'s own predicate.  Redirects are not
decoded at all: an instruction redirected if and only if the next retired pc is
not its own plus four, which is a property of the trace rather than of any
decoder.

The stream itself is the DYNAMIC one, taken from the commit log, so the model
works unchanged once branches exist: it sees the instructions that actually
executed, in the order they executed.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
import rv32i_ref     # noqa: E402


def analyse(records):
    """
    `records` is the commit stream as (pc, insn, writes) triples.

    Returns a dict with the predicted span and its parts, plus the pcs where
    each stall and each redirect was predicted -- which is what makes a
    disagreement debuggable rather than just a number that is off by three.
    """
    n = len(records)
    stalls, flushes = [], []

    for i in range(n - 1):
        pc, insn, _ = records[i]
        nxt_pc, nxt_insn, _ = records[i + 1]

        ctrl, regs = rv32i_ref.decode(insn)

        if ctrl["mem_read"] and regs["rd"] != 0:
            nctrl, nregs = rv32i_ref.decode(nxt_insn)
            if ((nctrl["uses_rs1"] and nregs["rs1"] == regs["rd"]) or
                    (nctrl["uses_rs2"] and nregs["rs2"] == regs["rd"])):
                stalls.append(pc)

        # A redirect is normally visible in the trace itself: the next
        # instruction to retire is not the one at pc+4.  That is deliberately
        # decoder-free, so a decoder that is wrong about which opcodes branch
        # cannot hide a flush.
        #
        # The one shape it misses is an unconditional jump whose target happens
        # to BE pc+4.  The pipeline redirects and flushes for every jump without
        # checking, so that costs two cycles while looking like straight-line
        # flow -- hence the `jump` term.  A taken BRANCH to pc+4 has the same
        # shape and is not covered; it is not emitted by the generator (targets
        # are at least three instructions ahead) and would be a strange thing to
        # write by hand.
        if nxt_pc != (pc + 4) & 0xFFFFFFFF or ctrl["jump"]:
            flushes.append(pc)

    return {
        "retired": n,
        "stalls": len(stalls),
        "flushes": len(flushes),
        "stall_pcs": stalls,
        "flush_pcs": flushes,
        "span": (n - 1) + len(stalls) + 2 * len(flushes),
    }


def explain(pred, actual_span, limit=6):
    """A report for a span that does not match, naming where the cycles went."""
    lines = [
        "  predicted span %d, measured %d  (difference %+d)"
        % (pred["span"], actual_span, actual_span - pred["span"]),
        "    %d retired -> %d baseline cycles" % (pred["retired"],
                                                  pred["retired"] - 1),
        "    %d load-use stall(s)  x1 cycle" % pred["stalls"],
        "    %d redirect(s)        x2 cycles" % pred["flushes"],
    ]
    if pred["stall_pcs"]:
        shown = ", ".join("0x%08x" % p for p in pred["stall_pcs"][:limit])
        more = "" if len(pred["stall_pcs"]) <= limit else ", ..."
        lines.append("    stalls predicted after: " + shown + more)
    if pred["flush_pcs"]:
        shown = ", ".join("0x%08x" % p for p in pred["flush_pcs"][:limit])
        more = "" if len(pred["flush_pcs"]) <= limit else ", ..."
        lines.append("    redirects predicted at: " + shown + more)
    lines.append("    A span that is too LARGE means the pipeline stalled where "
                 "the model did not:")
    lines.append("      a phantom stall -- most likely a hazard predicate "
                 "matching a register field")
    lines.append("      that is not a source.  Too SMALL means a hazard was "
                 "missed, and the commit")
    lines.append("      log should have diverged too.")
    return "\n".join(lines)
