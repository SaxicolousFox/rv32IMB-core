"""
Immediate-generator verification against the Python golden model (plan A2).

A2's "Done when" is that the immediate generator matches a Python decoder on
10^5 random instruction words for all five formats.  That is
test_immgen_random_100k below, run over all SEVEN encodings (the five plus
IMM_NONE and the Zicsr IMM_Z), so it strictly exceeds the requirement.

The interesting tests are the other two, because immgen is a pure wire
permutation plus sign extension:

  * Walking ones and walking zeros over all 32 instruction bits is COMPLETE for
    detecting any mis-routed wire.  If bit i of the instruction is connected to
    the wrong place -- or to nothing -- exactly the vector with bit i set
    differs from the model.  Random vectors find the same bugs eventually;
    walking bits find them deterministically, in 64 vectors per format.

  * Exhaustive over the 12-bit I/S/B immediate fields with the rest random,
    which is what plan A2 asks for and costs 4096 vectors per format.

The 20-bit U/J fields are not swept exhaustively by default: 2^20 each is ~22 s
of simulation to re-prove what walking-bits already establishes for a pure
permutation, and rvntt_immgen additionally carries a formal proof that pins
every output bit to its source bit for ALL inputs, which strictly subsumes an
exhaustive sweep.  Set IMMGEN_EXHAUSTIVE=1 to run them anyway, following the
KYBER_KAT_FULL precedent in tb/cosim/test_kyber_kat_spike.py.
"""
import os
import random
import sys

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "model"))
import rv32i_ref as ref   # noqa: E402

SEED = 0xA2A20002
FMTS = sorted(ref.IMM_FMTS.values())
FMT_NAME = {v: k for k, v in ref.IMM_FMTS.items()}

# Which instruction bits each format's immediate is built from.  Used to size
# the exhaustive sweeps and to report coverage honestly.
FIELD_BITS = {
    ref.IMM_I: list(range(20, 32)),
    ref.IMM_S: list(range(7, 12)) + list(range(25, 32)),
    ref.IMM_B: list(range(7, 12)) + list(range(25, 32)),
    ref.IMM_U: list(range(12, 32)),
    ref.IMM_J: list(range(12, 32)),
    ref.IMM_Z: list(range(15, 20)),
}


async def apply_and_check(dut, insn, fmt, tag, failures):
    dut.insn.value = insn
    dut.fmt.value = fmt
    await Timer(1, unit="ns")

    got = int(dut.imm.value)
    exp = ref.imm(insn, fmt)
    if got != exp:
        failures.append(
            f"{tag}: {FMT_NAME[fmt]}(insn=0x{insn:08x}) -> "
            f"got 0x{got:08x}, expected 0x{exp:08x}  (xor 0x{got ^ exp:08x})")


@cocotb.test()
async def test_pkg_agreement(dut):
    """The model's enum encodings still match rtl/core/rv32i_pkg.sv."""
    n = ref.check_pkg_agreement()
    assert n == len(ref.ALU_OPS) + len(ref.IMM_FMTS), \
        f"spec-drift guard checked only {n} members"
    dut._log.info(f"package agreement OK ({n} enum members)")


@cocotb.test()
async def test_walking_bits(dut):
    """
    Walking ones and walking zeros over every instruction bit, every format.

    This is the test that actually pins the B and J scrambling.  A single
    swapped or dropped wire changes exactly one of these 64 vectors per format,
    and the reported xor names the misplaced output bit directly.
    """
    failures = []
    n = 0
    for fmt in FMTS:
        for i in range(32):
            await apply_and_check(dut, 1 << i, fmt, "walk-1", failures)
            await apply_and_check(dut, 0xFFFFFFFF ^ (1 << i), fmt, "walk-0", failures)
            n += 2
    dut._log.info(f"walking bits: {n} vectors over {len(FMTS)} formats")
    assert not failures, "immgen walking-bit mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_exhaustive_12bit_fields(dut):
    """
    Exhaustive over the immediate field bits for the 12-bit formats (I, S, B),
    with all remaining instruction bits randomised.

    The randomised background matters: it is what proves the generator ignores
    the bits outside its field. A generator that accidentally folded in the
    opcode would pass an all-zero-background sweep.
    """
    rng = random.Random(SEED)
    failures = []
    n = 0
    for fmt in (ref.IMM_I, ref.IMM_S, ref.IMM_B):
        field = FIELD_BITS[fmt]
        mask = 0
        for b in field:
            mask |= 1 << b
        for v in range(1 << len(field)):
            # Scatter the counter's bits across the (non-contiguous) field.
            insn = rng.getrandbits(32) & ~mask
            for k, b in enumerate(field):
                if (v >> k) & 1:
                    insn |= 1 << b
            await apply_and_check(dut, insn, fmt, "exhaustive12", failures)
            n += 1
            if len(failures) > 20:
                break
    dut._log.info(f"exhaustive 12-bit fields: {n} vectors")
    assert not failures, "immgen exhaustive mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_exhaustive_20bit_fields(dut):
    """Optional 2^20 sweep of the U and J fields; see the module docstring."""
    if os.environ.get("IMMGEN_EXHAUSTIVE") != "1":
        dut._log.info("skipped: set IMMGEN_EXHAUSTIVE=1 to run the 2^20 U/J sweeps")
        return

    rng = random.Random(SEED)
    failures = []
    n = 0
    for fmt in (ref.IMM_U, ref.IMM_J):
        for v in range(1 << 20):
            insn = (v << 12) | (rng.getrandbits(12))
            await apply_and_check(dut, insn, fmt, "exhaustive20", failures)
            n += 1
            if len(failures) > 20:
                break
    dut._log.info(f"exhaustive 20-bit fields: {n} vectors")
    assert not failures, "immgen exhaustive mismatches:\n  " + "\n  ".join(failures[:20])


@cocotb.test()
async def test_immgen_random_100k(dut):
    """A2's acceptance test: 10^5 random instruction words, every format."""
    rng = random.Random(SEED)
    failures = []
    N = 100_000
    for _ in range(N):
        insn = rng.getrandbits(32)
        for fmt in FMTS:
            await apply_and_check(dut, insn, fmt, "random", failures)
        if len(failures) > 20:
            break
    dut._log.info(f"random: {N} words x {len(FMTS)} formats "
                  f"= {N * len(FMTS)} vectors, seed 0x{SEED:08x}")
    assert not failures, "immgen random mismatches:\n  " + "\n  ".join(failures[:20])
