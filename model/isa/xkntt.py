"""
Executable model of the Xkntt vendor extension.

This is the machine-checkable companion to docs/isa-spec.md.  Four artifacts
must agree on this encoding -- the RTL decoder, Spike, the LLVM MC layer, and
the test vectors -- so the spec is backed by code that can be diffed against
all of them rather than by prose alone.

Arithmetic comes from model/modarith.py, which P0.3 validated bit-exactly
against the pq-crystals C reference over 1261 polynomials.  Nothing here
reimplements montgomery_reduce or barrett_reduce.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modarith import Q, montgomery_reduce, barrett_reduce, _i16, _i32   # noqa: E402

# --------------------------------------------------------------------- opcodes
OPC_CUSTOM_0 = 0x0B      # Tier 1: tightly-coupled ALU-class
OPC_CUSTOM_1 = 0x2B      # Tier 2: block coprocessor control
# custom-2 (0x5B) is avoided: reserved for RV128.

ABI = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
       "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
       "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
       "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6"]

def reg(name):
    """Accept x<N> or an ABI name."""
    if isinstance(name, int):
        return name
    n = name.strip()
    if n in ABI:
        return ABI.index(n)
    if n == "fp":
        return 8
    if n.startswith("x") and n[1:].isdigit():
        v = int(n[1:])
        if 0 <= v < 32:
            return v
    raise ValueError(f"bad register: {name}")

# ---------------------------------------------------------------- instructions
# fmt: R  -> funct7[31:25] rs2[24:20] rs1[19:15] funct3[14:12] rd[11:7] op[6:0]
# fmt: R4 -> rs3[31:27] funct2[26:25] rs2 rs1 funct3 rd op
#
# Within custom-0, funct3 selects the FORMAT as well as the operation:
#   funct3 in {3,4} are R4-type; all others are R-type.  A decoder must apply
#   that rule before interpreting bits 31:25.
ISA = {
    #  name        opcode        fmt  funct3  funct7/funct2  operands
    "kmm":        (OPC_CUSTOM_0, "R",  0, 0x00, ("rd", "rs1", "rs2")),
    "kbfct":      (OPC_CUSTOM_0, "R",  1, 0x00, ("rd", "rs1", "rs2")),
    "kbfgs":      (OPC_CUSTOM_0, "R",  2, 0x00, ("rd", "rs1", "rs2")),
    "kbmul0":     (OPC_CUSTOM_0, "R4", 3, 0b00, ("rd", "rs1", "rs2", "rs3")),
    "kmac":       (OPC_CUSTOM_0, "R4", 4, 0b00, ("rd", "rs1", "rs2", "rs3")),
    "kbmul1":     (OPC_CUSTOM_0, "R",  5, 0x00, ("rd", "rs1", "rs2")),

    "kntt.cfg":   (OPC_CUSTOM_1, "R",  0, 0x00, ("rs1", "rs2")),
    "kntt.start": (OPC_CUSTOM_1, "R",  1, 0x00, ("rd", "rs1")),
    "kntt.wait":  (OPC_CUSTOM_1, "R",  2, 0x00, ("rd",)),
    "kntt.stat":  (OPC_CUSTOM_1, "R",  3, 0x00, ("rd",)),
}

R4_FUNCT3_CUSTOM0 = {3, 4}


def encode(mnemonic, **kw):
    """Build the 32-bit instruction word.  Unlisted register fields encode 0."""
    if mnemonic not in ISA:
        raise ValueError(f"unknown mnemonic {mnemonic}")
    opcode, fmt, funct3, f7f2, operands = ISA[mnemonic]

    for k in kw:
        if k not in operands:
            raise ValueError(f"{mnemonic} has no operand {k}")

    rd  = reg(kw.get("rd", 0))
    rs1 = reg(kw.get("rs1", 0))
    rs2 = reg(kw.get("rs2", 0))
    rs3 = reg(kw.get("rs3", 0))

    w = (opcode & 0x7F) | ((rd & 0x1F) << 7) | ((funct3 & 0x7) << 12) \
        | ((rs1 & 0x1F) << 15) | ((rs2 & 0x1F) << 20)
    if fmt == "R":
        w |= (f7f2 & 0x7F) << 25
    else:
        w |= ((f7f2 & 0x3) << 25) | ((rs3 & 0x1F) << 27)
    return w & 0xFFFFFFFF


def decode(word):
    """
    Decode a 32-bit word.  Returns a dict, or None if it is not a legal Xkntt
    instruction.  Reserved fields MUST be zero -- see docs/isa-spec.md.
    """
    w = word & 0xFFFFFFFF
    opcode = w & 0x7F
    rd     = (w >> 7)  & 0x1F
    funct3 = (w >> 12) & 0x7
    rs1    = (w >> 15) & 0x1F
    rs2    = (w >> 20) & 0x1F
    funct7 = (w >> 25) & 0x7F
    funct2 = (w >> 25) & 0x3
    rs3    = (w >> 27) & 0x1F

    for name, (op, fmt, f3, f7f2, operands) in ISA.items():
        if op != opcode or f3 != funct3:
            continue
        if fmt == "R":
            if funct7 != f7f2:
                return None
        else:
            if funct2 != f7f2:
                return None
        out = {"mnemonic": name}
        # Fields not in the operand list are reserved and must be zero.
        if "rd"  in operands: out["rd"]  = rd
        elif rd  != 0: return None
        if "rs1" in operands: out["rs1"] = rs1
        elif rs1 != 0: return None
        if "rs2" in operands: out["rs2"] = rs2
        elif rs2 != 0: return None
        if fmt == "R4":
            out["rs3"] = rs3
        return out
    return None


# ------------------------------------------------------------------- semantics
def lo16(x):  return _i16(x & 0xFFFF)
def hi16(x):  return _i16((x >> 16) & 0xFFFF)
def pack(hi, lo): return (((hi & 0xFFFF) << 16) | (lo & 0xFFFF)) & 0xFFFFFFFF
def sext32(x): return _i32(x) & 0xFFFFFFFF


def exec_kmm(rs1, rs2):
    """rd = sext32(montgomery_reduce(sext16(rs1[15:0]) * sext16(rs2[15:0])))"""
    return sext32(montgomery_reduce(lo16(rs1) * lo16(rs2)))


def exec_kbfct(rs1, rs2):
    """Cooley-Tukey butterfly.  a = rs1[15:0], b = rs1[31:16], z = rs2[15:0]."""
    a = lo16(rs1); b = hi16(rs1); z = lo16(rs2)
    t = montgomery_reduce(z * b)
    return pack(_i16(a - t), _i16(a + t))


def exec_kbfgs(rs1, rs2):
    """
    Gentleman-Sande butterfly.  a = rs1[15:0], b = rs1[31:16], z = rs2[15:0].

    NOTE the argument order of the modular multiply: the pq-crystals reference
    computes fqmul(zeta, r[j+len] - r[j]), i.e. (b - a).  See docs/isa-spec.md
    section "Deviation 1" -- the plan's proposed text says (t - b) = (a - b),
    which is the negation and would make INTT wrong.
    """
    a = lo16(rs1); b = hi16(rs1); z = lo16(rs2)
    a_new = barrett_reduce(_i16(a + b))
    b_new = montgomery_reduce(z * _i16(b - a))
    return pack(b_new, a_new)


def exec_kbmul0(rs1, rs2, rs3):
    """c0 = fqmul(fqmul(a1,b1), zeta) + fqmul(a0,b0)."""
    a0 = lo16(rs1); a1 = hi16(rs1)
    b0 = lo16(rs2); b1 = hi16(rs2)
    z  = lo16(rs3)
    t = montgomery_reduce(a1 * b1)
    t = montgomery_reduce(t * z)
    return sext32(_i16(t + montgomery_reduce(a0 * b0)))


def exec_kbmul1(rs1, rs2):
    """c1 = fqmul(a0,b1) + fqmul(a1,b0).  Needs no zeta, hence R-type."""
    a0 = lo16(rs1); a1 = hi16(rs1)
    b0 = lo16(rs2); b1 = hi16(rs2)
    return sext32(_i16(montgomery_reduce(a0 * b1) + montgomery_reduce(a1 * b0)))


def exec_kmac(rs1, rs2, rs3):
    """rd = barrett_reduce(trunc16(sext16(rs3) + montgomery_reduce(a*b)))."""
    a = lo16(rs1); b = lo16(rs2); c = lo16(rs3)
    return sext32(barrett_reduce(_i16(c + montgomery_reduce(a * b))))


EXEC = {
    "kmm": exec_kmm, "kbfct": exec_kbfct, "kbfgs": exec_kbfgs,
    "kbmul0": exec_kbmul0, "kbmul1": exec_kbmul1, "kmac": exec_kmac,
}
