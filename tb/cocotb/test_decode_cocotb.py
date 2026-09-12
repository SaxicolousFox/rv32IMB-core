"""
Instruction-decoder verification against the Python decoder (plan A3).

A3's "Done when" is zero mismatches on 10^6 random 32-bit words, with the
illegal-instruction flag correct for all reserved opcodes.  Both are here, plus
the tests that random words cannot reach on their own.

Why random words are the right headline test AND not sufficient:

  * At 10^6 uniform draws almost every word is illegal, so the random pass is
    overwhelmingly a test of the legal/illegal boundary -- which is exactly what
    the root CLAUDE.md says matters ("a lax and a strict decoder disagree on
    exactly those words").  It is a weak test of the CONTROL FIELDS, because it
    reaches each legal instruction only occasionally and with random operands.

  * So the legal encodings are also enumerated directly: every opcode crossed
    with every funct3 and the funct7 values that matter, which covers each legal
    instruction form deterministically rather than by luck.

  * And the strict-reserved-field rule gets its own exhaustive test, because it
    is the one rule where this decoder must match model/isa/xkntt.py exactly and
    where a plausible-looking lax implementation passes everything else.

The Python side delegates custom-0/custom-1 to model/isa/xkntt.py, the frozen
contract, rather than reimplementing the rules -- see model/rv32i_ref.py.
"""
import os
import random
import sys

import cocotb
from cocotb.triggers import Timer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "model"))
import rv32i_ref as ref   # noqa: E402

SEED = 0xA3A30001

# Fields compared on every vector.  Sourced from the model so that adding a
# ctrl_t field cannot silently go unchecked here.
FIELDS = ref.CTRL_FIELDS
REGS = ["rd", "rs1", "rs2"]


def _sample(dut):
    got = {f: int(getattr(dut, f).value) for f in FIELDS}
    regs = {r: int(getattr(dut, f"{r}_addr").value) for r in REGS}
    return got, regs


async def check(dut, insn, tag, failures):
    dut.insn.value = insn
    await Timer(1, unit="ns")
    got, got_regs = _sample(dut)
    exp, exp_regs = ref.decode(insn)

    # Register addresses are unconditional slices of the word; if these are
    # wrong nothing else means anything, so they are checked first.
    for r in REGS:
        if got_regs[r] != exp_regs[r]:
            failures.append(f"{tag}: insn=0x{insn:08x} {r}_addr "
                            f"got {got_regs[r]} expected {exp_regs[r]}")
            return

    # An illegal instruction's other control bits are all forced to zero by both
    # the RTL and the model, so comparing every field is meaningful even here.
    diff = [f for f in FIELDS if got[f] != exp[f]]
    if diff:
        detail = ", ".join(f"{f}: got {got[f]} exp {exp[f]}" for f in diff[:6])
        failures.append(f"{tag}: insn=0x{insn:08x} -> {detail}")


@cocotb.test()
async def test_pkg_agreement(dut):
    """The model's enum encodings still match rtl/core/rv32i_pkg.sv."""
    n = ref.check_pkg_agreement()
    dut._log.info(f"package agreement OK ({n} enum members)")


