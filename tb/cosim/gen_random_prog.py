#!/usr/bin/env python3
"""
Random RV32IMB program generator for lockstep cosimulation.

The generated program must never trap before its final ECALL (Spike prints no
commit line for a trapping instruction, so the diff would point at the wrong
place).  Four rules keep that true: only legal encodings are emitted (no
Zifencei; M is safe because RISC-V division never traps); every memory access
uses the reserved base register with an offset inside the scratch area; every
access is naturally aligned; branches are forward-only onto emitted labels.

The density knobs are each the probability of ALLOWING a hazard rather than
padding it away:

  --raw-density        0.0 pads every RAW to 3 instructions.
  --load-use-density   0.0 keeps a load's result unused for 2 instructions.
  --mul-density        M instructions.  Kept low: a 34-cycle divide is 34
                       cycles in which nothing else is exercised.
  --bm-density         B, Zbkb and Zicond instructions.
  --branch-density     control flow.  ~0.12 is the useful setting; a program
                       that is entirely branches executes almost nothing.
"""
import argparse
import random
import re
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
MULDIV = ["mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu"]

# B (Zba + Zbb + Zbs) and Zbkb, split by operand shape.
BM_RR = ["sh1add", "sh2add", "sh3add", "andn", "orn", "xnor",
         "min", "minu", "max", "maxu", "rol", "ror",
         "bset", "bclr", "binv", "bext", "pack", "packh",
         # Zicond.  Its interesting case is rs2 == 0, which _any_src() supplies
         # through P_X0_SOURCE.
         "czero.eqz", "czero.nez"]
BM_RI = ["rori", "bseti", "bclri", "binvi", "bexti"]      # rd, rs1, shamt
BM_UN = ["clz", "ctz", "cpop", "sext.b", "sext.h", "zext.h",
         "orc.b", "rev8", "brev8", "zip", "unzip"]        # rd, rs1

# A RAW dependency is safe once the producer has reached WB.
RAW_DISTANCE = 3
LOAD_USE_DISTANCE = 3


