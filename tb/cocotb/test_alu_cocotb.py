"""
Constrained-random ALU verification against the Python golden model (plan A2).

A2's "Done when" is 10^5 random ALU vectors passing.  That is the headline test
below, but random alone is a poor way to reach the corners of a 32-bit datapath
-- a uniform draw essentially never produces 0, -1, INT_MIN, or a shift amount
of exactly 31.  So the random pass is preceded by a directed sweep over the
values that actually break ALUs, crossed with every operation.

The model is model/rv32i_ref.py, written from the ISA spec rather than from the
RTL.  check_pkg_agreement() runs first: the model duplicates alu_op_e's
encodings, and a duplicated constant nobody checks is how you end up testing the
wrong operation and passing.
"""
import os
import random
import sys

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "model"))
import rv32i_ref as ref   # noqa: E402

# Deterministic: a failing run must be reproducible without plumbing a seed
# through the regression harness.
SEED = 0xA2A20001

# Values that break ALUs.  INT_MIN is here because -INT_MIN == INT_MIN, which is
# the classic signed-comparison and negation trap; 0x7FFFFFFF and 0x80000000 sit
# either side of the signed/unsigned divide, where SLT and SLTU must disagree.
CORNERS = [
    0x00000000, 0x00000001, 0x00000002, 0x7FFFFFFF, 0x80000000, 0x80000001,
    0xFFFFFFFF, 0xFFFFFFFE, 0x55555555, 0xAAAAAAAA, 0x0000FFFF, 0xFFFF0000,
    0x0000001F, 0x00000020, 0x00000021, 0x0000003F,
]

OPS = sorted(ref.ALU_OPS.values())


async def apply_and_check(dut, op, a, b, tag, failures):
    """Drive one vector, let the combinational logic settle, compare."""
    dut.op.value = op
    dut.a.value = a
    dut.b.value = b
    await Timer(1, unit="ns")

    got = int(dut.y.value)
    exp = ref.alu(op, a, b)
    if got != exp:
        name = next(k for k, v in ref.ALU_OPS.items() if v == op)
        failures.append(
            f"{tag}: {name}(a=0x{a:08x}, b=0x{b:08x}) -> "
            f"got 0x{got:08x}, expected 0x{exp:08x}")
    return len(failures)


@cocotb.test()
async def test_pkg_agreement(dut):
    """The model's enum encodings still match rtl/core/rv32i_pkg.sv."""
    n = ref.check_pkg_agreement()
    # ref.PKG_MEMBERS_CHECKED, not a local copy of the sum.  This assertion
    # used to spell the sum out here, went stale the moment A14 added the two
    # multi-cycle latency constants to the guard, and stayed wrong through A21
    # -- invisibly, because run_cocotb.py returned 0 whatever cocotb said.
    assert n == ref.PKG_MEMBERS_CHECKED, \
        f"spec-drift guard checked only {n} members"
    dut._log.info(f"package agreement OK ({n} enum members)")


@cocotb.test()
async def test_alu_corners(dut):
    """Every operation crossed with every pair of corner values."""
    failures = []
    n = 0
    for op in OPS:
        for a in CORNERS:
            for b in CORNERS:
                await apply_and_check(dut, op, a, b, "corner", failures)
                n += 1
                if len(failures) > 20:
                    break
    dut._log.info(f"corner cross: {n} vectors")
    assert not failures, "ALU corner mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_alu_shift_amounts(dut):
    """
    Every shift amount 0..31 for SLL/SRL/SRA, plus b values whose low five bits
    are right but whose upper bits are not.

    The upper-bit part is the real check: RV32I says shifts use only b[4:0], so
    a shift by 0x21 must equal a shift by 1.  An ALU that feeds the whole
    operand into the shifter passes every test where b < 32 and fails only here.
    """
    failures = []
    shift_ops = [ref.ALU_SLL, ref.ALU_SRL, ref.ALU_SRA]
    patterns = [0x80000000, 0xFFFFFFFF, 0x7FFFFFFF, 0x12345678, 0xDEADBEEF]
    n = 0
    for op in shift_ops:
        for a in patterns:
            for sh in range(32):
                for hi in (0x00000000, 0x00000020, 0xFFFFFFE0, 0x12345600):
                    await apply_and_check(dut, op, a, (hi | sh) & 0xFFFFFFFF,
                                          "shift", failures)
                    n += 1
    dut._log.info(f"shift sweep: {n} vectors")
    assert not failures, "ALU shift mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_alu_random_100k(dut):
    """A2's acceptance test: 10^5 random (a, b, op) triples."""
    rng = random.Random(SEED)
    failures = []
    N = 100_000
    for _ in range(N):
        op = rng.choice(OPS)
        a = rng.getrandbits(32)
        b = rng.getrandbits(32)
        # Bias a fraction of operands toward the corners so the random pass
        # still visits them rather than relying entirely on the directed test.
        if rng.random() < 0.15:
            a = rng.choice(CORNERS)
        if rng.random() < 0.15:
            b = rng.choice(CORNERS)
        await apply_and_check(dut, op, a, b, "random", failures)
        if len(failures) > 20:
            break
    dut._log.info(f"random: {N} vectors, seed 0x{SEED:08x}")
    assert not failures, "ALU random mismatches:\n  " + "\n  ".join(failures[:20])
