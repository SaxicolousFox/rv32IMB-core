#!/usr/bin/env python3
"""
The branch predictor of docs/a19-bpred-spec.md, implemented from that document.

MODS_A section 3.3 asks for a cycle model that can predict a program's span WITH
mispredicts, and asks for it to be built from the predictor's specification
rather than from the predictor's RTL -- otherwise the check is a mirror.  So
this file is written against docs/a19-bpred-spec.md and against nothing else.
If it disagrees with rtl/core/rvntt_bpred.sv, the specification decides which of
them is wrong; that is the whole reason the specification was written first.

It is a pure function of the RETIRED control-transfer stream.  Two decisions in
the specification are what make that true, and both were made for this reason:

  * state changes only when a transfer RESOLVES in EX, so the wrong path -- which
    a retired trace does not contain -- never touches it (spec section 4);
  * the reset state is "every entry invalid" and nothing else, so the model needs
    to know NOTHING about cycles to start in the same state the hardware does
    (spec section 6).  An earlier draft cleared its arrays with a 256-cycle sweep
    after reset and would have needed the model to count cycles from reset to
    reproduce it.
"""

BRANCH, JUMP, CALL, RET = 0, 1, 2, 3

BTB_ENTRIES = 256
RAS_ENTRIES = 8

# HOW LONG AN UPDATE TAKES TO BECOME VISIBLE, in FETCH cycles.  Specification
# section 8, and it is arithmetic rather than a guess:
#
#   instruction i is fetched at F(i) and looked up one address ahead, at F(i)-1;
#   transfer j resolves in EX at F(j)+2 and its write is readable from F(j)+3.
#
# So j's update reaches i's lookup exactly when F(i) - F(j) >= 4.  A
# three-instruction loop fetches its branch every 3 cycles when it is predicted,
# which is inside that window -- so the second iteration of a tight loop cannot
# see what the first one learned, and mispredicts.
#
# IT MUST BE FETCH CYCLES, NOT RETIRE CYCLES, and that cost a wrong answer to
# learn.  `retire - fetch` is not a constant: a load-use interlock holds an
# instruction in ID after its prediction has already been made, so a rule
# expressed on retirements is short by exactly the number of cycles it was held.
# On Dhrystone that is 39 of 396 mispredicts -- every one of them a return into
# a callee reached through a stall -- while CoreMark, whose calls are not, agreed
# exactly and hid the bug.  tb/perf/tb_profile.cpp dates every transfer by its
# fetch for this reason.
VISIBILITY_GAP = 4

# A REDIRECT TARGET IS NOT PREDICTED, and this is a timing constraint that
# became an architectural rule.  The lookup reads only registered sources, so
# during the cycle a redirect fires the predictor is looking at the address the
# front end WOULD have fetched, not at the redirect target.  Its answer is
# therefore about the wrong address and is suppressed.
#
# It is here because the alternative was unaffordable: indexing the BTB with
# `pc_next` -- which contains ex_redirect_target, the ALU's own output -- put the
# forwarding mux, the full ALU carry chain and a 256-entry array read in one
# cycle.  Measured at 16.058 ns against A17's 11.562; the design failed 80 MHz
# by 3.886 ns.  See docs/a19-bpred-spec.md section 2.
SUPPRESS_AFTER_REDIRECT = True

# What a freshly allocated entry's counter holds.  Weakly taken, which is what
# a branch that was just observed taken deserves and is also what a separate
# weakly-not-taken PHT would have reached on the same event.
ALLOC_CNT = 2


def btb_index(pc):
    return (pc >> 2) & (BTB_ENTRIES - 1)


def btb_tag(pc):
    """Everything above the index, so index and tag together are the whole word
    address -- a tag hit is the exact instruction, never an alias."""
    return pc >> (2 + BTB_ENTRIES.bit_length() - 1)


def is_link(r):
    return r == 1 or r == 5