class Gen:
    def __init__(self, seed, n, pool, raw_d, lu_d, br_d, mul_d=0.0, bm_d=0.0):
        self.rng = random.Random(seed)
        self.n = n
        self.pool = list(pool)
        self.raw_d = raw_d
        self.lu_d = lu_d
        self.br_d = br_d
        self.mul_d = mul_d
        self.bm_d = bm_d

        self.out = []              # emitted lines
        self.idx = 0               # instruction index (NOPs included)
        self.last_write = {}       # reg -> index of the instruction that wrote it
        self.last_was_load = {}    # reg -> True if that write was a load
        self.label_n = 0
        self.pending_labels = []   # (index_to_emit_at, label)
        # Set while a multi-instruction idiom is being emitted, to keep a
        # branch target from landing INSIDE it.  See mem().
        self.label_hold = False

    # ---------------------------------------------------------------- emit
    def _emit(self, text):
        # A forward branch target becomes due once the instruction stream
        # reaches it; labels are emitted between instructions, never inside a
        # padding run, so a branch can never land on a NOP that was inserted
        # after the target was chosen.
        due = ([] if self.label_hold else
               [lbl for at, lbl in self.pending_labels if at <= self.idx])
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

    # x0 is worth exercising (a forwarding bug that ignores rd == x0 shows up
    # only there) but must stay rare: at 10% of every source operand it
    # collapsed the program's value entropy to 0 and 1.
    P_X0_SOURCE = 0.05

    def _any_src(self):
        return 0 if self.rng.random() < self.P_X0_SOURCE else self.rng.choice(self.pool)

    # ------------------------------------------------------------ program
    def instruction(self):
        pick = self.rng.random()

        if pick < self.br_d:
            return self.branch()
        if pick < self.br_d + self.mul_d:
            return self.muldiv()
        if pick < self.br_d + self.mul_d + self.bm_d:
            return self.bitmanip()
        # Offset by all three control knobs, so a knob at 0 draws exactly the
        # same instruction as before it existed and every earlier seed still
        # reproduces byte for byte.
        base = self.br_d + self.mul_d + self.bm_d
        if pick < base + 0.18:
            return self.mem()
        # LUI/AUIPC inject entropy without reading anything, which is the
        # defence against every register drifting to 0 or 1.
        if pick < base + 0.34:
            return self.upper()
        if pick < base + 0.62:
            return self.reg_imm()
        return self.reg_reg()

    # Shift amounts random will never draw enough of: 0 (`rol rd, rs, 0` must
    # be the identity) and 31.  Constructed rather than hoped for.
    P_BM_EDGE = 0.30

    def bitmanip(self):
        kind = self.rng.randrange(3)
        rd, rs1 = self._r(), self._any_src()

        if kind == 0:
            op = self.rng.choice(BM_RR)
            rs2 = self._any_src()
            # Zicond's only interesting operand is a zero rs2, so it is
            # constructed here like the divide edge cases.
            if op.startswith("czero.") and self.rng.random() < self.P_BM_EDGE:
                rs2 = 0
            self._pad_for([rs1, rs2])
            self._emit(f"{op:<6} x{rd}, x{rs1}, x{rs2}")
        elif kind == 1:
            op = self.rng.choice(BM_RI)
            if self.rng.random() < self.P_BM_EDGE:
                sh = self.rng.choice([0, 31])
            else:
                sh = self.rng.randrange(32)
            self._pad_for([rs1])
            self._emit(f"{op:<6} x{rd}, x{rs1}, {sh}")
        else:
            op = self.rng.choice(BM_UN)
            self._pad_for([rs1])
            self._emit(f"{op:<6} x{rd}, x{rs1}")
        self._wrote(rd)

    def reg_reg(self):
        op = self.rng.choice(RR_OPS)
        rd, rs1, rs2 = self._r(), self._any_src(), self._any_src()
        self._pad_for([rs1, rs2])
        self._emit(f"{op:<6} x{rd}, x{rs1}, x{rs2}")
        self._wrote(rd)

    # The three operand shapes that matter are the three random will never
    # draw: a zero divisor, a divisor of -1 and a dividend of -2^31.
    P_MULDIV_EDGE = 0.30

    def muldiv(self):
        op = self.rng.choice(MULDIV)
        rd = self._r()
        rs1 = self._any_src()
        rs2 = self._any_src()

        if op[0] in "dr" and self.rng.random() < self.P_MULDIV_EDGE:
            kind = self.rng.randrange(3)
            if kind == 0:
                rs2 = 0                       # x0 -- division by zero
            elif kind == 1:
                rs2 = self._r()               # divisor -1
                self._emit(f"addi   x{rs2}, x0, -1")
                self._wrote(rs2)
            else:
                # The signed-overflow pair: -2^31 / -1.  Both operands have to
                # be built, and they must be different registers.
                rs1 = self._r()
                rs2 = self.rng.choice([r for r in self.pool if r != rs1])
                self._emit(f"lui    x{rs1}, {0x80000}")
                self._wrote(rs1)
                self._emit(f"addi   x{rs2}, x0, -1")
                self._wrote(rs2)

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

    # LUI and AUIPC have no rs1 operand: bits 19:15 are part of their
    # immediate.  Filling that field with the most recently written register
    # makes the shape that catches a hazard unit keying on the field rather
    # than on `uses_rs1` (a phantom stall behind a load) common instead of
    # accidental.
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

    # Aliasing the base: `addi xN, x8, k` immediately before the access, with
    # the offset reduced by k so the address is unchanged.  x8 is never
    # written, so without this the address operand was never a forwarded
    # value, and an aligned base plus an aligned offset never carried out of
    # bit 1.  k is not aligned, so the base's low two bits are arbitrary.
    P_ALIAS_BASE = 0.25

    def mem(self):
        load = self.rng.random() < 0.5
        op, align = self.rng.choice(LOADS if load else STORES)
        total = self.rng.randrange(0, SCRATCH_BYTES - 4) // align * align

        base, off = BASE_REG, total
        if self.rng.random() < self.P_ALIAS_BASE:
            base = self._r()                       # from the pool: never x0, never x8
            k = self.rng.randrange(0, total + 1)
            off = total - k
            self._emit(f"addi   x{base}, x{BASE_REG}, {k}")
            self._wrote(base)
            # The addi and its access are one idiom and a label must not split
            # them: a branch landing between the two enters with an arbitrary
            # base, and the access leaves scratch and traps (a hang on Spike).
            self.label_hold = True

        if load:
            rd = self._r()
            self._pad_for([base])
            self._emit(f"{op:<6} x{rd}, {off}(x{base})")
            self._wrote(rd, is_load=True)
        else:
            rs2 = self._any_src()
            self._pad_for([base, rs2])
            self._emit(f"{op:<6} x{rs2}, {off}(x{base})")
        self.label_hold = False

    # Comparing a register with itself a quarter of the time makes beq/bge/bgeu
    # certainly taken and bne/blt/bltu certainly not, whatever the values are.
    P_BRANCH_SAME_REG = 0.25

    # A share of the control transfers are unconditional jumps: JAL's
    # writeback is an address, and it always redirects.  Kept low, since a
    # jump skips everything up to its target.
    P_JUMP = 0.15

    def branch(self):
        self.label_n += 1
        lbl = f".Lb{self.label_n}"
        # Forward only, and far enough ahead that the target is still in front
        # of the branch after any padding.  A taken branch can shorten the
        # dynamic distance between producer and consumer; the pipeline forwards
        # and interlocks at every distance, so that is legal.
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
        # Base register, hand-expanded (`la` is auipc+addi with a
        # zero-separation RAW).
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
        # ...then fill the low 12 bits, which LUI leaves at zero: a shift
        # amount taken as b[5:0] needs bit 5 of some register set, and a byte
        # load that fails to sign-extend needs a byte with bit 7 set.  Padded,
        # because each xori reads the register its own LUI just wrote.
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
# seed=%d n=%d raw=%.2f load_use=%.2f branch=%.2f mul=%.2f bm=%.2f
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
    Non-zero, non-uniform initial contents for the scratch area.  Every fourth
    word has the high bit of each byte set so that lb/lh have something
    negative to read.
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


