"""
Golden model for the RV32I ALU, immediate generator and instruction decoder.

Written from the ISA specification, not transcribed from the RTL: if the RTL
disagrees with this file, the RTL is wrong.  check_pkg_agreement() parses
rtl/core/rv32i_pkg.sv and compares the enum encodings duplicated below, so a
renumbered enum is a test failure rather than a silently wrong test.
"""
import os
import re
import sys

MASK32 = 0xFFFFFFFF

# ---------------------------------------------------------------- alu_op_e
# Mirrors rtl/core/rv32i_pkg.sv; verified by check_pkg_agreement().
ALU_ADD    = 0
ALU_SUB    = 1
ALU_SLL    = 2
ALU_SLT    = 3
ALU_SLTU   = 4
ALU_XOR    = 5
ALU_SRL    = 6
ALU_SRA    = 7
ALU_OR     = 8
ALU_AND    = 9
ALU_PASS_B = 10

ALU_OPS = {
    "ALU_ADD": ALU_ADD, "ALU_SUB": ALU_SUB, "ALU_SLL": ALU_SLL,
    "ALU_SLT": ALU_SLT, "ALU_SLTU": ALU_SLTU, "ALU_XOR": ALU_XOR,
    "ALU_SRL": ALU_SRL, "ALU_SRA": ALU_SRA, "ALU_OR": ALU_OR,
    "ALU_AND": ALU_AND, "ALU_PASS_B": ALU_PASS_B,
}
ALU_OP_WIDTH = 4          # alu_op_e is logic [3:0]

# --------------------------------------------------------------- imm_fmt_e
IMM_NONE = 0
IMM_I    = 1
IMM_S    = 2
IMM_B    = 3
IMM_U    = 4
IMM_J    = 5
IMM_Z    = 6

IMM_FMTS = {
    "IMM_NONE": IMM_NONE, "IMM_I": IMM_I, "IMM_S": IMM_S, "IMM_B": IMM_B,
    "IMM_U": IMM_U, "IMM_J": IMM_J, "IMM_Z": IMM_Z,
}
IMM_FMT_WIDTH = 3         # imm_fmt_e is logic [2:0]

# ----------------------------------------------------------------- bm_op_e
# B (Zba + Zbb + Zbs) and Zbkb: one member per operation, not per encoding
# (rori is BM_ROR with the immediate operand).  Checked against the package.
BM_NONE, BM_SH1ADD, BM_SH2ADD, BM_SH3ADD = 0, 1, 2, 3
BM_ANDN, BM_ORN, BM_XNOR                 = 4, 5, 6
BM_CLZ, BM_CTZ, BM_CPOP                  = 7, 8, 9
BM_MIN, BM_MINU, BM_MAX, BM_MAXU         = 10, 11, 12, 13
BM_SEXTB, BM_SEXTH, BM_ZEXTH             = 14, 15, 16
BM_ORCB, BM_REV8                         = 17, 18
BM_ROL, BM_ROR                           = 19, 20
BM_BSET, BM_BCLR, BM_BINV, BM_BEXT       = 21, 22, 23, 24
BM_PACK, BM_PACKH                        = 25, 26
BM_BREV8, BM_ZIP, BM_UNZIP               = 27, 28, 29
BM_CZEQZ, BM_CZNEZ                       = 30, 31   # Zicond

