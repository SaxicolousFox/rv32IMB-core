#!/usr/bin/env python3
"""
An independent cycle model for the pipeline.

A commit-log diff proves the pipeline computes the right values in the right
order; it cannot see timing, because a phantom stall changes no architectural
state.  This predicts the SPAN -- the cycle distance from the first retirement
to the last, so reset length and pipeline fill cancel:

    span = (retired - 1) + stalls + flush_penalties + muldiv_stalls

  * `retired - 1`: a full five-stage pipeline retires one instruction per cycle.
  * `stalls`: one cycle per load-use hazard at distance 1.
  * `flush_penalties`: two cycles per redirect (the branch resolves in EX with
    two younger instructions in flight), or per mispredict with `predictor`,
    where the predictor is model/bpred.py.
  * `muldiv_stalls`: `occupancy - 1` per M instruction, predictable only
    because the latency is data-independent.  The numbers live in
    model/rv32i_ref.MULDIV_CYCLES, duplicated from rv32i_pkg.sv and compared.

Hazards are found by decoding the retired (dynamic) stream with
model/rv32i_ref.py, not with the RTL's predicate; a redirect is an
instruction whose successor is not pc+4, which needs no decoder.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
import rv32i_ref     # noqa: E402
import bpred         # noqa: E402


def analyse(records, predictor=False):
    """
    `records` is the commit stream as (pc, insn, writes) triples.

    Returns a dict with the predicted span and its parts, plus the pcs where
    each stall and each redirect was predicted -- which is what makes a
    disagreement debuggable rather than just a number that is off by three.
    """
    n = len(records)
    stalls, flushes = [], []
    muldiv_cycles = 0
    muldiv_n = 0
    mispredicts = []

    # A multi-cycle instruction's bubbles sit before its own retirement, so the
    # last instruction's extra cycles count and the first one's do not.
    for i in range(1, n):
        _pc0, insn, _ = records[i]
        ctrl, _regs0 = rv32i_ref.decode(insn)
        if ctrl["is_muldiv"]:
            muldiv_cycles += rv32i_ref.MULDIV_CYCLES[ctrl["muldiv_op"]] - 1
            muldiv_n += 1

    for i in range(n - 1):
        pc, insn, _ = records[i]
        nxt_pc, nxt_insn, _ = records[i + 1]

        ctrl, regs = rv32i_ref.decode(insn)

        if ctrl["mem_read"] and regs["rd"] != 0:
            nctrl, nregs = rv32i_ref.decode(nxt_insn)
            if ((nctrl["uses_rs1"] and nregs["rs1"] == regs["rd"]) or
                    (nctrl["uses_rs2"] and nregs["rs2"] == regs["rd"])):
                stalls.append(pc)

        # A redirect is visible in the trace itself: the next instruction is
        # not the one at pc+4.  The one shape that misses is a jump whose target
        # is pc+4, which still flushes -- hence the `jump` term.
        if nxt_pc != (pc + 4) & 0xFFFFFFFF or ctrl["jump"]:
            flushes.append(pc)

    # ---- the same span, with a predictor in front of the fetch --------------
    # With a predictor the flush term is 2 x mispredicts, decided by
    # model/bpred.py off the same retired stream.  What cannot be read off the
    # stream alone is when an update becomes visible: a transfer fewer than
    # four cycles after the one that would have taught the predictor sees the
    # old state.  So this walk is cycle-accurate, accumulating the span one
    # instruction at a time and handing each transfer's retire cycle to
    # DelayedBPred; the dependency is feed-forward, so one pass is exact.
    if predictor:
        bp = bpred.DelayedBPred()
        stall_at = set(stalls)
        cyc = 0                      # retire cycle of records[i], relative
        prev_mispredicted = False
        prev_pc = None
        prev_extra = 0
        prev_hold = 0                # cycles records[i] was held in ID
        pending_target = None        # pc of the instruction at a redirect target
        for i in range(n):
            pc, insn, _ = records[i]
            ctrl, _ = rv32i_ref.decode(insn)
            extra = (rv32i_ref.MULDIV_CYCLES[ctrl["muldiv_op"]] - 1
                     if ctrl["is_muldiv"] else 0)
            if i > 0:
                # retire(i) = retire(i-1) + 1 + load-use stall + flush + this
                # instruction's own extra EX occupancy.
                stall = 1 if prev_pc in stall_at else 0
                cyc += 1 + stall + (2 if prev_mispredicted else 0) + extra
                # ...and the cycles THIS instruction spent held in ID, which is
                # what separates its fetch from its retirement: a load-use
                # interlock holds it for one, and a multi-cycle EX instruction
                # ahead of it holds it for that instruction's extra cycles.
                prev_hold = stall + prev_extra
            prev_pc, prev_extra = pc, extra
            prev_mispredicted = False

            if not (ctrl["branch"] or ctrl["jump"]) or i == n - 1:
                # A non-transfer at the redirect target uses up the unpredicted
                # slot; the transfer after it is predicted normally.
                if pc == pending_target:
                    pending_target = None
                continue
            nxt_pc, _, _ = records[i + 1]
            taken = bool(ctrl["jump"]) or nxt_pc != (pc + 4) & 0xFFFFFFFF
            target = nxt_pc if taken else 0
            # The rule is on fetch cycles, not retirements: an instruction held
            # in ID has already had its prediction made (VISIBILITY_GAP).  A
            # redirect target is not predicted (SUPPRESS_AFTER_REDIRECT); the
            # cycle model sees every instruction, so it applies this exactly.
            suppressed = bpred.SUPPRESS_AFTER_REDIRECT and pc == pending_target
            if suppressed:
                bp.predict(pc, cyc - prev_hold)      # drain, then ignore
                p_taken, p_target = False, 0
            else:
                p_taken, p_target = bp.predict(pc, cyc - prev_hold)
            if taken:
                if not (p_taken and p_target == target):
                    mispredicts.append(pc)
                    prev_mispredicted = True
            elif p_taken:
                mispredicts.append(pc)
                prev_mispredicted = True
            bp.update(cyc - prev_hold, pc, insn, taken, target)
            pending_target = (target if taken else (pc + 4) & 0xFFFFFFFF) \
                             if prev_mispredicted else None

    control_cycles = 2 * (len(mispredicts) if predictor else len(flushes))

    return {
        "retired": n,
        "stalls": len(stalls),
        "flushes": len(flushes),
        "muldiv": muldiv_n,
        "muldiv_cycles": muldiv_cycles,
        "stall_pcs": stalls,
        "flush_pcs": flushes,
        "predictor": predictor,
        "mispredicts": len(mispredicts),
        "mispredict_pcs": mispredicts,
        "span": (n - 1) + len(stalls) + control_cycles + muldiv_cycles,
    }


def explain(pred, actual_span, limit=6):
    """A report for a span that does not match, naming where the cycles went."""
    lines = [
        "  predicted span %d, measured %d  (difference %+d)"
        % (pred["span"], actual_span, actual_span - pred["span"]),
        "    %d retired -> %d baseline cycles" % (pred["retired"],
                                                  pred["retired"] - 1),
        "    %d load-use stall(s)  x1 cycle" % pred["stalls"],
        ("    %d mispredict(s)      x2 cycles  (of %d redirect(s))"
         % (pred["mispredicts"], pred["flushes"])) if pred["predictor"] else
        ("    %d redirect(s)        x2 cycles" % pred["flushes"]),
        "    %d M instruction(s) -> %d extra EX cycle(s)"
        % (pred["muldiv"], pred["muldiv_cycles"]),
    ]
    if pred["stall_pcs"]:
        shown = ", ".join("0x%08x" % p for p in pred["stall_pcs"][:limit])
        more = "" if len(pred["stall_pcs"]) <= limit else ", ..."
        lines.append("    stalls predicted after: " + shown + more)
    key = "mispredict_pcs" if pred["predictor"] else "flush_pcs"
    if pred[key]:
        shown = ", ".join("0x%08x" % p for p in pred[key][:limit])
        more = "" if len(pred[key]) <= limit else ", ..."
        lines.append("    %s predicted at: %s%s"
                     % ("mispredicts" if pred["predictor"] else "redirects",
                        shown, more))
    lines.append("    A span that is too LARGE means the pipeline stalled where "
                 "the model did not:")
    lines.append("      a phantom stall -- most likely a hazard predicate "
                 "matching a register field")
    lines.append("      that is not a source.  Too SMALL means a hazard was "
                 "missed, and the commit")
    lines.append("      log should have diverged too.")
    return "\n".join(lines)
