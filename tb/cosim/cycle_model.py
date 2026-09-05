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

    span = (retired - 1) + stalls + flush_penalties + muldiv_stalls

Once A19 lands, `flush_penalties` is `2 x mispredicts` rather than
`2 x redirects`, and which one this function uses is the `predictor` argument.
The predictor itself comes from `model/bpred.py`, which implements
`docs/a19-bpred-spec.md` and reads nothing out of the RTL -- MODS_A section 3.3
names that as the one way this model can quietly stop being a check.

  * `retired - 1` because a five-stage pipeline with no hazards retires one
    instruction per cycle once it is full.
  * `stalls`: one cycle for each load-use hazard at distance 1 (plan A7).
  * `flush_penalties`: two cycles for each instruction that redirects the PC,
    because the branch resolves in EX with two younger instructions in flight
    (plan A8).
  * `muldiv_stalls`: `occupancy - 1` for each M instruction (MODS_A A14), which
    holds EX for as many cycles as its unit needs and bubbles EX/MEM on each of
    the others.  This term is only predictable because the latency is DATA-
    INDEPENDENT: the divider runs its 32 iterations whatever the operands are,
    so the cost is a property of the opcode and the model can read it off the
    retired stream like everything else here.  An early-out divider would be
    free performance and would make this model unbuildable.

    The latency numbers live in `model/rv32i_ref.MULDIV_CYCLES`, duplicated from
    `rv32i_pkg.sv` and compared by `check_pkg_agreement()` -- not read out of the
    RTL, which would make the prediction agree with the pipeline by construction.

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

    # A multi-cycle instruction's bubbles sit BEFORE its own retirement, which
    # is the opposite of a load-use stall (attributed to the load, paid by its
    # consumer) and of a flush (attributed to the branch, paid by its
    # successor).  So the last retired instruction's extra cycles DO count --
    # they delayed the retirement the span ends at -- and the FIRST one's do
    # not, because they happened before the span began.  Hence range(1, n).
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

    # ---- A19: the same span, with a predictor in front of the fetch ---------
    # MODS_A section 3.3 is explicit that after A19 the flush term stops being
    # `2 x redirects` and becomes `2 x MISPREDICTS`, and that the model has to
    # know the difference from the predictor's SPECIFICATION rather than from
    # its RTL.  model/bpred.py is that specification -- docs/a19-bpred-spec.md
    # implemented from the document -- and it is driven here off the same
    # retired stream everything else in this file reads.
    #
    # THE ONE THING THAT CANNOT BE READ OFF THE STREAM ALONE is *when* an
    # update becomes visible.  A predictor update lands in EX and cannot reach
    # a lookup that already happened, so a transfer fewer than four retire
    # cycles after the one that would have taught the predictor about it sees
    # the old state -- spec section 8.  That is not a detail: on a
    # three-instruction loop it is every other iteration, and ignoring it made
    # this model predict 916 cycles for sw/tests/a19_bpred.S where the RTL
    # measured 992.
    #
    # So this walk is CYCLE-ACCURATE rather than instruction-ordered: it
    # accumulates the same span the return value reports, one instruction at a
    # time, and hands each transfer's retire cycle to DelayedBPred.  The
    # dependency is feed-forward -- a mispredict costs two cycles, which pushes
    # later transfers further from their updates, which can only make more of
    # them visible -- so one pass is exact.
    #
    # A jump whose target happens to be pc + 4 is taken, and the trace cannot
    # say so; the decoder can, and does, for the same reason the flush term
    # above has a `jump` clause.
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
            # THE RULE IS ON FETCH CYCLES, NOT RETIREMENTS.  `retire - fetch` is
            # not constant: an instruction held in ID by a load-use interlock or
            # behind a multi-cycle EX has already had its prediction made.  See
            # model/bpred.py's VISIBILITY_GAP -- expressing this on retirements
            # was wrong on 39 of Dhrystone's 396 mispredicts, and right on every
            # one of CoreMark's, which is exactly how it hid.
            # A REDIRECT TARGET IS NOT PREDICTED.  The lookup reads only
            # registered sources, so during the cycle a redirect fires the
            # predictor is looking at the address the front end would otherwise
            # have fetched -- not at the target.  See model/bpred.py's
            # SUPPRESS_AFTER_REDIRECT and docs/a19-bpred-spec.md section 2.  The
            # cycle model can apply this exactly, because unlike a transfer
            # trace it sees EVERY instruction and therefore knows whether this
            # one is the redirect's successor.
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