BM_OPS = {
    "BM_NONE": BM_NONE, "BM_SH1ADD": BM_SH1ADD, "BM_SH2ADD": BM_SH2ADD,
    "BM_SH3ADD": BM_SH3ADD, "BM_ANDN": BM_ANDN, "BM_ORN": BM_ORN,
    "BM_XNOR": BM_XNOR, "BM_CLZ": BM_CLZ, "BM_CTZ": BM_CTZ,
    "BM_CPOP": BM_CPOP, "BM_MIN": BM_MIN, "BM_MINU": BM_MINU,
    "BM_MAX": BM_MAX, "BM_MAXU": BM_MAXU, "BM_SEXTB": BM_SEXTB,
    "BM_SEXTH": BM_SEXTH, "BM_ZEXTH": BM_ZEXTH, "BM_ORCB": BM_ORCB,
    "BM_REV8": BM_REV8, "BM_ROL": BM_ROL, "BM_ROR": BM_ROR,
    "BM_BSET": BM_BSET, "BM_BCLR": BM_BCLR, "BM_BINV": BM_BINV,
    "BM_BEXT": BM_BEXT, "BM_PACK": BM_PACK, "BM_PACKH": BM_PACKH,
    "BM_BREV8": BM_BREV8, "BM_ZIP": BM_ZIP, "BM_UNZIP": BM_UNZIP,
    "BM_CZEQZ": BM_CZEQZ, "BM_CZNEZ": BM_CZNEZ,
}
BM_OP_WIDTH = 6           # bm_op_e is logic [5:0]

# ------------------------------------------------------- multi-cycle latency
# EX occupancy in cycles for the M instructions: an instruction with occupancy
# N sits in EX for N cycles and inserts N-1 bubbles behind it.  Duplicated
# from rv32i_pkg.sv on purpose and compared by check_pkg_agreement(), so the
# cycle model stays independent of the RTL.
MUL_CYCLES = 2
DIV_CYCLES = 34

MULDIV_CYCLES = {
    0b000: MUL_CYCLES,   # MUL
    0b001: MUL_CYCLES,   # MULH
    0b010: MUL_CYCLES,   # MULHSU
    0b011: MUL_CYCLES,   # MULHU
    0b100: DIV_CYCLES,   # DIV
    0b101: DIV_CYCLES,   # DIVU
    0b110: DIV_CYCLES,   # REM
    0b111: DIV_CYCLES,   # REMU
}


# ------------------------------------------------------------------ helpers
def u32(x):
    """Truncate to an unsigned 32-bit value."""
    return x & MASK32


def s32(x):
    """Interpret the low 32 bits of x as a signed two's-complement value."""
    x &= MASK32
    return x - (1 << 32) if x & 0x80000000 else x


def sext(value, width):
    """Sign-extend a `width`-bit field to a signed Python int."""
    sign = 1 << (width - 1)
    return (value & (sign - 1)) - (value & sign)


def bits(word, hi, lo):
    """Extract word[hi:lo] inclusive, as in Verilog."""
    return (word >> lo) & ((1 << (hi - lo + 1)) - 1)


# ---------------------------------------------------------------------- ALU
def alu(op, a, b):
    """
    Evaluate one ALU operation.  `a` and `b` are unsigned 32-bit; the result is
    unsigned 32-bit.

    Shifts use only b[4:0], per RV32I.  SLT/SLTU differ only in how the
    operands are interpreted, and SRA is the one shift that replicates the sign
    bit.
    """
    a = u32(a)
    b = u32(b)
    shamt = b & 0x1F

    if op == ALU_ADD:
        return u32(a + b)
    if op == ALU_SUB:
        return u32(a - b)
    if op == ALU_SLL:
        return u32(a << shamt)
    if op == ALU_SLT:
        return 1 if s32(a) < s32(b) else 0
    if op == ALU_SLTU:
        return 1 if a < b else 0
    if op == ALU_XOR:
        return u32(a ^ b)
    if op == ALU_SRL:
        return u32(a >> shamt)
    if op == ALU_SRA:
        # Python's >> on a negative int is already arithmetic.
        return u32(s32(a) >> shamt)
    if op == ALU_OR:
        return u32(a | b)
    if op == ALU_AND:
        return u32(a & b)
    if op == ALU_PASS_B:
        return b
    # Unreachable encodings read as zero, matching the RTL's default arm.
    return 0


