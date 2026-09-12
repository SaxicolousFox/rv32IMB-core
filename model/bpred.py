#!/usr/bin/env python3
"""
The branch predictor, modelled from its specification rather than its RTL.

A direct-mapped BTB with a two-bit counter per entry and a return-address
stack, as a pure function of the retired control-transfer stream: state
changes only when a transfer resolves in EX (so the wrong path never touches
it), and the reset state is every entry invalid.  If this disagrees with
rtl/core/rvntt_bpred.sv, the specification decides which is wrong.
"""

BRANCH, JUMP, CALL, RET = 0, 1, 2, 3

BTB_ENTRIES = 256
RAS_ENTRIES = 8

# How long an update takes to become visible, in FETCH cycles: instruction i
# is looked up at F(i)-1; transfer j resolves in EX at F(j)+2 and its write is
# readable from F(j)+3, so j's update reaches i's lookup when F(i)-F(j) >= 4.
# A three-instruction loop cannot see what its previous iteration learned.
# Fetch cycles, not retire cycles: a load-use interlock holds an instruction
# in ID after its prediction was made.
VISIBILITY_GAP = 4

# A redirect target is not predicted: the lookup reads only registered sources,
# so during the cycle a redirect fires the predictor is looking at the address
# the front end would have fetched, and its answer is suppressed.
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
    """kind, from the encoding alone."""
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

    # ---- lookup -------------------------------------------------------------
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

    # ---- update -------------------------------------------------------------
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

    # ---- fault injection ----------------------------------------------------
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
    """A BPred whose updates land `VISIBILITY_GAP` retire cycles late -- the
    predictor as the pipeline presents it, and the only form the cycle model
    may use.  Updates are queued and applied in program order once far enough
    in the past."""

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

    The one place SUPPRESS_AFTER_REDIRECT is implemented: a transfer is
    unpredicted when it is the architectural successor of a transfer that just
    mispredicted.
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
