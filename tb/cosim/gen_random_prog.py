#!/usr/bin/env python3
"""
Random RV32I program generator for lockstep cosimulation (plan A5).

Plan A5 is explicit that riscv-dv is not needed here: "a 300-line Python script
that emits valid RV32I with a controlled hazard density, a bounded register set,
memory accesses clamped to a valid range, and forward-only branches will find
more bugs per hour than anything else you do."  This is that script.

THE GENERATED PROGRAM MUST NEVER TRAP before its final ECALL.  A trap would
diverge the two logs for a reason that has nothing to do with the pipeline, and
Spike does not even print a commit line for the trapping instruction, so the
report would point at the wrong place.  Four rules keep that true:

  * only legal RV32I encodings are emitted (no M, no Zifencei);
  * every memory access uses the reserved base register, never a computed one,
    with an offset inside the scratch area;
  * every access is naturally aligned, because Spike traps on a misaligned lw
    and the core does not;
  * branches are forward-only and land on emitted labels.

THE DENSITY KNOBS are what make one generator serve A5 through A8.  Each is the
probability of ALLOWING a hazard rather than padding it away:

  --raw-density        0.0 pads every RAW to 3 instructions -- what A4/A5 need,
                       since the core has no forwarding.  A6 raises it.
  --load-use-density   0.0 keeps a load's result unused for 2 instructions.
                       A7 raises it.
  --branch-density     0.0 emits no control flow at all, which A5 requires:
                       the A4 core's PC is pc+4 and rvntt_core's
                       dbg_unsupported fires on any branch or jump.  A8 raises
                       it.  Unlike the other two this one is not run at 1.0:
                       a program that is entirely branches executes almost
                       nothing, so ~0.12 is the useful setting.

At every density the program stays architecturally well-defined, so Spike is
always the reference for what it should do.
"""
import argparse
import random
import sys

# Registers the generator may write.  x0 is excluded (writes are discarded), and
# so is the memory base register, which must stay pointing at scratch.
BASE_REG = 8            # s0
DEFAULT_POOL = [5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17]

SCRATCH_WORDS = 64
SCRATCH_BYTES = SCRATCH_WORDS * 4

RR_OPS = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"]
RI_OPS = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
SHIFT_I = ["slli", "srli", "srai"]
LOADS = [("lw", 4), ("lh", 2), ("lhu", 2), ("lb", 1), ("lbu", 1)]
STORES = [("sw", 4), ("sh", 2), ("sb", 1)]
BRANCHES = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]

# A RAW dependency is safe once the producer has reached WB.
RAW_DISTANCE = 3
LOAD_USE_DISTANCE = 3