# --------------------------------------------------------- immediate decode
def imm(insn, fmt):
    """
    Decode the immediate of `insn` under format `fmt`, as an unsigned 32-bit
    value (i.e. already sign-extended and truncated the way the datapath sees
    it).

    Written directly from the RISC-V base-instruction-format tables.  Bit 0 of
    the B and J immediates is a hardwired zero, not an instruction bit: branch
    and jump targets are halfword-aligned.
    """
    insn = u32(insn)

    if fmt == IMM_I:
        return u32(sext(bits(insn, 31, 20), 12))

    if fmt == IMM_S:
        v = (bits(insn, 31, 25) << 5) | bits(insn, 11, 7)
        return u32(sext(v, 12))

    if fmt == IMM_B:
        v = ((bits(insn, 31, 31) << 12)
             | (bits(insn, 7, 7) << 11)
             | (bits(insn, 30, 25) << 5)
             | (bits(insn, 11, 8) << 1))
        return u32(sext(v, 13))

    if fmt == IMM_U:
        return u32(bits(insn, 31, 12) << 12)

    if fmt == IMM_J:
        v = ((bits(insn, 31, 31) << 20)
             | (bits(insn, 19, 12) << 12)
             | (bits(insn, 20, 20) << 11)
             | (bits(insn, 30, 21) << 1))
        return u32(sext(v, 21))

    if fmt == IMM_Z:
        return bits(insn, 19, 15)          # zero-extended, never signed

    return 0                                # IMM_NONE and unused encodings


# ----------------------------------------------------------- spec-drift guard
_PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "rtl", "core", "rv32i_pkg.sv")


def _parse_enum(text, typename):
    """
    Pull `NAME = <n>'d<value>` pairs out of one typedef enum in the package.
    Only the decimal `'dN` form is matched; anything else fails the comparison
    loudly.
    """
    # `[^}]*` rather than `.*?`, so the match cannot run from one typedef to
    # the closing brace of the next.
    m = re.search(r"typedef\s+enum\s+logic\s*\[(\d+):0\]\s*\{([^}]*)\}\s*"
                  + typename, text, re.S)
    if not m:
        raise AssertionError(f"could not find `typedef enum ... {typename}` in {_PKG}")
    width = int(m.group(1)) + 1
    members = dict(re.findall(r"(\w+)\s*=\s*\d+'d(\d+)", m.group(2)))
    return width, {k: int(v) for k, v in members.items()}


# Enum members plus the two multi-cycle latency constants.  See the assertion
# at the end of check_pkg_agreement() for why this is exported.
PKG_MEMBERS_CHECKED = len(ALU_OPS) + len(IMM_FMTS) + len(BM_OPS) + 2


def check_pkg_agreement():
    """
    Assert that this file's enum encodings still match rv32i_pkg.sv.

    Raises AssertionError with a specific message on any drift.  Returns the
    number of members checked so a caller can prove the check was not vacuous.
    """
    with open(os.path.normpath(_PKG)) as f:
        text = f.read()

    checked = 0
    for typename, expected, exp_width in (("alu_op_e", ALU_OPS, ALU_OP_WIDTH),
                                          ("imm_fmt_e", IMM_FMTS, IMM_FMT_WIDTH),
                                          ("bm_op_e", BM_OPS, BM_OP_WIDTH)):
        width, found = _parse_enum(text, typename)
        assert width == exp_width, (
            f"{typename} is {width} bits in the package, {exp_width} here")
        assert set(found) == set(expected), (
            f"{typename} members differ: package has {sorted(found)}, "
            f"model has {sorted(expected)}")
        for name, val in expected.items():
            assert found[name] == val, (
                f"{typename}.{name} is {found[name]} in the package "
                f"but {val} in model/rv32i_ref.py")
            checked += 1

    # The multi-cycle latency contract: not an enum, so parsed separately.
    for name, expected in (("MULDIV_MUL_CYCLES", MUL_CYCLES),
                           ("MULDIV_DIV_CYCLES", DIV_CYCLES)):
        m = re.search(r"localparam\s+int\s+" + name + r"\s*=\s*(\d+)\s*;", text)
        assert m, f"could not find `localparam int {name}` in {_PKG}"
        assert int(m.group(1)) == expected, (
            f"{name} is {m.group(1)} in the package but {expected} in "
            f"model/rv32i_ref.py -- if the latency really changed, change it "
            f"here AND in tb/cosim/cycle_model.py's prediction, or the "
            f"independent cycle model silently stops being independent")
        checked += 1

    # The non-vacuity count, exported as PKG_MEMBERS_CHECKED so callers do not
    # each keep their own copy of the sum.
    assert checked == PKG_MEMBERS_CHECKED, (
        f"the spec-drift guard checked {checked} members, expected "
        f"{PKG_MEMBERS_CHECKED} -- an enum was added to the loop above and not "
        f"to PKG_MEMBERS_CHECKED, or the reverse")
    return checked


