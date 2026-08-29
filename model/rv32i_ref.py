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