def classify(insn):
    """kind, from the encoding alone -- specification section 4."""
    op = insn & 0x7F
    rd = (insn >> 7) & 0x1F
    rs1 = (insn >> 15) & 0x1F
    if op == 0x63:
        return BRANCH
    if op == 0x67 and is_link(rs1) and not is_link(rd):
        return RET
    if is_link(rd):
        return CALL
    return JUMP


class BPred:
    """Entries are (tag, kind, cnt, target) or None for invalid."""

    def __init__(self):
        self.btb = [None] * BTB_ENTRIES
        self.ras = [0] * RAS_ENTRIES
        self.ras_sp = 0
        self.ras_count = 0

    # ---------------------------------------------------------- specification 3
    def predict(self, addr):
        """Return (taken, target) for a fetch of `addr`."""
        e = self.btb[btb_index(addr)]
        if e is None or e[0] != btb_tag(addr):
            return False, 0
        _, kind, cnt, target = e
        if kind == BRANCH:
            taken = cnt >= 2
        elif kind == RET:
            taken = self.ras_count > 0
            target = self.ras[(self.ras_sp - 1) % RAS_ENTRIES]
        else:
            taken = True
        return (True, target) if taken else (False, 0)

    # ---------------------------------------------------------- specification 4
    def update(self, pc, insn, taken, target):
        kind = classify(insn)
        i = btb_index(pc)
        tag = btb_tag(pc)
        e = self.btb[i]
        hit = e is not None and e[0] == tag

        if taken:
            cnt = (min(e[2] + 1, 3) if (hit and kind == BRANCH) else ALLOC_CNT)
            self.btb[i] = (tag, kind, cnt, target)
        elif hit and kind == BRANCH:
            # A not-taken branch never ALLOCATES -- that is what keeps a branch
            # which is never taken free, exactly as it is today -- but one that
            # already has an entry still learns.
            self.btb[i] = (tag, kind, max(e[2] - 1, 0), e[3])

        if kind == CALL:
            self.ras[self.ras_sp] = (pc + 4) & 0xFFFFFFFF
            self.ras_sp = (self.ras_sp + 1) % RAS_ENTRIES
            self.ras_count = min(self.ras_count + 1, RAS_ENTRIES)
        elif kind == RET:
            self.ras_sp = (self.ras_sp - 1) % RAS_ENTRIES
            self.ras_count = max(self.ras_count - 1, 0)


COUNTS = ("transfers", "taken", "not_taken", "mispredicts", "hit_taken",
          "miss_cold", "wrong_target", "ntaken_predicted_taken",
          "branch", "jump", "call", "ret", "ret_correct")


def step(bp, pc, insn, taken, target, n, fault=0):
    """One transfer: predict, score, update.  Shared so the projection driver
    and `run` below cannot drift apart."""
    kind = classify(insn)
    p_taken, p_target = bp.predict(pc)

    # ---- fault injection, specification section 9 --------------------------
    if fault == 1:                          # tag ignored: any index match hits
        e = bp.btb[btb_index(pc)]
        if e is not None:
            _, k, c, t = e
            if k == RET:
                p_taken = bp.ras_count > 0
                p_target = bp.ras[(bp.ras_sp - 1) % RAS_ENTRIES]
            else:
                p_taken = (c >= 2) if k == BRANCH else True
                p_target = t
            if not p_taken:
                p_target = 0
    if fault == 4 and not p_taken:          # predict taken from a cold entry
        p_taken, p_target = True, (pc + 4) & 0xFFFFFFFF

    n["transfers"] += 1
    n[("branch", "jump", "call", "ret")[kind]] += 1
    if taken:
        n["taken"] += 1
        if p_taken and p_target == target:
            n["hit_taken"] += 1
            if kind == RET:
                n["ret_correct"] += 1
        else:
            n["mispredicts"] += 1
            n["wrong_target" if p_taken else "miss_cold"] += 1
    else:
        n["not_taken"] += 1
        if p_taken:
            n["mispredicts"] += 1
            n["ntaken_predicted_taken"] += 1

    if fault == 2 and kind == BRANCH:       # counters wrap instead of saturating
        i, tag = btb_index(pc), btb_tag(pc)
        e = bp.btb[i]
        if e is not None and e[0] == tag:
            bp.btb[i] = (tag, kind, (e[2] + (1 if taken else -1)) & 3,
                         target if taken else e[3])
        elif taken:
            bp.btb[i] = (tag, kind, ALLOC_CNT, target)
    elif fault == 3 and kind == CALL:       # RAS push condition dropped
        if taken:
            bp.btb[btb_index(pc)] = (btb_tag(pc), kind, ALLOC_CNT, target)
    else:
        bp.update(pc, insn, taken, target)


