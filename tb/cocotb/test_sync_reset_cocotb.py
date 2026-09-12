"""
Trivial cocotb test against rvntt_sync_reset; also proves the cocotb <->
Verilator path works.
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

STAGES = 2  # must match the RTL default


@cocotb.test()
async def test_async_assert_sync_release(dut):
    """Reset asserts asynchronously and releases synchronously after STAGES edges."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.arst_n.value = 0
    await Timer(1, unit="ns")
    assert dut.rst_n.value == 0, "rst_n must be low while arst_n is low"

    for _ in range(3):
        await RisingEdge(dut.clk)
        assert dut.rst_n.value == 0, "rst_n must stay low while arst_n is low"

    # Release asynchronously; the output must not follow until clocked through.
    dut.arst_n.value = 1
    await Timer(1, unit="ns")
    assert dut.rst_n.value == 0, "release must be synchronous, not immediate"

    for i in range(STAGES):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
    assert dut.rst_n.value == 1, f"rst_n must be high {STAGES} edges after release"

    for _ in range(5):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        assert dut.rst_n.value == 1, "rst_n must stay high"


@cocotb.test()
async def test_reassert(dut):
    """Asynchronous re-assertion takes effect without a clock edge."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.arst_n.value = 1
    for _ in range(STAGES + 2):
        await RisingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert dut.rst_n.value == 1

    dut.arst_n.value = 0
    await Timer(1, unit="ns")
    assert dut.rst_n.value == 0, "async re-assert must not need a clock edge"