@cocotb.test()
async def test_directed_encodings(dut):
    """
    Hand-written words with a known intended meaning.

    These are the cases where a mistake in BOTH the RTL and the model would
    otherwise cancel out: the two are independent, but they were written by the
    same hand on the same day, so a shared misreading of the spec is a real
    risk.  Every expectation below is asserted absolutely, not just compared
    against the model.
    """
    failures = []
    # (word, description, expected is_illegal)
    CASES = [
        (0x00000033, "add x0,x0,x0",            0),
        (0x40208033, "sub x0,x1,x2",            0),
        # M (A14).  All eight are legal; the two funct7 values on either side
        # of 0000001 are not, which is what pins "M is one funct7" rather than
        # "the low funct7 bits are ignored".  Written out one by one because a
        # rule that legalises eight encodings out of 2^32 is exactly the shape
        # the random sweep covers by luck and a directed test covers on purpose
        # -- the same lesson A3's missing SYSTEM reserved-field case taught.
        (0x02C58633, "mul    a2,a1,a2",         0),
        (0x02C59633, "mulh   a2,a1,a2",         0),
        (0x02C5A633, "mulhsu a2,a1,a2",         0),
        (0x02C5B633, "mulhu  a2,a1,a2",         0),
        (0x02C5C633, "div    a2,a1,a2",         0),
        (0x02C5D633, "divu   a2,a1,a2",         0),
        (0x02C5E633, "rem    a2,a1,a2",         0),
        (0x02C5F633, "remu   a2,a1,a2",         0),
        (0x04C58633, "OP funct7=0000010",       1),
        (0x06C58633, "OP funct7=0000011",       1),
        (0x02C5D613, "OP-IMM funct7=0000001",   1),
        (0x00001013, "slli",                    0),
        (0x02001013, "slli with insn[25] set",  1),
        (0x40005013, "srai",                    0),
        (0x20005013, "srli, bad funct7",        1),
        (0x00002063, "branch funct3=010",       1),
        (0x00003063, "branch funct3=011",       1),
        (0x00000063, "beq",                     0),
        (0x00003003, "load funct3=011",         1),
        (0x00006003, "load funct3=110",         1),
        (0x00002003, "lw",                      0),
        (0x00003023, "store funct3=011",        1),
        (0x00002023, "sw",                      0),
        (0x00001067, "jalr funct3!=0",          1),
        (0x00000067, "jalr",                    0),
        (0x0000000f, "fence",                   0),
        (0x0ff0000f, "fence with pred/succ set", 0),
        (0x0000100f, "fence.i (no Zifencei)",   1),
        (0x00000073, "ecall",                   0),
        (0x00100073, "ebreak",                  0),
        (0x30200073, "mret",                    0),
        (0x10500073, "wfi",                     0),
        (0x00108073, "ecall with rs1 != 0",      1),
        (0x00000f73, "ecall with rd != 0",       1),
        (0x30208073, "mret with rs1 != 0",       1),
        (0x00100093 & ~0x7F | 0x73, "system junk funct12", 1),
        (0x00004073, "system funct3=100",       1),
        (0x00002073, "csrrs",                   0),
        (0x00005073, "csrrwi",                  0),
        (0x00000013, "nop (addi x0,x0,0)",      0),
        (0x00000000, "all zeros",               1),
        (0xFFFFFFFF, "all ones",                1),
        (0x00000001, "insn[1:0]=01 (compressed)", 1),
        (0x00000002, "insn[1:0]=10 (compressed)", 1),
    ]
    for word, desc, exp_illegal in CASES:
        dut.insn.value = word
        await Timer(1, unit="ns")
        got = int(dut.is_illegal.value)
        if got != exp_illegal:
            failures.append(f"directed: 0x{word:08x} ({desc}) "
                            f"is_illegal got {got} expected {exp_illegal}")
        await check(dut, word, "directed-model", failures)

    dut._log.info(f"directed: {len(CASES)} hand-written encodings")
    assert not failures, "decoder directed mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_system_reserved_fields(dut):
    """
    ECALL / EBREAK / MRET / WFI reserve rd and rs1; sweep both exhaustively.

    This test exists because fault injection found its absence.  Dropping the
    `rs1_addr == 0` half of the decoder's check escaped every other test in this
    file, including the 10^6-word random run -- a SYSTEM word with funct3=0,
    rd=0, rs1 nonzero and funct12 in {0x000, 0x001, 0x302, 0x105} has
    probability ~7.1e-8 under the random generator, i.e. 0.07 expected hits in
    10^6 draws.  The Xkntt reserved fields had an exhaustive test from the
    start; these did not, and random sampling was never going to cover them.

    The general lesson, worth applying to every future reserved field: a rule
    that constrains a handful of specific encodings out of 2^32 needs a directed
    sweep.  Random testing covers the common case, never the rare constraint.
    """
    failures = []
    n = 0
    OPC_SYSTEM = 0x73
    for funct12 in (0x000, 0x001, 0x302, 0x105):
        for rd in range(32):
            for rs1 in range(32):
                insn = (OPC_SYSTEM | (rd << 7) | (0 << 12)
                        | (rs1 << 15) | (funct12 << 20))
                await check(dut, insn, "system-reserved", failures)
                n += 1
                if len(failures) > 20:
                    break
    # Also sweep funct12 itself: everything outside the four listed values is
    # illegal even with rd and rs1 correctly zero.
    for funct12 in range(4096):
        insn = OPC_SYSTEM | (0 << 7) | (0 << 12) | (0 << 15) | (funct12 << 20)
        await check(dut, insn, "system-funct12", failures)
        n += 1
        if len(failures) > 20:
            break

    dut._log.info(f"system reserved fields: {n} vectors")
    assert not failures, "decoder system-field mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_all_opcodes_and_funct3(dut):
    """
    Every 7-bit opcode crossed with every funct3 and the funct7 values that
    matter, with the remaining fields randomised.

    This is what deterministically covers the legal encodings and the reserved
    opcodes.  A3's "Done when" names the illegal flag being correct for all
    reserved opcodes, and 'all reserved opcodes' is a sweep, not a sample.
    """
    rng = random.Random(SEED)
    failures = []
    n = 0
    for opcode in range(128):
        for f3 in range(8):
            for f7 in (0b0000000, 0b0100000, 0b0000001, 0b1111111):
                base = rng.getrandbits(32)
                insn = ((base & ~0xFE00707F)
                        | opcode | (f3 << 12) | (f7 << 25))
                await check(dut, insn, "opcode-sweep", failures)
                n += 1
                if len(failures) > 20:
                    break
    dut._log.info(f"opcode x funct3 x funct7 sweep: {n} vectors")
    assert not failures, "decoder sweep mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_random_1m(dut):
    """A3's acceptance test: 10^6 random 32-bit words, zero mismatches."""
    rng = random.Random(SEED)
    failures = []
    N = 1_000_000
    legal = 0
    for _ in range(N):
        insn = rng.getrandbits(32)
        # Steer a quarter of the draws onto real opcodes.  Uniform words are
        # ~99.6% illegal, so without this the control fields of legal
        # instructions would barely be exercised at all -- the random test would
        # be almost entirely a test of the illegal path.
        if rng.random() < 0.25:
            insn = (insn & ~0x7F) | rng.choice(
                [0x03, 0x0F, 0x13, 0x17, 0x23,
                 0x33, 0x37, 0x63, 0x67, 0x6F, 0x73])
        await check(dut, insn, "random", failures)
        if int(dut.is_illegal.value) == 0:
            legal += 1
        if len(failures) > 20:
            break
    dut._log.info(f"random: {N} words, seed 0x{SEED:08x}, "
                  f"{legal} legal ({100.0 * legal / N:.2f}%)")
    # A coverage floor, not a correctness check: if steering ever broke and
    # every word came out illegal, the test above would still pass vacuously.
    assert legal > N // 100, \
        f"only {legal} of {N} random words decoded legal -- steering is broken"
    assert not failures, "decoder random mismatches:\n  " + "\n  ".join(failures[:20])