def run(trace, fault=0, bp=None):
    """Drive the predictor over a retired control-transfer trace of
    (pc, insn, next_pc) triples.  `bp` lets a caller supply a warmed predictor."""
    bp = bp or BPred()
    n = {k: 0 for k in COUNTS}
    for pc, insn, next_pc in trace:
        taken = next_pc != (pc + 4) & 0xFFFFFFFF
        step(bp, pc, insn, taken, next_pc if taken else 0, n, fault)
    return n


class DelayedBPred:
    """A BPred whose updates land `VISIBILITY_GAP` retire cycles late.

    This is the predictor as the pipeline actually presents it, and it is the
    only form the cycle model may use.  An earlier version of model/bpred.py
    applied every update immediately; it predicted a span 76 cycles short of the
    RTL's on sw/tests/a19_bpred.S, and the difference was entirely the window
    described above -- 38 tight-loop iterations that the hardware cannot yet
    know about and the model thought it could.

    The caller supplies each transfer's retire cycle.  Updates are queued and
    applied in program order once far enough in the past, which is exactly what
    a distributed-RAM write followed by an asynchronous read does.
    """

    def __init__(self, gap=VISIBILITY_GAP):
        self.bp = BPred()
        self.gap = gap
        self.pending = []          # (retire_cycle, pc, insn, taken, target)

    def _drain(self, now):
        while self.pending and now - self.pending[0][0] >= self.gap:
            _c, pc, insn, taken, target = self.pending.pop(0)
            self.bp.update(pc, insn, taken, target)

    def predict(self, addr, now):
        self._drain(now)
        return self.bp.predict(addr)

    def update(self, retire_cycle, pc, insn, taken, target):
        self.pending.append((retire_cycle, pc, insn, taken, target))


def score_trace(bp, seg, counts=None):
    """Score a warmed DelayedBPred over [(fetch_cycle, pc, insn, next_pc)].

    THE ONE PLACE the suppression rule of SUPPRESS_AFTER_REDIRECT is
    implemented, so the profiler's closure, the projection driver and any future
    caller cannot disagree about it.  A transfer is unpredicted when it IS the
    architectural successor of a transfer that just mispredicted -- that is the
    instruction the front end fetched from the redirect target, and the
    predictor was looking elsewhere when the redirect fired.
    """
    n = counts if counts is not None else {k: 0 for k in COUNTS}
    pending_target = None                # architectural next pc of a mispredict
    for f, pc, insn, nxt in seg:
        kind = classify(insn)
        taken = nxt != (pc + 4) & 0xFFFFFFFF
        target = nxt if taken else 0
        suppressed = SUPPRESS_AFTER_REDIRECT and pending_target == pc
        p_taken, p_target = (False, 0) if suppressed else bp.predict(pc, f)
        if suppressed:
            bp.predict(pc, f)            # still drain, so state stays in step
        n["transfers"] += 1
        n[("branch", "jump", "call", "ret")[kind]] += 1
        mis = False
        if taken:
            n["taken"] += 1
            if p_taken and p_target == target:
                n["hit_taken"] += 1
                if kind == RET:
                    n["ret_correct"] += 1
            else:
                mis = True
                n["wrong_target" if p_taken else "miss_cold"] += 1
        else:
            n["not_taken"] += 1
            if p_taken:
                mis = True
                n["ntaken_predicted_taken"] += 1
        if mis:
            n["mispredicts"] += 1
            pending_target = target if taken else (pc + 4) & 0xFFFFFFFF
        else:
            pending_target = None
        bp.update(f, pc, insn, taken, target)
    return n