class Gen:
    def __init__(self, seed, n, pool, raw_d, lu_d, br_d):
        self.rng = random.Random(seed)
        self.n = n
        self.pool = list(pool)
        self.raw_d = raw_d
        self.lu_d = lu_d
        self.br_d = br_d

        self.out = []              # emitted lines
        self.idx = 0               # instruction index (NOPs included)
        self.last_write = {}       # reg -> index of the instruction that wrote it
        self.last_was_load = {}    # reg -> True if that write was a load
        self.label_n = 0
        self.pending_labels = []   # (index_to_emit_at, label)

    # ---------------------------------------------------------------- emit
    def _emit(self, text):
        # A forward branch target becomes due once the instruction stream
        # reaches it; labels are emitted between instructions, never inside a
        # padding run, so a branch can never land on a NOP that was inserted
        # after the target was chosen.
        due = [lbl for at, lbl in self.pending_labels if at <= self.idx]
        if due:
            for lbl in due:
                self.out.append(f"{lbl}:")
            self.pending_labels = [(at, l) for at, l in self.pending_labels
                                   if l not in due]
        self.out.append("        " + text)
        self.idx += 1

    def _nop(self):
        self._emit("nop")

    def _pad_for(self, regs):
        """Insert NOPs until every source in `regs` is safely readable."""
        while True:
            need = 0
            for r in regs:
                if r == 0 or r not in self.last_write:
                    continue
                dist = self.idx - self.last_write[r]
                want = (LOAD_USE_DISTANCE if self.last_was_load.get(r)
                        else RAW_DISTANCE)
                density = self.lu_d if self.last_was_load.get(r) else self.raw_d
                if dist < want and self.rng.random() >= density:
                    need = max(need, want - dist)
            if need == 0:
                return
            for _ in range(need):
                self._nop()

    def _wrote(self, rd, is_load=False):
        if rd != 0:
            self.last_write[rd] = self.idx - 1
            self.last_was_load[rd] = is_load

    def _r(self):
        return self.rng.choice(self.pool)

    # x0 is worth exercising -- it is the only register whose value is
    # architecturally fixed, so reading it is how a forwarding bug that ignores
    # rd == x0 shows up -- but it must stay RARE.  At 10% of every source
    # operand it was collapsing the program's value entropy: combined with
    # slt/sltu, which produce 0 or 1, most registers ended up holding 0 or 1,
    # and a shift of 0 or 1 gives the same answer however broken the shifter is.
    # Fault injection found this: a mutation making the shift amount b[5:0]
    # instead of b[4:0] escaped, because all seven shifts with bit 5 set were
    # shifting 0, 1, or all-ones.
    P_X0_SOURCE = 0.05

    def _any_src(self):
        return 0 if self.rng.random() < self.P_X0_SOURCE else self.rng.choice(self.pool)

    # ------------------------------------------------------------ program
    def instruction(self):
        pick = self.rng.random()

        if pick < self.br_d:
            return self.branch()
        if pick < self.br_d + 0.18:
            return self.mem()
        # LUI/AUIPC are the only instructions that inject entropy without
        # reading anything, so they are also the program's defence against every
        # register drifting to 0 or 1 over a few hundred instructions.  See
        # P_X0_SOURCE above for how that drift hid a real bug.
        if pick < self.br_d + 0.34:
            return self.upper()
        if pick < self.br_d + 0.62:
            return self.reg_imm()
        return self.reg_reg()

    def reg_reg(self):
        op = self.rng.choice(RR_OPS)
        rd, rs1, rs2 = self._r(), self._any_src(), self._any_src()
        self._pad_for([rs1, rs2])
        self._emit(f"{op:<6} x{rd}, x{rs1}, x{rs2}")
        self._wrote(rd)

    def reg_imm(self):
        if self.rng.random() < 0.3:
            op = self.rng.choice(SHIFT_I)
            imm = self.rng.randrange(0, 32)
        else:
            op = self.rng.choice(RI_OPS)
            imm = self.rng.randrange(-2048, 2048)
        rd, rs1 = self._r(), self._any_src()
        self._pad_for([rs1])
        self._emit(f"{op:<6} x{rd}, x{rs1}, {imm}")
        self._wrote(rd)

    # LUI and AUIPC have no rs1 operand: instruction bits 19:15 are part of
    # their immediate.  Left to chance those bits name a live register only
    # rarely, and that is the ONLY shape in which a hazard unit keying on the
    # FIELD rather than on `uses_rs1` misbehaves -- it stalls behind a load into
    # a register the instruction never reads.  Such a phantom stall changes no
    # value, so the commit-log diff is byte-identical and only the cycle model
    # sees it.
    #
    # Fault injection found this directly: the mutation that ties `uses_rs1`
    # high was caught by the directed test and escaped the random suite,
    # because reaching it needed a specific immediate bit pattern immediately
    # after a load into that exact register (about 0.5% per load).  Filling the
    # field with the MOST RECENTLY WRITTEN register makes the shape common
    # instead of accidental, and does so generically -- nothing here knows or
    # cares that the interesting predecessor is a load.
    P_UPPER_FIELD_LIVE = 0.5

    def _most_recent_write(self):
        if not self.last_write:
            return None
        return max(self.last_write, key=lambda r: self.last_write[r])

    def upper(self):
        # LUI and AUIPC read no register, so they are the generator's way of
        # injecting fresh entropy without creating a dependency.
        rd = self._r()
        imm = self.rng.randrange(0, 1 << 20)
        live = self._most_recent_write()
        if live is not None and self.rng.random() < self.P_UPPER_FIELD_LIVE:
            # imm bits 7:3 land in insn[19:15], the rs1 field.
            imm = (imm & ~(0x1F << 3)) | (live << 3)
        op = "lui" if self.rng.random() < 0.7 else "auipc"
        self._emit(f"{op:<6} x{rd}, {imm}")
        self._wrote(rd)

    def mem(self):
        if self.rng.random() < 0.5:
            op, align = self.rng.choice(LOADS)
            off = self.rng.randrange(0, SCRATCH_BYTES - 4) // align * align
            rd = self._r()
            self._pad_for([BASE_REG])
            self._emit(f"{op:<6} x{rd}, {off}(x{BASE_REG})")
            self._wrote(rd, is_load=True)
        else:
            op, align = self.rng.choice(STORES)
            off = self.rng.randrange(0, SCRATCH_BYTES - 4) // align * align
            rs2 = self._any_src()
            self._pad_for([BASE_REG, rs2])
            self._emit(f"{op:<6} x{rs2}, {off}(x{BASE_REG})")

    # A branch between two independently random 32-bit values is almost never
    # taken for beq and almost always taken for bne, and the fall-through and
    # taken paths need each other's coverage.  Comparing a register WITH ITSELF
    # a quarter of the time fixes both ends at once: beq/bge/bgeu become
    # certainly taken and bne/blt/bltu certainly not, whatever the values are.
    P_BRANCH_SAME_REG = 0.25

    # A share of the control transfers are unconditional jumps.  JAL is worth
    # having in the random mix and not only in the directed test, because it is
    # the one instruction whose writeback is an ADDRESS rather than a datum --
    # and because it always redirects, so it exercises the flush on every
    # execution rather than on a data-dependent fraction of them.  Kept low: a
    # jump skips everything up to its target, so a high rate would shrink the
    # dynamic program to almost nothing.
    P_JUMP = 0.15

    def branch(self):
        self.label_n += 1
        lbl = f".Lb{self.label_n}"
        # Forward only, and far enough ahead that the target is still in front
        # of the branch after any padding the intervening instructions need.
        #
        # A taken branch changes the DYNAMIC distance between a producer and a
        # consumer, and can shorten it: the generator's padding is computed on
        # static indices.  That is deliberate and harmless from A8 onwards --
        # the pipeline forwards and interlocks at every distance, so any dynamic
        # sequence is legal -- but it does mean the density knobs stop being an
        # exact statement about what the pipeline sees once branches are on.
        target = self.idx + self.rng.randrange(3, 12)

        if self.rng.random() < self.P_JUMP:
            rd = self._r() if self.rng.random() < 0.7 else 0
            self.pending_labels.append((target, lbl))
            self._emit(f"jal    x{rd}, {lbl}")
            self._wrote(rd)
            return

        op = self.rng.choice(BRANCHES)
        rs1 = self._any_src()
        rs2 = rs1 if self.rng.random() < self.P_BRANCH_SAME_REG else self._any_src()
        self._pad_for([rs1, rs2])
        self.pending_labels.append((target, lbl))
        self._emit(f"{op:<6} x{rs1}, x{rs2}, {lbl}")

    def generate(self):
        # Base register, hand-expanded: `la` is auipc+addi with a
        # zero-separation RAW, which the A4 core cannot execute.
        self.out.append("1:      auipc   x%d, %%pcrel_hi(scratch)" % BASE_REG)
        self.idx += 1
        for _ in range(RAW_DISTANCE):
            self._nop()
        self._emit("addi   x%d, x%d, %%pcrel_lo(1b)" % (BASE_REG, BASE_REG))
        self.last_write[BASE_REG] = self.idx - 1
        for _ in range(RAW_DISTANCE):
            self._nop()

        # Seed the pool so the body does not spend its first instructions
        # computing with zeros.  LUI reads nothing, so no padding is needed
        # between the LUI of one register and the LUI of the next.
        for r in self.pool:
            self._emit(f"lui    x{r}, {self.rng.randrange(0, 1 << 20)}")
            self._wrote(r)
        # ...then fill the LOW 12 bits, which LUI leaves at zero.  Without this
        # every register starts with bits 11:0 clear, and fault injection found
        # two mutations escaping because of it: a shift amount taken as b[5:0]
        # instead of b[4:0] needs bit 5 of some register to be set, and a byte
        # load that fails to sign-extend needs a byte with bit 7 set.  Both were
        # unreachable from an all-LUI seed.  Padded, because each xori reads the
        # register its own LUI just wrote.
        for r in self.pool:
            self._pad_for([r])
            self._emit(f"xori   x{r}, x{r}, {self.rng.randrange(-2048, 2048)}")
            self._wrote(r)

        for _ in range(self.n):
            self.instruction()

        # Flush any branch target that is still outstanding, so no label is
        # left undefined.
        while self.pending_labels:
            self._nop()

        return self.out