MEM_RE = re.compile(r"^\s+(lw|lh|lhu|lb|lbu|sw|sh|sb)\s+x\d+,\s*-?\d+\(x(\d+)\)")
ALIAS_RE = re.compile(r"^\s+addi\s+x(\d+), x%d, \d+$" % BASE_REG)


def check_alias_bases(lines):
    """Every aliased base is initialised on every path that reaches its access.

    A branch target emitted between the `addi` and the access would let
    control arrive with the base holding an arbitrary value; `label_hold`
    prevents that and this checks it, because the failure is a hang (mtvec is
    not armed until the epilogue).  Raises AssertionError.
    """
    for i, line in enumerate(lines):
        m = MEM_RE.match(line)
        if not m or int(m.group(2)) == BASE_REG:
            continue
        base = int(m.group(2))
        j = i - 1
        while j >= 0 and lines[j].strip() == "nop":
            j -= 1
        assert j >= 0, "aliased access at line %d has nothing before it" % i
        a = ALIAS_RE.match(lines[j])
        assert a and int(a.group(1)) == base, (
            "aliased access `%s` is not immediately preceded by its "
            "`addi x%d, x%d, k` -- found `%s`.  A label between the two makes "
            "the base register arbitrary on the branch's path."
            % (line.strip(), base, BASE_REG, lines[j].strip()))


def generate(seed, n=200, pool=None, raw_d=0.0, lu_d=0.0, br_d=0.0,
             mul_d=0.0, bm_d=0.0):
    """Return the full assembly source for one random program."""
    pool = pool or DEFAULT_POOL
    # t0/t1 (x5/x6) are used by the epilogue AFTER the body has finished, so
    # they may be in the pool; the base register may not.
    assert BASE_REG not in pool, "the memory base register must not be written"
    g = Gen(seed, n, pool, raw_d, lu_d, br_d, mul_d, bm_d)
    lines = g.generate()
    check_alias_bases(lines)
    body = "\n".join(lines)
    return (PROLOGUE % (seed, n, raw_d, lu_d, br_d, mul_d, bm_d)) + body + "\n" + EPILOGUE


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("-n", type=int, default=200, help="body instructions")
    ap.add_argument("--raw-density", type=float, default=0.0)
    ap.add_argument("--load-use-density", type=float, default=0.0)
    ap.add_argument("--branch-density", type=float, default=0.0)
    ap.add_argument("--mul-density", type=float, default=0.0)
    ap.add_argument("--bm-density", type=float, default=0.0,
                    help="density of B / Zbkb / Zicond instructions")
    ap.add_argument("-o", default=None)
    a = ap.parse_args()

    src = generate(a.seed, a.n, None, a.raw_density,
                   a.load_use_density, a.branch_density, a.mul_density,
                   a.bm_density)
    if a.o:
        open(a.o, "w").write(src)
    else:
        sys.stdout.write(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