if __name__ == "__main__":
    n = check_pkg_agreement()
    print(f"rv32i_ref: package agreement OK ({n} enum members checked)")


# =============================================================================
# Instruction decoder
# =============================================================================
# Written from the ISA specification.

# ------------------------------------------------------------------ opcodes
OPC_LOAD     = 0x03
OPC_MISC_MEM = 0x0F
OPC_OP_IMM   = 0x13
OPC_AUIPC    = 0x17
OPC_STORE    = 0x23
OPC_OP       = 0x33
OPC_LUI      = 0x37
OPC_BRANCH   = 0x63
OPC_JALR     = 0x67
OPC_JAL      = 0x6F
OPC_SYSTEM   = 0x73

# -------------------------------------------------------------- alu_src_a_e
SRCA_RS1, SRCA_PC, SRCA_ZERO = 0, 1, 2
# -------------------------------------------------------------- alu_src_b_e
SRCB_RS2, SRCB_IMM = 0, 1
# ------------------------------------------------------------- result_sel_e
RES_ALU, RES_MEM, RES_PC4, RES_CSR = 0, 1, 2, 3

# The fields of ctrl_t, in the order the struct declares them.  Used by the
# testbench to compare and to report a mismatch by name.
CTRL_FIELDS = [
    "reg_write", "mem_read", "mem_write", "mem_op", "branch", "jump", "jalr",
    "alu_op", "alu_src_a", "alu_src_b", "result_sel", "imm_fmt",
    "uses_rs1", "uses_rs2",
    "is_ecall", "is_ebreak", "is_mret", "is_csr",
    "is_muldiv", "muldiv_op",
    "is_bitmanip", "bm_op",
    "is_illegal",
]

F7_BASE, F7_ALT, F7_MULDIV = 0b0000000, 0b0100000, 0b0000001

# Flat tables, so they can be diffed by eye against rvntt_decode.sv's.
F7_ZBA_SHADD  = 0b0010000
F7_ZBB_MINMAX = 0b0000101
F7_ZBB_ROT    = 0b0110000
F7_ZBKB_PACK  = 0b0000100
F7_ZBS_BSET   = 0b0010100
F7_ZBS_BCLR   = 0b0100100
F7_ZBS_BINV   = 0b0110100
F7_ZICOND     = 0b0000111    # Zicond