PROLOGUE = """# Generated by tb/cosim/gen_random_prog.py -- do not edit.
# seed=%d n=%d raw=%.2f load_use=%.2f branch=%.2f
        .section .text.init
        .globl _start
_start:
"""

EPILOGUE = """
        # Arm Spike's trap handler, then stop.  `csrw mtvec, t0` writes no
        # register, so the core and Spike agree on architectural state.
2:      auipc   t0, %pcrel_hi(trap_handler)
        nop
        nop
        nop
        addi    t0, t0, %pcrel_lo(2b)
        nop
        nop
        nop
        csrw    mtvec, t0
        nop
        nop
        nop
        ecall

        .text
        .align 2
        .globl trap_handler
trap_handler:
        addi    t0, x0, 1
3:      auipc   t1, %pcrel_hi(tohost)
        addi    t1, t1, %pcrel_lo(3b)
        sw      t0, 0(t1)
9:      j       9b

        .section .data
        .align 4
        .globl scratch
scratch:
{scratch_words}

        .section .tohost, "aw", @progbits
        .globl tohost
        .align 3
tohost:   .dword 0
        .globl fromhost
        .align 3
fromhost: .dword 0
"""


def _scratch_init():
    """
    Non-zero, non-uniform initial contents for the scratch area.

    An all-zero scratch (`.space`) makes every load before the first store read
    0, so a byte load that forgets to sign-extend is indistinguishable from a
    correct one -- fault injection caught exactly that.  The pattern is
    generated, not hand-typed, and every fourth word is forced to have the high
    bit of each byte set so that lb/lh have something negative to read.
    """
    words = []
    x = 0x12345678
    for i in range(SCRATCH_WORDS):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        w = x ^ (x >> 16)
        if i % 4 == 3:
            w |= 0x80808080
        words.append(w & 0xFFFFFFFF)
    lines = []
    for i in range(0, SCRATCH_WORDS, 4):
        lines.append("        .word " +
                     ", ".join("0x%08x" % w for w in words[i:i + 4]))
    return "\n".join(lines)


EPILOGUE = EPILOGUE.replace("{scratch_words}", _scratch_init())


def generate(seed, n=200, pool=None, raw_d=0.0, lu_d=0.0, br_d=0.0):
    """Return the full assembly source for one random program."""
    pool = pool or DEFAULT_POOL
    # t0/t1 (x5/x6) are used by the epilogue AFTER the body has finished, so
    # they may be in the pool; the base register may not.
    assert BASE_REG not in pool, "the memory base register must not be written"
    g = Gen(seed, n, pool, raw_d, lu_d, br_d)
    body = "\n".join(g.generate())
    return (PROLOGUE % (seed, n, raw_d, lu_d, br_d)) + body + "\n" + EPILOGUE


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("-n", type=int, default=200, help="body instructions")
    ap.add_argument("--raw-density", type=float, default=0.0)
    ap.add_argument("--load-use-density", type=float, default=0.0)
    ap.add_argument("--branch-density", type=float, default=0.0)
    ap.add_argument("-o", default=None)
    a = ap.parse_args()

    src = generate(a.seed, a.n, None, a.raw_density,
                   a.load_use_density, a.branch_density)
    if a.o:
        open(a.o, "w").write(src)
    else:
        sys.stdout.write(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
