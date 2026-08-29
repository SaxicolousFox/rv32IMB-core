"""
Golden model for the RV32I ALU and immediate generator (plan A2).

Written from the ISA specification, NOT transcribed from the RTL.  The whole
point of a golden model is that it fails for different reasons than the design
does; if this file were derived from rvntt_alu.sv it would agree with it by
construction and prove nothing.

Follows the same rule as the rest of model/: **if the RTL disagrees with this
file, the RTL is wrong.**  Do not adjust these functions to make hardware pass.

`check_pkg_agreement()` is a spec-drift guard.  The enum encodings below are
duplicated from rtl/core/rv32i_pkg.sv, and a duplicated constant that nobody
checks is a bug waiting to happen: renumber alu_op_e in the package and every
test here would keep passing while testing the wrong operations.  So the
package is parsed and compared, and the tests call this first.
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

    Only the decimal `'dN` form is matched, which is the form the package uses
    for these two enums.  If someone writes a member as 4'h5 or as a bare
    integer this returns nothing for it and the comparison below fails loudly
    -- which is the correct outcome for a parser that no longer understands the
    file it is guarding.
    """
    # The body is `[^}]*`, not `.*?`.  With a non-greedy `.*?` and re.S the
    # match happily STARTS at the first typedef in the file (opcode_e) and runs
    # all the way to `} alu_op_e`, reporting opcode_e's width as alu_op_e's.
    # Forbidding a closing brace inside the body pins the match to one enum.
    m = re.search(r"typedef\s+enum\s+logic\s*\[(\d+):0\]\s*\{([^}]*)\}\s*"
                  + typename, text, re.S)
    if not m:
        raise AssertionError(f"could not find `typedef enum ... {typename}` in {_PKG}")
    width = int(m.group(1)) + 1
    members = dict(re.findall(r"(\w+)\s*=\s*\d+'d(\d+)", m.group(2)))
    return width, {k: int(v) for k, v in members.items()}


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
                                          ("imm_fmt_e", IMM_FMTS, IMM_FMT_WIDTH)):
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

    assert checked == len(ALU_OPS) + len(IMM_FMTS)
    return checked


if __name__ == "__main__":
    n = check_pkg_agreement()
    print(f"rv32i_ref: package agreement OK ({n} enum members checked)")


# =============================================================================
# Instruction decoder (plan A3)
# =============================================================================
# Written from the ISA specification and, for the custom opcodes, delegated to
# model/isa/xkntt.py -- the FROZEN contract.  Delegating rather than
# reimplementing is the point: the four-way agreement in the root CLAUDE.md is
# defined as the RTL decoder, model/isa/xkntt.py, Spike and the LLVM SchedModel
# all matching.  A second hand-written copy of the Xkntt decode rules here would
# be a fourth thing to keep in sync, and it could agree with the RTL while both
# disagreed with the contract.

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "isa"))
import xkntt as _xkntt   # noqa: E402

# ------------------------------------------------------------------ opcodes
OPC_LOAD     = 0x03
OPC_CUSTOM_0 = 0x0B
OPC_MISC_MEM = 0x0F
OPC_OP_IMM   = 0x13
OPC_AUIPC    = 0x17
OPC_STORE    = 0x23
OPC_CUSTOM_1 = 0x2B
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
RES_ALU, RES_MEM, RES_PC4, RES_CSR, RES_XKNTT = 0, 1, 2, 3, 4
# --------------------------------------------------------------- xkntt_op_e
XK_NONE, XK_KMM, XK_KBFCT, XK_KBFGS, XK_KBMUL0, XK_KMAC, XK_KBMUL1 = range(7)
XK_NTT_CFG, XK_NTT_START, XK_NTT_WAIT, XK_NTT_STAT = 7, 8, 9, 10

# Xkntt mnemonic (as model/isa/xkntt.py names it) -> xkntt_op_e encoding.
XK_BY_MNEMONIC = {
    "kmm": XK_KMM, "kbfct": XK_KBFCT, "kbfgs": XK_KBFGS,
    "kbmul0": XK_KBMUL0, "kmac": XK_KMAC, "kbmul1": XK_KBMUL1,
    "kntt.cfg": XK_NTT_CFG, "kntt.start": XK_NTT_START,
    "kntt.wait": XK_NTT_WAIT, "kntt.stat": XK_NTT_STAT,
}

# The fields of ctrl_t, in the order the struct declares them.  Used by the
# testbench to compare and to report a mismatch by name.
CTRL_FIELDS = [
    "reg_write", "mem_read", "mem_write", "mem_op", "branch", "jump", "jalr",
    "alu_op", "alu_src_a", "alu_src_b", "result_sel", "imm_fmt",
    "uses_rs1", "uses_rs2", "uses_rs3",
    "is_ecall", "is_ebreak", "is_mret", "is_csr", "is_xkntt", "xkntt_op",
    "is_illegal",
]

F7_BASE, F7_ALT = 0b0000000, 0b0100000

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
    CTRL_FIELDS, plus the four register addresses.

    Returns (ctrl, regs) where regs is {"rd", "rs1", "rs2", "rs3"}.

    Legality follows three separate rules; see rtl/core/rvntt_decode.sv's header
    for why they are not one rule:
      1. Xkntt reserved fields are strict (delegated to model/isa/xkntt.py).
      2. FENCE's unused fields are ignored, per the base ISA.
      3. Anything outside rv32i_zicsr_zicntr_xkntt0p1 is illegal -- no M, no
         Zifencei.
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
        "rs3": bits(insn, 31, 27),
    }

    c = _blank()
    f7_base = funct7 == F7_BASE
    f7_alt = funct7 == F7_ALT

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
        if funct3 in _ALU_BY_F3:
            c.update(alu_op=_ALU_BY_F3[funct3], is_illegal=0)
        elif funct3 == 0b001:                       # SLLI
            c.update(alu_op=ALU_SLL, is_illegal=0 if f7_base else 1)
        else:                                       # 0b101, SRLI / SRAI
            c.update(alu_op=ALU_SRA if f7_alt else ALU_SRL,
                     is_illegal=0 if (f7_base or f7_alt) else 1)

    elif opcode == OPC_OP:
        c.update(reg_write=1, uses_rs1=1, uses_rs2=1, alu_src_b=SRCB_RS2,
                 result_sel=RES_ALU)
        if funct3 == 0b000:                         # ADD / SUB
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

    elif opcode in (OPC_CUSTOM_0, OPC_CUSTOM_1):
        c["is_xkntt"] = 1
        d = _xkntt.decode(insn)          # the frozen contract decides legality
        if d is not None:
            op = XK_BY_MNEMONIC[d["mnemonic"]]
            c.update(xkntt_op=op, is_illegal=0)
            if opcode == OPC_CUSTOM_0:
                c.update(reg_write=1, uses_rs1=1, uses_rs2=1,
                         result_sel=RES_XKNTT)
                if op in (XK_KBMUL0, XK_KMAC):
                    c["uses_rs3"] = 1
            elif op == XK_NTT_CFG:
                c.update(uses_rs1=1, uses_rs2=1)
            elif op == XK_NTT_START:
                c.update(uses_rs1=1, reg_write=1, result_sel=RES_XKNTT)
            else:                        # kntt.wait / kntt.stat
                c.update(reg_write=1, result_sel=RES_XKNTT)

    # An illegal instruction has no architectural effect.  The WHOLE bundle is
    # reset, not just the side-effect flags: several branches above set
    # result_sel or imm_fmt before legality is known.  The RTL states the same
    # rule the same way, at the bottom of its always_comb.
    if c["is_illegal"]:
        c = _blank()

    return c, regs