# (funct7, funct3) -> op, for the register-register forms in OP.
_BM_OP_R = {
    (F7_ZBA_SHADD,  0b010): BM_SH1ADD,
    (F7_ZBA_SHADD,  0b100): BM_SH2ADD,
    (F7_ZBA_SHADD,  0b110): BM_SH3ADD,
    # funct7 0100000 is shared with SUB (funct3 000) and SRA (101); these three
    # are 100, 110 and 111, so the two sets are disjoint.
    (F7_ALT,        0b100): BM_XNOR,
    (F7_ALT,        0b110): BM_ORN,
    (F7_ALT,        0b111): BM_ANDN,
    (F7_ZBB_MINMAX, 0b100): BM_MIN,
    (F7_ZBB_MINMAX, 0b101): BM_MINU,
    (F7_ZBB_MINMAX, 0b110): BM_MAX,
    (F7_ZBB_MINMAX, 0b111): BM_MAXU,
    (F7_ZBB_ROT,    0b001): BM_ROL,
    (F7_ZBB_ROT,    0b101): BM_ROR,
    (F7_ZBS_BSET,   0b001): BM_BSET,
    (F7_ZBS_BCLR,   0b001): BM_BCLR,
    (F7_ZBS_BCLR,   0b101): BM_BEXT,
    (F7_ZBS_BINV,   0b001): BM_BINV,
    # zext.h IS pack with rs2 = x0 -- the same encoding, and pack computes the
    # right answer for it, so there is no BM_ZEXTH row here either.
    (F7_ZBKB_PACK,  0b100): BM_PACK,
    (F7_ZBKB_PACK,  0b111): BM_PACKH,
    # Zicond.  000-100 and 110 under this funct7 stay illegal.
    (F7_ZICOND,     0b101): BM_CZEQZ,
    (F7_ZICOND,     0b111): BM_CZNEZ,
}

# The OP-IMM unary group: these five share opcode, funct3 AND imm[11:5] and
# differ ONLY in the rs2 field.  Values 3, 6 and 7 are absent on purpose --
# they are reserved and must decode as illegal.
_BM_UNARY = {0b00000: BM_CLZ, 0b00001: BM_CTZ, 0b00010: BM_CPOP,
             0b00100: BM_SEXTB, 0b00101: BM_SEXTH}
RS2_ORCB, RS2_REV8, RS2_BREV8, RS2_ZIPUNZ = 0b00111, 0b11000, 0b00111, 0b01111


def _bm_op_i(funct7, funct3, rs2):
    """The OP-IMM (immediate / unary) bit-manipulation forms, or None."""
    if funct7 == F7_ZBB_ROT and funct3 == 0b001:
        return _BM_UNARY.get(rs2)
    if funct7 == F7_ZBB_ROT and funct3 == 0b101:
        return BM_ROR                                   # rori
    if funct7 == F7_ZBS_BSET and funct3 == 0b001:
        return BM_BSET
    if funct7 == F7_ZBS_BSET and funct3 == 0b101:
        return BM_ORCB if rs2 == RS2_ORCB else None
    if funct7 == F7_ZBS_BCLR and funct3 == 0b001:
        return BM_BCLR
    if funct7 == F7_ZBS_BCLR and funct3 == 0b101:
        return BM_BEXT
    if funct7 == F7_ZBS_BINV and funct3 == 0b001:
        return BM_BINV
    if funct7 == F7_ZBS_BINV and funct3 == 0b101:
        if rs2 == RS2_REV8:
            return BM_REV8
        if rs2 == RS2_BREV8:
            return BM_BREV8
        return None
    if funct7 == F7_ZBKB_PACK and funct3 == 0b001:
        return BM_ZIP if rs2 == RS2_ZIPUNZ else None
    if funct7 == F7_ZBKB_PACK and funct3 == 0b101:
        return BM_UNZIP if rs2 == RS2_ZIPUNZ else None
    return None

# funct3 -> alu_op for the non-shift OP/OP-IMM operations.
_ALU_BY_F3 = {
    0b000: ALU_ADD, 0b010: ALU_SLT, 0b011: ALU_SLTU,
    0b100: ALU_XOR, 0b110: ALU_OR, 0b111: ALU_AND,
}


def _blank():
    d = {f: 0 for f in CTRL_FIELDS}
    d["is_illegal"] = 1
    return d


def decode(insn):
    """
    Decode one 32-bit word into the ctrl_t bundle, as a dict keyed by
    CTRL_FIELDS, plus the register addresses.

    Returns (ctrl, regs) where regs is {"rd", "rs1", "rs2"}.

    Legality: FENCE's unused fields are ignored, per the base ISA; everything
    outside rv32im_zba_zbb_zbs_zbkb_zicond_zkr_zkt_zicsr_zicntr
    (tb/cosim/spike_asm.py ISA_BASE) is illegal, with B, Zbkb and Zicond legal
    only at the exact encodings in _BM_OP_R and _bm_op_i.  Zifencei is not
    included, so FENCE.I stays illegal.
    """
    insn = u32(insn)
    opcode = bits(insn, 6, 0)
    funct3 = bits(insn, 14, 12)
    funct7 = bits(insn, 31, 25)
    funct12 = bits(insn, 31, 20)

    regs = {
        "rd":  bits(insn, 11, 7),
        "rs1": bits(insn, 19, 15),
        "rs2": bits(insn, 24, 20),
    }

    c = _blank()
    f7_base = funct7 == F7_BASE
    f7_alt = funct7 == F7_ALT
    # In OP-IMM this is not a register: it is part of the opcode for the unary
    # forms.  Named imm_rs2 so the two uses stay distinct.
    imm_rs2 = bits(insn, 24, 20)
    bm_r = _BM_OP_R.get((funct7, funct3))
    bm_i = _bm_op_i(funct7, funct3, imm_rs2)

    if opcode == OPC_LUI:
        c.update(reg_write=1, imm_fmt=IMM_U, alu_op=ALU_PASS_B,
                 alu_src_b=SRCB_IMM, result_sel=RES_ALU, is_illegal=0)

    elif opcode == OPC_AUIPC:
        c.update(reg_write=1, imm_fmt=IMM_U, alu_op=ALU_ADD, alu_src_a=SRCA_PC,
                 alu_src_b=SRCB_IMM, result_sel=RES_ALU, is_illegal=0)

    elif opcode == OPC_JAL:
        c.update(reg_write=1, jump=1, imm_fmt=IMM_J, alu_op=ALU_ADD,
                 alu_src_a=SRCA_PC, alu_src_b=SRCB_IMM, result_sel=RES_PC4,
                 is_illegal=0)

    elif opcode == OPC_JALR:
        if funct3 == 0:
            c.update(reg_write=1, jump=1, jalr=1, uses_rs1=1, imm_fmt=IMM_I,
                     alu_op=ALU_ADD, alu_src_a=SRCA_RS1, alu_src_b=SRCB_IMM,
                     result_sel=RES_PC4, is_illegal=0)

    elif opcode == OPC_BRANCH:
        if funct3 not in (0b010, 0b011):
            c.update(branch=1, uses_rs1=1, uses_rs2=1, imm_fmt=IMM_B,
                     alu_op=ALU_ADD, alu_src_a=SRCA_PC, alu_src_b=SRCB_IMM,
                     is_illegal=0)

    elif opcode == OPC_LOAD:
        if funct3 in (0b000, 0b001, 0b010, 0b100, 0b101):
            c.update(reg_write=1, mem_read=1, mem_op=funct3, uses_rs1=1,
                     imm_fmt=IMM_I, alu_op=ALU_ADD, alu_src_b=SRCB_IMM,
                     result_sel=RES_MEM, is_illegal=0)

    elif opcode == OPC_STORE:
        if funct3 in (0b000, 0b001, 0b010):
            c.update(mem_write=1, mem_op=funct3, uses_rs1=1, uses_rs2=1,
                     imm_fmt=IMM_S, alu_op=ALU_ADD, alu_src_b=SRCB_IMM,
                     is_illegal=0)

    elif opcode == OPC_OP_IMM:
        c.update(reg_write=1, uses_rs1=1, imm_fmt=IMM_I, alu_src_b=SRCB_IMM,
                 result_sel=RES_ALU)
        # B immediate forms first, as rvntt_decode.sv orders it: they share
        # funct3 001/101 with SLLI/SRLI/SRAI and are separated by imm[11:5].
        if bm_i is not None:
            c.update(is_bitmanip=1, bm_op=bm_i, alu_op=ALU_ADD, is_illegal=0)
        elif funct3 in _ALU_BY_F3:
            c.update(alu_op=_ALU_BY_F3[funct3], is_illegal=0)
        elif funct3 == 0b001:                       # SLLI
            c.update(alu_op=ALU_SLL, is_illegal=0 if f7_base else 1)
        else:                                       # 0b101, SRLI / SRAI
            c.update(alu_op=ALU_SRA if f7_alt else ALU_SRL,
                     is_illegal=0 if (f7_base or f7_alt) else 1)

    elif opcode == OPC_OP:
        c.update(reg_write=1, uses_rs1=1, uses_rs2=1, alu_src_b=SRCB_RS2,
                 result_sel=RES_ALU)
        if funct7 == F7_MULDIV:
            # M: all eight funct3 values are legal under this funct7.  result_sel
            # stays RES_ALU; rvntt_core delivers the product through ex_result.
            c.update(is_muldiv=1, muldiv_op=funct3, alu_op=ALU_ADD,
                     is_illegal=0)
        elif bm_r is not None:
            # B/Zbkb/Zicond funct7 values; 0100000 is shared with SUB and SRA at
            # funct3 000 and 101, disjoint from these at 100, 110 and 111.
            c.update(is_bitmanip=1, bm_op=bm_r, alu_op=ALU_ADD, is_illegal=0)
        elif funct3 == 0b000:                       # ADD / SUB
            c.update(alu_op=ALU_SUB if f7_alt else ALU_ADD,
                     is_illegal=0 if (f7_base or f7_alt) else 1)
        elif funct3 == 0b101:                       # SRL / SRA
            c.update(alu_op=ALU_SRA if f7_alt else ALU_SRL,
                     is_illegal=0 if (f7_base or f7_alt) else 1)
        elif funct3 == 0b001:
            c.update(alu_op=ALU_SLL, is_illegal=0 if f7_base else 1)
        else:
            c.update(alu_op=_ALU_BY_F3[funct3], is_illegal=0 if f7_base else 1)

    elif opcode == OPC_MISC_MEM:
        # FENCE: the fm/pred/succ/rs1/rd fields are ignored by base
        # implementations, so any value of them is legal.  FENCE.I (funct3=1)
        # is Zifencei, which is not in this core's ISA string.
        if funct3 == 0b000:
            c.update(is_illegal=0)

    elif opcode == OPC_SYSTEM:
        if funct3 == 0b000:
            if regs["rd"] == 0 and regs["rs1"] == 0:
                if funct12 == 0x000:
                    c.update(is_ecall=1, is_illegal=0)
                elif funct12 == 0x001:
                    c.update(is_ebreak=1, is_illegal=0)
                elif funct12 == 0x302:
                    c.update(is_mret=1, is_illegal=0)
                elif funct12 == 0x105:              # WFI, a legal NOP
                    c.update(is_illegal=0)
        elif funct3 in (0b001, 0b010, 0b011):
            c.update(is_csr=1, reg_write=1, uses_rs1=1, imm_fmt=IMM_I,
                     result_sel=RES_CSR, is_illegal=0)
        elif funct3 in (0b101, 0b110, 0b111):
            # rs1 is a uimm here, not a register: uses_rs1 must stay 0.
            c.update(is_csr=1, reg_write=1, imm_fmt=IMM_Z,
                     result_sel=RES_CSR, is_illegal=0)
        # funct3 == 0b100 is reserved.

    # An illegal instruction has no architectural effect.  The WHOLE bundle is
    # reset, not just the side-effect flags: several branches above set
    # result_sel or imm_fmt before legality is known.  The RTL states the same
    # rule the same way, at the bottom of its always_comb.
    if c["is_illegal"]:
        c = _blank()

    return c, regs
