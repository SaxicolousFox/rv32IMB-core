#!/usr/bin/env python3
"""
Mutation testing for the Track A pipeline.

"Fault-inject every checking mechanism" is the working norm in this repo, and
through A5 it was done with a throwaway script per step.  That made each result
unreproducible the moment the session ended, which is the wrong property for the
habit that has caught more real bugs here than anything else.  This is the same
technique, kept.

WHAT MAKES A RESULT MEANINGFUL.  Every mutation declares WHICH tests must catch
it, and both directions are failures:

  * ESCAPED   -- no declared catcher failed.  The bug is invisible to the suite.
  * NOT-CAUGHT-BY -- a test that was declared to catch it did not.  Either the
    test is weaker than believed or the manifest is stale; both are worth
    knowing, and a manifest that only had to be caught by *something* would
    slowly decay into every mutation being caught by the one broadest test.

A mutation that fails to BUILD proves nothing -- it is reported as an error, not
a catch, because deleting an expression's only use makes the tool the detector
rather than the test.  Two A4 mutations were rejected on exactly those grounds.

The baseline run comes first: every test named anywhere in the manifest is run
against unmutated RTL and must pass.  Without it a stuck-at-fail test would
appear to catch everything.

Usage:
    python3 tb/mutate/run_mutation.py                 # every mutation
    python3 tb/mutate/run_mutation.py --step A6       # one step's
    python3 tb/mutate/run_mutation.py --only fwd_priority_swapped
"""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tb"))
sys.path.insert(0, os.path.join(ROOT, "tb/cosim"))
sys.path.insert(0, os.path.join(ROOT, "tb/unit"))
import commit_diff                  # noqa: E402
import gen_random_prog              # noqa: E402
import test_cosim_a5 as a5          # noqa: E402
import test_cosim_directed as dr    # noqa: E402
import test_core_verilator as t4    # noqa: E402
import test_riscv_tests as rvt      # noqa: E402
import test_csr_traps as ct         # noqa: E402
from rtl_deps import with_deps      # noqa: E402

# ---------------------------------------------------------------- the manifest
CORE  = "rtl/core/rvntt_core.sv"
FWD   = "rtl/core/rvntt_forward.sv"
HAZ   = "rtl/core/rvntt_hazard.sv"
BR    = "rtl/core/rvntt_branch.sv"
ALU   = "rtl/core/rvntt_alu.sv"
CSR   = "rtl/core/rvntt_csr.sv"
MD    = "rtl/core/rvntt_muldiv.sv"
BM    = "rtl/core/rvntt_bitmanip.sv"
DEC   = "rtl/core/rvntt_decode.sv"
BP    = "rtl/core/rvntt_bpred.sv"
RF    = "rtl/core/rvntt_regfile.sv"
RVFI  = "rtl/core/rvntt_rvfi.sv"
MMIO    = "rtl/soc/rvntt_mmio.sv"
SOCTOP  = "rtl/soc/rvntt_soc_top.sv"
UARTRX  = "rtl/soc/rvntt_uart_rx.sv"

# Files that must be MIRRORED so they can be mutated, but which are not part of
# the Verilator simulation build.  rvntt_rvfi.sv is instantiated only under
# `RISCV_FORMAL, so t4.RTL -- which is the simulator's source list -- does not
# name it, and without this a mutation to the RVFI port would be reported as
# "not in the build's source list" rather than run.
# A12's SoC is a second design over the same core: rvntt_soc_top instantiates
# rvntt_core, rvntt_ram and rvntt_mmio, and none of them are in t4.RTL either
# (that list is the CORE simulator's).  Mirroring them lets `soc:` mutations
# reach the address decoder, the peripherals and the top level.
MIRROR_EXTRA = [RVFI, MMIO, SOCTOP, UARTRX,
                "rtl/soc/rvntt_soc_sim_top.sv", "rtl/soc/rvntt_clkgen.sv",
                "rtl/soc/rvntt_uart_tx.sv", "rtl/soc/rvntt_ram.sv",
                "rtl/common/rvntt_sync_reset.sv"]

RVFI_RUNNER = os.path.join(ROOT, "tb/formal/run_riscv_formal.py")
SOC_RUNNER  = os.path.join(ROOT, "tb/unit/test_soc_verilator.py")
BENCH_RUNNER = os.path.join(ROOT, "tb/unit/test_bench_verilator.py")

MUTATIONS = [
    # --------------------------------------------------------------- A13 ----
    # The benchmarks are the only workload here that reads mcycle and minstret
    # for their VALUES rather than to check a single architectural rule, so they
    # are the only thing that can notice a counter which is self-consistent but
    # wrong.  rtl/core/rvntt_csr.sv says as much: "mcycle is a real cycle counter
    # and therefore does NOT agree with Spike ... Nothing compares it."  These
    # three are what makes that sentence false.
    dict(step="A13", name="mcycle_counts_retires_not_cycles",
         why="mcycle advances once per retired instruction instead of once per "
             "clock -- exactly Spike's behaviour, and self-consistent enough "
             "that IPC comes out at a perfectly plausible 1.00.  Every cycle "
             "count, every score and every derived second would be wrong "
             "together, with nothing in the output looking odd",
         # A20 MOVED THIS.  mcountinhibit put a guard in front of the
         # increment, so the bare statement this used to match no longer
         # exists.  FIFTH time an anchor has gone stale under an edit here --
         # and this one was only visible because the A19 session fixed
         # run_regress.py's 40-line output truncation, which had been hiding
         # exactly these lines above the tail.
         edits=[(CSR, "      if (!inhibit_cy_q) mcycle_q <= mcycle_q + 64'd1;",
                      "      if (!inhibit_cy_q && instret_bump) mcycle_q <= mcycle_q + 64'd1;")],
         caught=["bench"]),

    dict(step="A13", name="minstret_counts_twice",
         why="minstret advances by two per retirement, so IPC reads about 1.4 "
             "-- impossible for a single-issue in-order pipeline, which is the "
             "only reason it is detectable at all from a number with no "
             "independent reference",
         # A20 MOVED THIS TOO -- mcountinhibit's IR bit joined the condition and
         # the statement wrapped onto a second line.  Same cause as the row
         # above; see its note.
         edits=[(CSR, "      if (instret_bump && !minstret_written && !inhibit_ir_q)\n"
                      "        minstret_q <= minstret_q + 64'd1;",
                      "      if (instret_bump && !minstret_written && !inhibit_ir_q)\n"
                      "        minstret_q <= minstret_q + 64'd2;")],
         caught=["bench"]),

    # Not a counter bug: a datapath bug, checked by the benchmarks' OWN output
    # rather than by anything this project wrote.  CoreMark validates its results
    # against published CRCs and dhry_verify() checks Dhrystone's published final
    # values, so this asks whether those two are worth anything as checkers.
    #
    # It replaces a first attempt that ESCAPED, and the reason is worth keeping:
    # making SRA fill with zeros changed nothing here.  Both benchmarks do
    # arithmetic right shifts -- libgcc's __divsi3 opens with `srai a2,a0,31` to
    # capture the sign -- but only ever on non-negative values, where SRA and SRL
    # agree.  Negative-operand SRA is covered by riscv-tests and riscv-formal and
    # NOT by A13; recording that is more useful than inventing a benchmark input
    # that would reach it.
    # Written as a two-byte enable rather than the obvious 4'b1111, which left
    # ex_byte_off unreferenced and would not build -- the same reformulation the
    # A12 mutations needed, and for the same reason.
    dict(step="A13", name="sb_writes_two_bytes",
         why="a byte store also writes the byte above it.  Dhrystone's inner "
             "loop is three 31-byte strcpy calls, so this corrupts the "
             "character after every one it writes -- and the check that sees it "
             "is Dhrystone's OWN published final string value, not anything "
             "written here",
         edits=[(CORE, "          dmem_be    = 4'b0001 << ex_byte_off;",
                       "          dmem_be    = 4'b0011 << ex_byte_off;")],
         caught=["bench"]),

    # --------------------------------------------------------------- A12 ----
    dict(step="A12", name="mmio_store_not_gated_from_ram",
         why="an MMIO store also reaches the RAM.  rvntt_ram ALIASES rather "
             "than faulting, so a UART write lands at (addr-BASE) truncated "
             "and quietly corrupts the program that is running",
         edits=[(MMIO, "  always_comb ram_be = is_ram ? dmem_be : 4'b0000;",
                       "  always_comb ram_be = dmem_be;")],
         caught=["soc"]),

    # Formulated as a wrong VALUE in sel_ram_q rather than as
    # `dmem_rdata = is_ram ? ...`, which was the first attempt: that version left
    # sel_ram_q unreferenced and Verilator refused to build it.  A mutation that
    # does not compile proves nothing, so every signal has to stay live.
    dict(step="A12", name="mmio_read_mux_never_selects_mmio",
         why="the read mux always returns RAM data, so every peripheral read "
             "gets whatever the aliased RAM word holds.  The bus has no "
             "handshake and no error response, so nothing downstream can tell",
         edits=[(MMIO, "      sel_ram_q    <= is_ram;",
                       "      sel_ram_q    <= 1'b1;")],
         caught=["soc"]),

    # This one ESCAPED twice before it meant anything, and both reasons are
    # worth keeping.  First formulation moved the sample to 25% of a bit, which
    # is still comfortably inside it -- a mutation that does not change
    # behaviour proves nothing.  Second, even a true boundary sample decodes
    # perfectly when the host's edges are ideal, so with the original 8-cycle
    # sim baud NOTHING could distinguish the two.  tb_soc.cpp now transmits ~2.9%
    # slow at a 34-cycle divisor, which is what the receiver claims to tolerate;
    # the STIMULUS was the gap, not the checker.
    dict(step="A12", name="uart_rx_samples_on_bit_edge",
         why="the receiver samples on the bit BOUNDARY instead of the midpoint, "
             "so it decodes correctly only from a host with perfect edges and "
             "shifts by a bit against any real baud mismatch",
         edits=[(UARTRX, "              div_q   <= DIV_W'(DIVISOR - 1);\n"
                         "              bit_q   <= '0;\n"
                         "              state_q <= R_DATA;",
                         "              div_q   <= DIV_W'(DIVISOR / 2 - 1);\n"
                         "              bit_q   <= '0;\n"
                         "              state_q <= R_DATA;")],
         caught=["soc"]),

    # NOT "the synchroniser is missing".  Bypassing the two-flop synchroniser
    # (.sw(sw) instead of .sw(sw_sync_q)) is behaviourally IDENTICAL under a
    # testbench that holds the switches steady, so that mutation would escape --
    # and the stimulus is the reason, not the checker.  Metastability is not
    # simulatable here; the synchroniser is justified by the XDC's false paths
    # (rtl/soc/CLAUDE.md) and by review, not by this harness.  What IS checkable
    # is that the readback path works at all, so that is what this claims.
    # Zeroing the synchroniser output was the obvious formulation and left
    # sw_meta_q unreferenced, so it would not build.  Swapping the two fields
    # keeps every signal live AND is the more realistic bug: a packed read whose
    # field order is wrong looks completely plausible in review.
    dict(step="A12", name="gpio_in_fields_swapped",
         why="GPIO_IN returns {sw, btn} instead of {btn, sw}, so software reads "
             "the buttons where it expects the switches.  Both halves are still "
             "live, which is exactly why a smoke test that only checks 'GPIO_IN "
             "is nonzero' would pass",
         edits=[(MMIO, "      R_GPIO_IN:   mmio_rdata_c = {24'b0, btn, sw};",
                       "      R_GPIO_IN:   mmio_rdata_c = {24'b0, sw, btn};")],
         caught=["soc"]),

    # ---------------------------------------------------------------- A6 ----
    dict(step="A6", name="fwd_priority_swapped",
         why="the older producer wins over the younger one -- the exact case "
             "plan A6 names as its directed test",
         edits=[(FWD,
                 "      if      (mem_supplies && (mem_rd_addr == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_MEM;\n"
                 "      else if (wb_supplies  && (wb_rd_addr  == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_WB;",
                 "      if      (wb_supplies  && (wb_rd_addr  == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_WB;\n"
                 "      else if (mem_supplies && (mem_rd_addr == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_MEM;")],
         caught=["formal:rvntt_forward", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_rs2_port_dropped",
         why="rs2 is never forwarded, so arithmetic on a just-computed value "
             "in the second operand reads the stale register",
         edits=[(FWD, "    if (ex_uses_rs2) begin",
                      "    if (ex_uses_rs2 && (ex_rs2_addr == 5'd0)) begin")],
         caught=["formal:rvntt_forward", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_x0_suppression_dropped",
         why="a producer whose rd is x0 becomes a forwarding source, so x0 "
             "stops reading as zero",
         edits=[(FWD,
                 "  wire mem_supplies = mem_valid && mem_reg_write && !mem_mem_read &&\n"
                 "                      (mem_rd_addr != 5'd0);",
                 "  wire mem_supplies = mem_valid && mem_reg_write && !mem_mem_read;")],
         caught=["formal:rvntt_forward", "directed:a6_forward"]),

    dict(step="A6", name="fwd_store_data_not_forwarded",
         why="the store DATA operand keeps its stale register read -- plan A7 "
             "calls this the case people forget, and it never goes through the "
             "ALU so an arithmetic-only test cannot see it",
         edits=[(CORE, "          dmem_wdata = ex_rs2_fwd;\n          dmem_be    = 4'b1111;",
                       "          dmem_wdata = id_ex_q.rs2_data;\n          dmem_be    = 4'b1111;")],
         caught=["directed:a6_forward", "random:raw"]),

    dict(step="A6", name="fwd_valid_ignored",
         why="an && typo'd to ||, so an invalid MEM/WB slot can supply a "
             "value.  Only formal catches this at A6: the only invalid slots "
             "before A8 are the reset bubbles, whose rd is x0 and which the "
             "rd != 0 term already excludes.  A8 adds the flushed-slot case",
         edits=[(FWD, "  wire wb_supplies  = wb_valid  && wb_reg_write  && (wb_rd_addr  != 5'd0);",
                      "  wire wb_supplies  = (wb_valid  || wb_reg_write) && (wb_rd_addr  != 5'd0);")],
         caught=["formal:rvntt_forward"]),

    dict(step="A6", name="regfile_writethrough_dropped",
         why="distance 3 stops working.  Forwarding covers 1 and 2 only, so "
             "without write-through there is a hole at exactly 3 -- and a "
             "generator that pads to 3 would sit in it",
         edits=[(RF, "assign rd1 = (wr_en && (wa == ra1)) ? wd : regs[ra1];",
                     "assign rd1 = regs[ra1];")],
         # NOT a4_checksum: that program pads every RAW with three NOPs, so
         # its dependencies are at distance 4 and never touch the
         # write-through path at all.  Found by this harness reporting it as
         # PARTIAL, which is the manifest earning its keep.
         caught=["formal:rvntt_regfile", "directed:a6_forward", "random:raw"]),

    dict(step="A6", name="break_sra",
         why="the plan's own suggested injection, kept as a permanent check "
             "that the differ still localises an ordinary datapath bug",
         edits=[(ALU, "rv32i_pkg::ALU_SRA:    y = $unsigned($signed(a) >>> shamt);",
                      "rv32i_pkg::ALU_SRA:    y = a >> shamt;")],
         caught=["formal:rvntt_alu", "random:raw"]),

    # ---------------------------------------------------------------- A7 ----
    dict(step="A7", name="interlock_wired_to_mem_write",
         why="a port-wiring typo: the interlock watches mem_write instead of "
             "mem_read, so loads stop stalling and stores start.  Both halves "
             "are bugs and they are caught by different mechanisms -- the "
             "missing stall by the commit log, the phantom one by the span",
         edits=[(CORE, "      .ex_mem_read (id_ex_q.ctrl.mem_read),",
                       "      .ex_mem_read (id_ex_q.ctrl.mem_write),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="interlock_rs2_watches_rs1",
         why="the rs2 comparison is wired to rs1, so a load feeding a STORE'S "
             "DATA operand does not stall.  That operand never reaches the ALU, "
             "which is why plan A7 names it specifically",
         edits=[(CORE, "      .id_rs2_addr (id_rs2),",
                       "      .id_rs2_addr (id_rs1),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="interlock_x0_load_stalls",
         why="`lw x0, ...` becomes a stall source.  Nothing can read its "
             "result, so NO VALUE CHANGES ANYWHERE -- and every nop is "
             "`addi x0, x0, 0`, so the pipeline stalls on a large fraction of "
             "all code while remaining functionally perfect.  Only the cycle "
             "model can see this",
         edits=[(HAZ, "  wire ex_pending_load = ex_valid && ex_mem_read && (ex_rd_addr != 5'd0);",
                      "  wire ex_pending_load = ex_valid && ex_mem_read;")],
         caught=["formal:rvntt_hazard", "directed:a7_loaduse"]),

    dict(step="A7", name="interlock_stalls_on_non_source_field",
         why="the interlock keys on the rs1 FIELD rather than on whether the "
             "instruction reads rs1, so LUI and AUIPC -- whose insn[19:15] is "
             "part of an immediate -- stall behind an unrelated load.  Again no "
             "value changes; this is the phantom stall the plan warns about",
         edits=[(CORE, "      .id_uses_rs1 (id_ctrl.uses_rs1),",
                       "      .id_uses_rs1 (1'b1),")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_lets_the_pc_advance",
         why="IF is not held, so the fetch stream runs on by one during the "
             "bubble and an instruction is skipped entirely",
         # A19 MOVED THIS.  The PC hold used to be a branch of the pc_q
         # always_ff; the predictor turned the whole thing into a combinational
         # pc_next mux, so the hold is now an arm of that mux and the old anchor
         # matched nothing.  The harness reported NO-OP rather than scoring a
         # mutation it had not applied, which is the correct behaviour and the
         # fourth time an anchor has gone stale under a rename here.  Deleting
         # this arm still means "the fetch stream runs on during the bubble":
         # pc_next falls through to the predicted target or pc+4.
         edits=[(CORE, "    else if (front_stall)   pc_next = pc_q;\n", "")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_forgets_the_instruction_hold",
         why="the held word is never replayed, so the stalled slot decodes the "
             "NEXT instruction while carrying the previous pc.  This is the "
             "failure mode that makes the hold register necessary at all: "
             "holding pc_q and if_id_q is not enough, because the RAM's output "
             "register has already moved on",
         edits=[(CORE, "      insn_held_q <= front_stall && !ex_redirect;",
                       "      insn_held_q <= 1'b0;")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    dict(step="A7", name="stall_injects_no_bubble",
         why="ID/EX is not cleared, so the consumer is issued twice -- a "
             "stalled cycle retires an instruction, which is exactly what plan "
             "A7's done-when forbids",
         # A14 split `stall` into id_stall and ex_stall and gave ID/EX a third
         # arm; the interlock's bubble is now the id_stall half of the second.
         edits=[(CORE, "    end else if (id_stall || ex_redirect) begin",
                       "    end else if (ex_redirect) begin")],
         caught=["directed:a7_loaduse", "random:loaduse"]),

    # ---------------------------------------------------------------- A8 ----
    dict(step="A8", name="flush_spares_if_id",
         why="only ID/EX is squashed, so the instruction whose fetch was in "
             "flight behind the branch executes from the wrong path.  This is "
             "the one-slot-too-shallow flush, and a taken branch immediately "
             "behind a taken branch is what makes it obvious",
         edits=[(CORE,
                 "    end else if (ex_redirect) begin\n"
                 "      if_id_q.valid <= 1'b0;\n"
                 "      if_id_q.pc    <= pc_q;\n"
                 "      if_id_q.insn  <= 32'h0;\n", "")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="flush_spares_id_ex",
         why="only IF/ID is squashed, so the instruction already decoded behind "
             "the branch reaches EX and executes",
         edits=[(CORE, "    end else if (id_stall || ex_redirect) begin",
                       "    end else if (id_stall) begin")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="branch_reads_stale_rs1",
         why="the comparator reads the register file instead of the forwarded "
             "operand, so a branch on a value computed one instruction earlier "
             "-- `sub` then `beqz`, which is how every compiler writes a "
             "comparison -- takes the wrong direction",
         # The .funct3 line is part of the anchor because A14's rvntt_muldiv is
         # wired from the same two forwarded operands with the same port names
         # and the same spacing.  Without it this matched TWICE and the harness
         # silently mutated the multiplier instead of the comparator -- which is
         # why mirror_rtl now rejects an ambiguous anchor outright.
         edits=[(CORE, "      .funct3 (id_ex_q.insn[14:12]),\n"
                       "      .a      (ex_rs1_fwd),",
                       "      .funct3 (id_ex_q.insn[14:12]),\n"
                       "      .a      (id_ex_q.rs1_data),")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="branch_reads_stale_rs2",
         why="the same slip on the other operand.  Two mutations rather than "
             "one because a comparator wired to one forwarded and one stale "
             "source is a real shape, and a test that only exercises rs1 would "
             "call the module verified",
         edits=[(CORE, "      .b      (ex_rs2_fwd),\n"
                       "      .taken  (ex_branch_taken)",
                       "      .b      (id_ex_q.rs2_data),\n"
                       "      .taken  (ex_branch_taken)")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="jalr_keeps_bit0",
         why="JALR does not clear bit 0 of its target.  The FETCH is unaffected "
             "-- rvntt_ram ignores the low address bits by design -- so the "
             "core executes the right instruction at a pc that is off by one, "
             "and only the commit log's pc column shows it",
         edits=[(CORE, "{ex_alu_y[31:1], 1'b0}", "{ex_alu_y[31:1], ex_alu_y[0]}")],
         # A11 added the second catcher, and it is the interesting one: RISCOF
         # passes 76/76 over this mutation (see the A10 table in CLAUDE.md),
         # because no arch-test computes an odd JALR target.  riscv-formal's
         # jalr model states the `& ~1` directly, so it cannot be missed.
         # pc_fwd does NOT catch it -- the core is self-consistent, fetching
         # from exactly the odd pc it reports -- which is why the catcher has to
         # be the instruction model and not a consistency check.
         caught=["directed:a8_control", "rvfi:insn_jalr_ch0"]),

    dict(step="A8", name="jump_does_not_redirect",
         why="JAL and JALR fall through instead of jumping, while branches "
             "still work",
         edits=[(CORE,
                 "                      ((id_ex_q.ctrl.branch && ex_branch_taken) ||\n"
                 "                       id_ex_q.ctrl.jump);",
                 "                      (id_ex_q.ctrl.branch && ex_branch_taken);")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="jal_link_is_the_target",
         why="the link register gets the branch target instead of pc+4.  The "
             "control flow is perfect and only the writeback is wrong, which is "
             "why JAL needs an rd != 0 somewhere in the test set",
         edits=[(CORE, "      rv32i_pkg::RES_PC4: mem_result = ex_mem_q.pc_plus4;",
                       "      rv32i_pkg::RES_PC4: mem_result = ex_mem_q.ex_result;")],
         caught=["directed:a8_control", "random:branch"]),

    dict(step="A8", name="blt_uses_unsigned_compare",
         why="the signed/unsigned distinction collapses.  Every test built from "
             "small positive numbers passes; it takes operands whose sign bits "
             "differ to see it at all",
         edits=[(BR, "      rv32i_pkg::F3_BLT:  taken =  lt;",
                     "      rv32i_pkg::F3_BLT:  taken =  ltu;")],
         caught=["formal:rvntt_branch", "directed:a8_control", "random:branch"]),

    dict(step="A8", name="bge_is_not_the_complement_of_blt",
         why="BGE returns the same answer as BLT rather than its negation -- an "
             "inverted-polarity slip, which is the most common way a six-way "
             "condition decoder goes wrong",
         edits=[(BR, "      rv32i_pkg::F3_BGE:  taken = !lt;",
                     "      rv32i_pkg::F3_BGE:  taken =  lt;")],
         caught=["formal:rvntt_branch", "directed:a8_control", "random:branch"]),

    dict(step="A8", name="branch_funct3_reserved_takes",
         why="the reserved BRANCH encodings default to taken instead of not "
             "taken.  The decoder already rejects them, so nothing reaches this "
             "-- which is the point: only the proof can see it, and defence in "
             "depth that nothing checks is decoration",
         edits=[(BR, "      default:            taken = 1'b0;   // 010 and 011: reserved, and illegal",
                     "      default:            taken = 1'b1;")],
         caught=["formal:rvntt_branch"]),

    # ---------------------------------------------------------------- A9 ----
    dict(step="A9", name="minstret_counted_at_wb",
         why="the counter moves back to the WB stage, where two older "
             "instructions are still uncounted when a CSR read executes.  "
             "riscv-tests does not notice -- its own check happens to survive "
             "the off-by-two -- and the comparison against Spike's instruction "
             "count does",
         edits=[(CORE, "      .instret_bump     (id_ex_q.valid && !ex_trap && !ex_stall),",
                       "      .instret_bump     (mem_wb_q.valid),")],
         caught=["csr:a9_minstret"]),

    dict(step="A9", name="minstret_write_not_suppressed",
         why="a write to minstret no longer suppresses the writing "
             "instruction's own increment, so `csrwi minstret, 0` followed by a "
             "read gives 1.  Only riscv-tests' instret_overflow states this",
         edits=[(CSR, "  wire minstret_written = do_write &&\n"
                      "                          (addr == CSR_MINSTRET || addr == CSR_MINSTRETH);",
                      "  wire minstret_written = do_write && (addr == CSR_MCYCLE);")],
         caught=["riscv:rv32mi/instret_overflow", "formal:rvntt_csr"]),

    dict(step="A9", name="illegal_instruction_does_not_trap",
         why="an illegal instruction retires instead of trapping.  It is the "
             "one case rvntt_core's dbg_unsupported still watches for, which is "
             "why that guard was kept when A9 made it unreachable",
         edits=[(CORE, "      if (id_ex_q.ctrl.is_illegal ||\n"
                       "          (id_ex_q.ctrl.is_csr && ex_csr_illegal)) begin",
                       "      if (1'b0 && (id_ex_q.ctrl.is_illegal ||\n"
                       "          (id_ex_q.ctrl.is_csr && ex_csr_illegal))) begin")],
         caught=["riscv:rv32mi/illegal", "riscv:rv32mi/shamt"]),

    dict(step="A9", name="misaligned_load_does_not_trap",
         why="a misaligned load aliases onto the containing word instead of "
             "faulting -- which is what this core did before A9, and which no "
             "test written alongside it would have questioned",
         edits=[(CORE, "      end else if (id_ex_q.ctrl.mem_read && ex_addr_misaligned) begin\n"
                       "        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = ex_mem_addr;\n", "")],
         caught=["riscv:rv32mi/lw-misaligned", "riscv:rv32mi/ma_addr"]),

    dict(step="A9", name="faulting_store_still_writes_memory",
         why="the misaligned store traps AND lands.  The trap is reported "
             "correctly, so every check that looks at mcause passes; only a "
             "test that reads the memory back afterwards can see it",
         edits=[(CORE, "    if (id_ex_q.valid && id_ex_q.ctrl.mem_write && !ex_trap) begin",
                       "    if (id_ex_q.valid && id_ex_q.ctrl.mem_write) begin")],
         caught=["csr:a9_csr"]),

    dict(step="A9", name="trap_does_not_squash_the_instruction",
         why="the faulting instruction carries on to WB and retires.  That "
             "breaks three things at once: minstret counts it, the commit log "
             "gains a line Spike does not have, and the register write it was "
             "supposed to abandon happens",
         # A14 added ex_stall as a second reason to bubble here.  Deleting the
         # whole arm would delete that too and test two things at once, so this
         # removes only the trap term and leaves the stall bubble intact.
         edits=[(CORE, "    end else if (ex_trap || ex_stall) begin",
                       "    end else if (ex_stall) begin")],
         # NOT a9_minstret: that program never traps, so its counter is
         # unaffected.  A squashed-instruction bug shows up where instructions
         # actually fault -- and in every random program, whose closing ECALL
         # would then retire and appear in a log Spike has no line for.
         caught=["riscv:rv32mi/illegal", "random:branch"]),

    dict(step="A9", name="csrrs_with_x0_writes_anyway",
         why="CSRRS/CSRRC stop checking their source for zero, so `csrr rd, "
             "csr` becomes a write.  On a read-only CSR that turns a legal "
             "read into an illegal-instruction trap, which is how riscv-tests' "
             "zicntr sees it; a9_csr sees the write itself",
         edits=[(CORE, "  wire ex_csr_src_nz = ex_csr_imm ? (id_ex_q.imm[4:0] != 5'd0)\n"
                       "                                  : (id_ex_q.rs1_addr != 5'd0);",
                       "  wire ex_csr_src_nz = ex_csr_imm ? (id_ex_q.imm[4:0] != 5'd0)\n"
                       "                                  : 1'b1;")],
         caught=["riscv:rv32mi/zicntr", "csr:a9_csr"]),

    dict(step="A9", name="read_only_csr_accepts_a_write",
         why="writing a counter shadow silently does nothing instead of "
             "trapping.  Nothing observes the lost write; the missing trap is "
             "the whole of the failure",
         edits=[(CSR, "  assign illegal = !known || (wen && read_only);",
                      "  assign illegal = !known;")],
         caught=["formal:rvntt_csr", "csr:a9_csr"]),

    dict(step="A9", name="mret_does_not_restore_mie",
         why="MRET leaves MIE where the trap left it.  There are no interrupts "
             "yet, so nothing in the machine behaves differently -- this is "
             "invisible until the first one, which is a long way from here",
         edits=[(CSR, "        mstatus_mie_q  <= mstatus_mpie_q;\n"
                      "        mstatus_mpie_q <= 1'b1;",
                      "        mstatus_mpie_q <= 1'b1;")],
         caught=["formal:rvntt_csr", "csr:a9_csr"]),

    dict(step="A9", name="mtval_not_set_on_a_misaligned_access",
         why="the trap is taken with the right cause and the wrong mtval, so a "
             "handler that tries to emulate the access works on the wrong "
             "address",
         edits=[(CORE, "        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = ex_mem_addr;",
                       "        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = 32'h0;")],
         # ma_addr checks the CAUSE and the handler's ability to resume; it
         # does not read mtval back, so only the directed test sees this.
         caught=["csr:a9_csr"]),

    dict(step="A9", name="misaligned_jump_target_does_not_trap",
         why="a jump to a 2-mod-4 address is taken instead of faulting.  The "
             "fetch then aliases, because rvntt_ram ignores the low address "
             "bits, so the core runs the right instruction at the wrong pc",
         edits=[(CORE, "  wire ex_target_misaligned = ex_ctrl_xfer && ex_jump_target[1];",
                       "  wire ex_target_misaligned = ex_ctrl_xfer && ex_jump_target[0];")],
         caught=["riscv:rv32mi/ma_fetch"]),
    # --------------------------------------------------------------- A11 ----
    # These are mutations of the RVFI PORT rather than of the pipeline: a
    # verification interface that misreports is exactly as dangerous as a broken
    # datapath, because everything downstream believes it.  Each one names the
    # single riscv-formal check that states the property directly.
    dict(step="A11", name="rvfi_order_skips_traps",
         why="rvfi_order stops counting trapped instructions -- which is what "
             "it would do if it were derived from minstret, the trap plan A11 "
             "warns about.  Two instructions then share an index, and every "
             "check that identifies an instruction BY its order is quietly "
             "looking at the wrong one",
         edits=[(RVFI, "    else if (mw_q.valid) order_q <= order_q + 64'd1;",
                       "    else if (mw_q.valid && !mw_q.trap) order_q <= order_q + 64'd1;")],
         caught=["rvfi:unique_ch0"]),

    dict(step="A11", name="rvfi_trap_not_reported",
         why="the trapped instruction is dropped from RVFI instead of being "
             "reported with rvfi_trap -- i.e. RVFI is wired straight out of the "
             "commit tracer, which is what plan A11 says to do and what does "
             "not work.  The stream then jumps from the instruction before the "
             "fault to the handler's first instruction, and pc_fwd sees a "
             "pc_rdata that does not follow the previous pc_wdata",
         edits=[(RVFI, "    ex_pkt.valid    = ex_valid;",
                       "    ex_pkt.valid    = ex_valid && !ex_trap;")],
         caught=["rvfi:pc_fwd_ch0"]),

    dict(step="A11", name="rvfi_rs1_not_forwarded",
         why="RVFI reports the register file's own read port instead of the "
             "forwarded operand.  The core computes correctly and LIES about "
             "what it read, so the spec model is fed a stale value and predicts "
             "a different result -- a whole class of bug that only exists "
             "because RVFI has to report the architectural pre-state",
         edits=[(CORE, "      .ex_rs1_fwd         (ex_rs1_fwd),",
                       "      .ex_rs1_fwd         (id_ex_q.rs1_data),")],
         caught=["rvfi:reg_ch0"]),

    dict(step="A11", name="rvfi_rd_addr_from_the_decoder",
         why="rvfi_rd_addr is taken from the instruction word rather than from "
             "the register file's write enable, so an instruction that writes "
             "no register still names one.  A store's rd field is part of its "
             "immediate, which is why this shows up on sw and not on add",
         edits=[(RVFI, "  assign rvfi_rd_addr   = wb_we ? wb_rd_addr : 5'd0;",
                       "  assign rvfi_rd_addr   = wb_rd_addr;")],
         caught=["rvfi:insn_sw_ch0"]),

    dict(step="A11", name="rvfi_mem_addr_not_word_aligned",
         why="the reported access address keeps its low two bits, against "
             "RISCV_FORMAL_ALIGNED_MEM.  NOT caught by insn_lw: a word load "
             "that does not trap is aligned already, so the mutation is "
             "invisible there and only the sub-word accesses see it.  A "
             "reminder that picking the widest test is not picking the "
             "strongest one",
         edits=[(RVFI, "    ex_pkt.mem_addr  = {ex_mem_addr[31:2], 2'b00};",
                       "    ex_pkt.mem_addr  = ex_mem_addr;")],
         caught=["rvfi:insn_lb_ch0"]),

    dict(step="A11", name="rvfi_shadow_reports_one_cycle_early",
         why="the shadow pipeline loses its MEM stage, so RVFI describes the "
             "instruction in MEM while rd_wdata and mem_rdata still belong to "
             "the one in WB.  This is the failure the module's own "
             "a_shadow_pc/a_shadow_insn assertions exist to localise",
         edits=[(RVFI, "      mw_q           <= em_q;",
                       "      mw_q           <= ex_pkt;")],
         caught=["rvfi:pc_fwd_ch0"]),

    dict(step="A11", name="fwd_xkntt_disagrees_with_writeback",
         why="RESTORES A REAL BUG that riscv-formal found on its first clean "
             "run: the MEM forwarding mux sent every result_sel that is not "
             "RES_PC4 to ex_result, while mem_result sends RES_XKNTT to zero. "
             "A legal Xkntt instruction therefore forwarded its ALU output and "
             "wrote zero to the register file.  No RV32I program can reach it, "
             "which is why five suites and 35 mutations had not",
         edits=[(CORE, "      default:            ex_mem_fwd_data = 32'h0;",
                       "      default:            ex_mem_fwd_data = ex_mem_q.ex_result;")],
         caught=["rvfi:reg_ch0"]),

    # --------------------------------------------------------------- A14 ----
    # The M extension, and the multi-cycle EX mechanism under it.  Two of these
    # are the interesting ones and neither changes an architectural value:
    # `muldiv_done_one_cycle_late` is visible ONLY to the cycle model, and
    # `minstret_counts_stalled_cycles` ONLY to a9_minstret.  Everything else in
    # the tree -- Spike, rv32um, the commit-log differ -- is structurally blind
    # to both, which is the same shape as A7's phantom stall.
    dict(step="A14", name="muldiv_done_one_cycle_late",
         why="every M instruction occupies EX for one cycle longer than the "
             "latency contract.  Every VALUE is unchanged and every retirement "
             "is in the right order, so Spike's log, rv32um and riscv-formal "
             "all agree -- the span is the only thing that moves.  This is the "
             "mutation that proves tb/cosim/cycle_model.py's Sum(latency-1) "
             "term is doing work rather than being satisfied by construction",
         edits=[(MD, "  assign done = req && active_q && (cnt_q == target);",
                     "  assign done = req && active_q && (cnt_q == target + 6'd1);")],
         caught=["directed:a14_muldiv", "random:muldiv"]),

    dict(step="A14", name="muldiv_done_one_cycle_early",
         why="the result is taken one cycle before it exists: for MUL that is "
             "the previous operation's product still sitting in the output "
             "register, for DIV it is 31 iterations instead of 32.  This is "
             "the partial-result failure MODS_A A14 names, in the place it "
             "actually lives -- the unit's own done condition rather than the "
             "forwarding network, which never sees a partial value at all "
             "because EX/MEM is bubbled on every stalled cycle",
         edits=[(MD, "  assign done = req && active_q && (cnt_q == target);",
                     "  assign done = req && active_q && (cnt_q == target - 6'd1);")],
         caught=["directed:a14_muldiv", "random:muldiv",
                 "riscv:rv32um/mul", "riscv:rv32um/div"]),

    # THIS ENTRY REPLACED ONE THAT ESCAPED, AND THE ESCAPE WAS THE FINDING.
    # The first version dropped the multiplier's operand-register ENABLE, so
    # m_a_q/m_b_q reloaded from the drifting forwarding muxes on every stalled
    # cycle.  It escaped every declared catcher -- correctly.  The multiplier is
    # a three-deep register CHAIN whose latency equals its depth, so the value
    # read on the done cycle is the product of the operands present on the START
    # cycle whether or not the enable is there; the later ones are still in
    # flight behind it.  The enable is load-bearing for the DIVIDER, whose state
    # is a loop rather than a chain, and structurally redundant for the
    # multiplier.  That is recorded in rvntt_muldiv.sv rather than papered over
    # with a mutation aimed somewhere it could never bite.
    #
    # The bug the original entry was trying to reach lives one level up, in how
    # the CORE feeds the unit -- and there it is real for both halves.
    dict(step="A14", name="muldiv_reads_the_register_file_not_forwarding",
         why="the multi-cycle unit is fed id_ex_q.rs1_data/rs2_data instead of "
             "the forwarding muxes' outputs, which is the ordinary way a new "
             "functional unit gets wired in.  The answer is right whenever no "
             "producer happened to be within two instructions, so it passes "
             "casual testing and fails on compiled code -- and rv32um does not "
             "see it, because its operands are set up by an li several "
             "instructions ahead",
         # The .op line is part of the anchor because rvntt_branch is wired
         # from the same two forwarded operands with the same port names and
         # the same spacing, so the two-line form matches twice.
         edits=[(CORE, "      .op     (id_ex_q.ctrl.muldiv_op),\n"
                       "      .a      (ex_rs1_fwd),\n      .b      (ex_rs2_fwd),",
                       "      .op     (id_ex_q.ctrl.muldiv_op),\n"
                       "      .a      (id_ex_q.rs1_data),\n      .b      (id_ex_q.rs2_data),")],
         caught=["directed:a14_muldiv", "random:muldiv"]),

    dict(step="A14", name="divide_by_zero_quotient_is_zero",
         why="division by zero returns 0 rather than all-ones.  Zero is the "
             "answer an implementation gives when it simply lets the loop run "
             "on a zero divisor and never subtracts, so this is what a MISSING "
             "special case looks like rather than a wrong one",
         edits=[(MD, "      div_by_zero_q ? (want_rem_q ? dividend_q : 32'hFFFF_FFFF)",
                     "      div_by_zero_q ? (want_rem_q ? dividend_q : 32'h0000_0000)")],
         caught=["directed:a14_muldiv", "random:muldiv",
                 "riscv:rv32um/div", "riscv:rv32um/divu"]),

    dict(step="A14", name="divide_by_zero_remainder_is_magnitude",
         why="the remainder of a division by zero is the dividend's MAGNITUDE "
             "rather than the dividend.  Wrong only for a negative dividend "
             "with a zero divisor, which is a two-condition coincidence random "
             "operands never produce -- a14_muldiv.S constructs it on purpose.\n"
             "         rv32um/rem DOES NOT CATCH THIS, and the reason is worth "
             "keeping: its only negative-dividend-over-zero case is "
             "rem(-2^31, 0), and the 32-bit magnitude of -2^31 IS -2^31.  The "
             "one value that makes the mutation invisible is the one the "
             "external suite picked.  a14_muldiv.S uses -12345",
         edits=[(MD, "      dividend_q    <= a;",
                     "      dividend_q    <= a_mag;")],
         caught=["directed:a14_muldiv"]),

    dict(step="A14", name="quotient_loses_bit31",
         why="a positive quotient's top bit is cleared.  This is the SIGNED "
             "OVERFLOW case in the only form it can be broken here: -2^31 / -1 "
             "is not a special case in this divider -- the magnitude loop and "
             "the sign rule produce 0x80000000 between them -- so there is "
             "nothing to delete, and the way to check the general path really "
             "covers it is to break the one bit that only that case needs",
         edits=[(MD, "  wire [31:0] quo_mag = neg_quo_q ? (~quo_q + 32'd1) : quo_q;",
                     "  wire [31:0] quo_mag = neg_quo_q ? (~quo_q + 32'd1) : {1'b0, quo_q[30:0]};")],
         caught=["directed:a14_muldiv", "riscv:rv32um/div", "riscv:rv32um/divu"]),

    dict(step="A14", name="rem_takes_the_divisor_sign",
         why="the remainder takes the sign of the divisor instead of the "
             "dividend.  RISC-V rounds toward zero, so -7 % 2 is -1 and 7 % -2 "
             "is 1; an implementation that got this backwards agrees on every "
             "same-sign pair, which is three quarters of random operands",
         edits=[(MD, "      neg_rem_q     <= a_neg;            // the remainder takes the DIVIDEND's sign",
                     "      neg_rem_q     <= b_neg;            // the remainder takes the DIVIDEND's sign")],
         caught=["directed:a14_muldiv", "random:muldiv", "riscv:rv32um/rem"]),

    dict(step="A14", name="mulhsu_sign_extends_rs2",
         why="MULHSU treats its second operand as signed, which makes it "
             "MULH.  The three high-half multiplies differ only in how the "
             "operands are extended, so this is invisible unless a test uses "
             "an rs2 with its top bit set -- which is why a14_muldiv.S uses "
             "-1 and 0xFFFFFFFF rather than small friendly numbers",
         edits=[(MD, "  wire b_is_signed = (op[1]   == 1'b0);    // MUL and MULH only",
                     "  wire b_is_signed = (op[1:0] != 2'b11);   // MUL and MULH only")],
         caught=["directed:a14_muldiv", "riscv:rv32um/mulhsu"]),

    dict(step="A14", name="mulh_returns_the_low_half",
         why="all four multiplies return the low 32 bits.  MUL is unaffected, "
             "which is the point: a suite that exercised MUL heavily and the "
             "high-half forms once would look healthy",
         edits=[(MD, "      m_hi_q <= (op[1:0] != 2'b00);      // everything but MUL wants the top half",
                     "      m_hi_q <= 1'b0;                    // everything but MUL wants the top half")],
         caught=["directed:a14_muldiv", "random:muldiv",
                 "riscv:rv32um/mulh", "riscv:rv32um/mulhu"]),

    dict(step="A14", name="minstret_counts_stalled_cycles",
         why="minstret is bumped on every cycle a multi-cycle instruction sits "
             "in EX, so a divide counts 34 times.  Nothing architectural moves "
             "and no value changes, so the differ, rv32um and riscv-formal are "
             "all blind to it -- and every IPC and DMIPS number downstream "
             "would be wrong by a factor that depends on the workload's "
             "multiply density, which is exactly the kind of error that gets "
             "believed",
         edits=[(CORE, "      .instret_bump     (id_ex_q.valid && !ex_trap && !ex_stall),",
                       "      .instret_bump     (id_ex_q.valid && !ex_trap),")],
         caught=["csr:a9_minstret"]),

    dict(step="A14", name="idex_bubbles_instead_of_holding",
         why="ID/EX takes a bubble on a multi-cycle stall instead of holding, "
             "which is A7's behaviour applied to A14's stall.  The instruction "
             "is dropped on the floor mid-operation: the unit sees req go low, "
             "aborts, and nothing ever retires",
         edits=[(CORE, "      id_ex_q <= id_ex_q;",
                       "      id_ex_q <= '0;")],
         caught=["directed:a14_muldiv", "random:muldiv", "riscv:rv32um/mul"]),

    dict(step="A14", name="exmem_latches_every_stalled_cycle",
         why="EX/MEM is not bubbled during a multi-cycle stall, so one "
             "instruction retires once per stalled cycle -- 34 times for a "
             "divide, each with whatever the unit's output happened to be.\n"
             "         rv32um/mul DOES NOT CATCH THIS.  The LAST of those "
             "retirements writes the correct value, and a self-checking test "
             "that reads the destination register afterwards sees exactly the "
             "right answer; the three spurious retirements before it leave no "
             "trace it can look at.  Only a differ that compares the whole "
             "COMMIT STREAM against Spike can see a retirement that should not "
             "have happened -- which is the same blind spot minstret has, from "
             "the other side",
         edits=[(CORE, "    end else if (ex_trap || ex_stall) begin",
                       "    end else if (ex_trap) begin")],
         caught=["directed:a14_muldiv", "random:muldiv"]),

    # --------------------------------------------------------------- A15 ----
    # A14's own formal work found a real bug in the RVFI port and nothing else
    # could have.  This entry is what stops it coming back.
    dict(step="A15", name="rvfi_samples_the_last_ex_cycle",
         why="the RVFI shadow reports the operands the forwarding muxes held on "
             "a multi-cycle instruction's LAST EX cycle rather than its first. "
             "By then the producers behind it have drained out of MEM and WB "
             "and the mux has fallen back to the ID-time register value, so "
             "rvfi_rs1_rdata names a value the register does not hold.  The "
             "ARITHMETIC is unaffected -- the unit latched its operands when it "
             "started -- so no commit-log diff, no rv32um test and no Spike "
             "comparison can see it: rvfi_rs1_rdata is not an architectural "
             "value, it is a CLAIM about one.  This is the second time `reg` "
             "has caught something nothing else in the tree could reach",
         edits=[(RVFI, "  assign ex_pkt_eff = ex_first ? ex_pkt : ex_hold_q;",
                       "  assign ex_pkt_eff = ex_pkt;")],
         caught=["rvfi:reg_ch0"]),

    # ... and two that fault-inject the NEW proof, because a proof nobody has
    # broken on purpose is a proof nobody has checked.
    dict(step="A15", name="divider_never_restores",
         why="the restoring step keeps the difference whether or not the "
             "subtract fitted, so the remainder goes negative and stays wrong. "
             "Aimed at the standalone proof's algebraic statement -- q*|b| + r "
             "== |a| with r < |b| -- which pins q and r completely without "
             "describing how either was produced",
         edits=[(MD, "      rem_q <= fits ? diff[31:0] : shifted[31:0];",
                     "      rem_q <= diff[31:0];")],
         caught=["formal:rvntt_muldiv", "directed:a14_muldiv",
                 "riscv:rv32um/div", "riscv:rv32um/rem"]),

    dict(step="A15", name="divider_quotient_bit_always_set",
         why="every quotient bit is 1 regardless of whether the divisor fitted. "
             "The remainder loop still runs correctly, so a proof that checked "
             "only `r < |b|` would pass -- it is the multiplicative identity "
             "that fails, which is exactly the half of the property that was "
             "expensive enough to be tempting to leave out",
         edits=[(MD, "      quo_q <= {quo_q[30:0], fits};",
                     "      quo_q <= {quo_q[30:0], 1'b1};")],
         caught=["formal:rvntt_muldiv", "directed:a14_muldiv",
                 "riscv:rv32um/div", "riscv:rv32um/divu"]),

    # ---- A17: the dedicated address adder ---------------------------------
    # The adder exists for timing, so most of what could go wrong with it is
    # invisible to timing and visible only here.  Note what is NOT in this
    # list: pointing the misalignment check back at ex_alu_y.  That is the
    # fault injection MODS_A A17 asks for, and it is deliberately absent,
    # because a_addr_adder_matches_alu proves the two are the same number --
    # so it is a NO-OP by construction and only an implementation run can see
    # it.  It is done as a timing experiment instead; see docs/a17-fmax.md.
    dict(step="A17", name="addr_adder_drops_carry_into_bit2",
         why="the address adder carries within [1:0] and within [31:2] but not "
             "between them.  Chosen because it is nearly invisible: an aligned "
             "base with an aligned offset never carries out of bit 1, so every "
             "word access in the benchmarks is unaffected and only a sub-word "
             "access at an odd offset is wrong.  It is the case where the "
             "formal assertion earns its place over the directed tests",
         edits=[(CORE, "  wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;",
                       "  wire [31:0] ex_mem_addr = {ex_rs1_fwd[31:2] + id_ex_q.imm[31:2],\n"
                       "                             ex_rs1_fwd[1:0] + id_ex_q.imm[1:0]};")],
         caught=["rvfi:reg_ch0", "random:raw"]),

    dict(step="A17", name="addr_adder_reads_the_register_file_not_forwarding",
         why="the new adder takes rs1 from the ID/EX register instead of the "
             "forwarding mux.  This is the specific mistake a second adder "
             "invites -- the ALU's operand already went through forwarding, so "
             "duplicating the arithmetic without duplicating the mux computes "
             "an address from a value that is one or two instructions stale",
         edits=[(CORE, "  wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;",
                       "  wire [31:0] ex_mem_addr = id_ex_q.rs1_data + id_ex_q.imm;")],
         caught=["rvfi:reg_ch0", "directed:a6_forward", "random:raw", "bench"]),

    dict(step="A17", name="store_byte_offset_from_the_alu_low_bits",
         why="the byte-enable shift keeps reading ex_alu_y[1:0] while the "
             "address comes from the adder.  Today those agree, so this is the "
             "shape of a bug that arrives LATER: it is the line that would "
             "have to be edited too if the adder ever stopped matching, and "
             "leaving it behind is how a store lands in the wrong byte lane.  "
             "NOT caught by soc, and that is structural rather than a gap in "
             "the test: every MMIO access is a word access by the register "
             "map's own rule, and `sw` sets all four byte enables whatever the "
             "offset says.  A byte-lane fault cannot reach the UART",
         edits=[(CORE, "  assign ex_byte_off = ex_mem_addr[1:0];",
                       "  assign ex_byte_off = ex_alu_y[1:0] ^ 2'b01;")],
         caught=["bench", "random:raw"]),

    # ---- A19: the branch predictor ----------------------------------------
    # FOUR OF THESE SIX ARE ARCHITECTURALLY INVISIBLE.  The core retires the
    # same instructions in the same order with every one of them applied, so
    # no commit-log diff, no riscv-formal check and no compliance test can see
    # them -- they are caught by tb/cosim/cycle_model.py's SPAN check and
    # nowhere else.  MODS_A A19 asks for exactly that ("at least one must be
    # caught by the cycle model rather than by a correctness check"), and it is
    # the reason the cycle model was taught the predictor from
    # docs/a19-bpred-spec.md rather than from this RTL.
    dict(step="A19", name="btb_tag_compared_against_the_wrong_bits",
         why="the stored tag is compared against the lookup tag ROTATED LEFT "
             "by one -- an off-by-one slice, and the most ordinary way to get "
             "a tag comparison wrong.  Rotated rather than shifted so the top "
             "bit stays referenced: a shift leaves it unread and the harness "
             "rejects the mutation for not compiling, correctly.  A correct entry then never matches its "
             "own address, so every transfer misses and the predictor buys "
             "nothing at all while still costing its LUTs.  "
             "NOT covered by this or any other test here: a wrong tag "
             "that MATCHES -- a false hit.  Producing one needs two hot branch "
             "sites whose tags collide under the specific broken comparison, "
             "which is tuning a program to a mutation rather than testing a "
             "property.  The stimulus that would cover it is a long random "
             "program containing loops, and gen_random_prog.py emits "
             "forward-only branches by design.  Recorded, not papered over",
         edits=[(BP, "  wire        lk_hit  = btb_valid_q[lk_i] && (lk_e[G_LSB +: TAG_W] == lk_t);",
                     "  wire        lk_hit  = btb_valid_q[lk_i] && (lk_e[G_LSB +: TAG_W] == {lk_t[TAG_W-2:0], lk_t[TAG_W-1]});")],
         caught=["directed:a19_bpred"]),

    dict(step="A19", name="bpred_counter_wraps_instead_of_saturating",
         why="a strongly-taken branch becomes strongly-not-taken on ONE "
             "not-taken execution.  The classic two-bit-counter bug, and the "
             "reason a 2-bit counter is worth more than a 1-bit one at all -- "
             "with it, a loop mispredicts twice per exit instead of once",
         edits=[(BP, "                         ? ((up_cnt == 2'b11) ? 2'b11 : up_cnt + 2'b01)",
                     "                         ? (up_cnt + 2'b01)")],
         caught=["directed:a19_bpred"]),

    dict(step="A19", name="ras_push_condition_dropped",
         why="calls no longer push, so the return stack is always empty and "
             "every return falls through to its BTB entry -- which holds "
             "whichever caller returned there last",
         edits=[(BP, "  wire up_call = upd_valid && (upd_kind == rv32i_pkg::BP_CALL);",
                     "  wire up_call = 1'b0;")],
         caught=["directed:a19_bpred"]),

    # THE VALID BIT HAS NO MUTATION, and the reason is worth recording rather
    # than leaving as an eight-entry list where a reader expects nine.
    # Dropping `btb_valid_q[lk_i]` from the hit test is a NO-OP in simulation:
    # an entry that was never written reads as all zeros, so its tag is zero,
    # and nothing in this project executes below address 0x400 -- the tag
    # comparison alone rejects it.  The valid bit is therefore defence in depth
    # against a memory that does NOT power up zeroed, which is the case the
    # specification's section 6 reset argument is written for and the case no
    # simulation here can produce.  A mutation that cannot fail is not evidence.
    dict(step="A19", name="btb_never_replaces_a_live_entry",
         why="the BTB refuses to overwrite an entry belonging to a different "
             "address -- a plausible 'do not thrash' policy, and wrong: with a "
             "direct-mapped array the second site to reach an index would then "
             "never be predicted at all, forever.  ARCHITECTURALLY INVISIBLE, "
             "and the mutation that case 8 of a19_bpred.S exists for: two hot "
             "branches one kilobyte apart share index 0, and correct behaviour "
             "is that they evict each other on every pass",
         edits=[(BP, "  wire btb_we = upd_valid && (upd_taken || (up_hit && up_isbr));",
                     "  wire btb_we = upd_valid && (upd_taken || (up_hit && up_isbr))\n"
                     "                          && (!btb_valid_q[up_i] || up_hit);")],
         caught=["directed:a19_bpred"]),

    dict(step="A19", name="mispredict_ignores_the_target",
         why="the check compares only the DIRECTION.  A predicted-taken "
             "transfer whose target moved is accepted, and the wrong "
             "instruction retires -- which makes this the one predictor "
             "mutation the commit log can see, and the reason the target "
             "comparison is not an optimisation",
         edits=[(CORE, "                       ((id_ex_q.pred_taken != ex_ctrl_xfer) ||\n"
                       "                        (ex_ctrl_xfer &&\n"
                       "                         (id_ex_q.pred_target != ex_jump_target)));",
                       "                       (id_ex_q.pred_taken != ex_ctrl_xfer);")],
         caught=["directed:a19_bpred", "rvfi:pc_fwd_ch0"]),

    dict(step="A19", name="prediction_outranks_the_redirect",
         why="the PC mux prefers a prediction to an EX redirect.  IF has seen "
             "an address; EX has seen the instruction, and it wins -- getting "
             "that priority backwards means a mispredict is never actually "
             "recovered from",
         edits=[(CORE, "    if      (ex_redirect)   pc_next = ex_redirect_target;\n"
                       "    else if (front_stall)   pc_next = pc_q;\n"
                       "    else if (bp_pred_taken) pc_next = bp_pred_target;",
                       "    if      (bp_pred_taken) pc_next = bp_pred_target;\n"
                       "    else if (ex_redirect)   pc_next = ex_redirect_target;\n"
                       "    else if (front_stall)   pc_next = pc_q;")],
         caught=["directed:a19_bpred", "rvfi:pc_fwd_ch0"]),

    dict(step="A19", name="rvfi_pc_wdata_falls_through_a_taken_branch",
         why="pc_wdata comes from the trap vector or from pc + 4, never from "
             "the branch target.  This is the shape of the bug A19 nearly "
             "shipped: pc_wdata used to be gated on ex_redirect, and a "
             "correctly predicted taken branch no longer redirects, so the old "
             "expression would have reported a fall-through on exactly the "
             "branches the predictor got RIGHT.  ex_redirect was deleted from "
             "this module's port list so that expression cannot be written at "
             "all; this mutation is what proves the property that made the "
             "deletion necessary",
         edits=[(RVFI, "    ex_pkt.pc_wdata = ex_redirect_target;",
                       "    ex_pkt.pc_wdata = ex_trap ? ex_redirect_target\n"
                       "                              : (ex_pc + 32'd4);")],
         caught=["rvfi:pc_fwd_ch0"]),

    dict(step="A19", name="bpred_allocates_only_on_branches",
         why="jumps, calls and returns never get a BTB entry -- only branches "
             "do.  A plausible misreading of the specification's 'allocate "
             "only on a taken resolution', and ARCHITECTURALLY PERFECT: the "
             "core retires exactly the same instructions in exactly the same "
             "order.  It simply throws away most of the predictor's value, "
             "because JAL, JALR and returns are a third of Dhrystone's "
             "control transfers.  The span is the only thing that says so",
         edits=[(BP, "  wire btb_we = upd_valid && (upd_taken || (up_hit && up_isbr));",
                     "  wire btb_we = upd_valid && up_isbr && (upd_taken || up_hit);")],
         caught=["directed:a19_bpred"]),

    # ---------------------------------------------------------------- A20
    # The counters are OBSERVATIONAL: nothing in the datapath reads them, so
    # every mutation here is architecturally perfect by construction and the
    # commit log is byte-identical with all of them applied.  What catches them
    # is either the directed CSR contract test or -- for the two that get the
    # ATTRIBUTION wrong rather than the plumbing -- the comparison against A18's
    # instrument, which is the reason that comparison exists.
    dict(step="A20", name="mcountinhibit_does_not_inhibit_mcycle",
         why="the inhibit bit is stored, and reads back correctly, and does "
             "nothing.  The plausible version of this bug: the register is "
             "implemented as a register and nobody wires it to the counter.  "
             "Every test that reads mcountinhibit passes; only one that "
             "inhibits and then watches the counter can see it",
         edits=[(CSR, "      if (!inhibit_cy_q) mcycle_q <= mcycle_q + 64'd1;",
                      "      mcycle_q <= mcycle_q + 64'd1;")],
         caught=["csr:a20_hpm"]),

    dict(step="A20", name="unimplemented_counters_trap_instead_of_reading_zero",
         why="mhpmcounter9..31 become illegal instructions rather than "
             "read-only zero.  The spec requires an unimplemented counter to "
             "read zero and NOT to fault, and the difference only shows when "
             "software probes for how many counters exist -- which is exactly "
             "what software does with this extension",
         edits=[(CSR, "        known = hpm_any;", "        known = hpm_any && hpm_impl;")],
         caught=["csr:a20_hpm"]),

    dict(step="A20", name="the_event_selector_is_not_warl",
         why="an out-of-range write to mhpmevent lands, so a counter can be "
             "programmed to count an event number that does not exist.  The "
             "increment then indexes the event bus out of range",
         edits=[(CSR, "              else if (hpm_evt && wdata[3:0] <= rv32i_pkg::HPM_EV_MAX && wdata[31:4] == '0)",
                      "              else if (hpm_evt)")],
         caught=["csr:a20_hpm"]),

    # ---------------------------------------------------------------- A21
    # B (Zba+Zbb+Zbs) and Zbkb.  Every one of these is architecturally VISIBLE
    # -- a wrong bit-manipulation result is a wrong register value -- so the
    # catchers are the ones that compare architectural state: RISCOF's B suite,
    # the random cosim against Spike, and rvntt_bitmanip's own proof.
    dict(step="A21", name="rev8_and_brev8_swapped",
         why="rev8 reverses BYTES and brev8 reverses BITS WITHIN each byte. "
             "They sound alike, they are adjacent in the encoding (imm[11:5] "
             "0110100 for both, differing only in the rs2 field), and swapping "
             "them is the ordinary mistake.  Both are involutions, so a "
             "round-trip test would pass with them swapped",
         # BOTH arms are swapped.  Pointing rev8 at brev8_v alone leaves
         # rev8_v unreferenced and Verilator rejects the build -- and a
         # mutation that does not compile proves nothing.
         edits=[(BM,
                 "      rv32i_pkg::BM_REV8:   y = rev8_v;",
                 "      rv32i_pkg::BM_REV8:   y = brev8_v;"),
                (BM,
                 "      rv32i_pkg::BM_BREV8:  y = brev8_v;",
                 "      rv32i_pkg::BM_BREV8:  y = rev8_v;")],
         caught=["formal:rvntt_bitmanip", "random:bitmanip"]),

    dict(step="A21", name="ctz_of_zero_returns_31",
         why="the specification DEFINES clz(0) = ctz(0) = 32.  31 is the "
             "off-by-one an implementation reaches by counting positions "
             "instead of naming the absent bit, and it is wrong for exactly "
             "one input out of 2^32 -- which random stimulus will not find",
         edits=[(BM, "    ctz_v = 6'd32;", "    ctz_v = 6'd31;")],
         caught=["formal:rvntt_bitmanip"]),

    dict(step="A21", name="rotate_complement_off_by_one",
         why="the complementary shift becomes 31 - shamt instead of -shamt, so "
             "every rotate is off by one bit.  THE FIRST VERSION OF THIS "
             "MUTATION WAS `6'd32 - shamt` AND IT ESCAPED -- correctly, "
             "because on a 32-bit target `a << 32` is zero and that "
             "formulation is a valid alternative implementation, not a bug.  "
             "Worth recording: an escaped mutation is sometimes evidence that "
             "the mutation was wrong, not that the checks are weak.  This one "
             "is genuinely wrong, and rotate-by-zero -- which gen_random_prog "
             "constructs rather than waits for -- is where it shows first",
         edits=[(BM, "  wire [4:0] rshamt = 5'd0 - shamt;",
                     "  wire [4:0] rshamt = 5'd31 - shamt;")],
         caught=["formal:rvntt_bitmanip", "random:bitmanip"]),

    dict(step="A21", name="bitmanip_result_never_reaches_writeback",
         why="the EX result mux ignores is_bitmanip, so every B instruction "
             "writes back the ALU's output instead.  The unit is correct, the "
             "decoder is correct, and the answer is wrong -- which is what the "
             "separate-result-lane design of MODS_A2 section 3.4 costs if the "
             "lane is not actually selected",
         edits=[(CORE, "    else if (id_ex_q.ctrl.is_bitmanip) ex_result = ex_bm_result;",
                       "    else if (1'b0) ex_result = ex_bm_result;")],
         caught=["random:bitmanip"]),

    dict(step="A21", name="unary_group_ignores_the_rs2_field",
         why="clz, ctz, cpop, sext.b and sext.h share opcode, funct3 AND "
             "imm[11:5] and differ ONLY in the rs2 field.  Treating that field "
             "as a don't-care makes the decoder accept rs2 = 3, 6 and 7, which "
             "are RESERVED and must trap.  ARCHITECTURALLY INVISIBLE to every "
             "functional test, because no functional test emits a reserved "
             "encoding -- this is the exact class A3's 10^6-word sweep exists "
             "for, and the only thing here that catches it",
         edits=[(DEC,
                 "          rv32i_pkg::RS2_SEXTH: bm_op_i = rv32i_pkg::BM_SEXTH;\n"
                 "          default:              bm_op_i = rv32i_pkg::BM_NONE;",
                 "          rv32i_pkg::RS2_SEXTH: bm_op_i = rv32i_pkg::BM_SEXTH;\n"
                 "          default:              bm_op_i = rv32i_pkg::BM_CLZ;")],
         caught=["cocotb:decode"]),

    # TWO A20 MUTATIONS ARE DELIBERATELY NOT IN THIS MANIFEST, and both are
    # recorded here rather than left out silently.
    #
    # "the stall tie is broken the wrong way" -- hpm_event[LOADUSE] made
    # `id_stall` instead of `id_stall && !ex_stall`.  IT IS NOT HERE BECAUSE IT
    # IS NOT A MUTATION: it was injected, the counters came back identical on
    # both benchmarks, and the reason is that the two stalls are DISJOINT BY
    # CONSTRUCTION -- id_stall requires a load in EX, ex_stall requires a
    # multiply or divide there, and one instruction cannot be both.  The guard
    # is a semantically-equivalent rewrite, so removing it mutates nothing and
    # the harness would rightly call it a NO-OP.
    #
    # The disjointness is now asserted in rvntt_core.sv
    # (a_stalls_are_disjoint) and proved by every riscv-formal check at depth
    # 14, which is a stronger statement than any mutation of it could be.  This
    # note is kept because the ORIGINAL comment in the core claimed the guard
    # was load-bearing, and fault injection is what showed it was not.
    #
    # "BTB hit counted when the prediction was suppressed" -- pred_hit ignoring
    # `flush`, so the instruction at a redirect target is recorded as a hit even
    # though no lookup described its address.  NOTHING HERE CATCHES IT, and that
    # is a real gap rather than an oversight: the retired instruction stream
    # contains no evidence about whether the BTB held an entry, so no check
    # built on it can see the difference.  run_stall_profile.py reports the hit
    # count rather than checking it, and says so.  Closing this needs an RTL
    # assertion relating pred_hit to the previous cycle's flush, which is
    # rvntt_bpred's formal job and not a mutation's.
]
# ------------------------------------------------------------------- the tests
RANDOM_SUITES = {
    # Small on purpose: these run once per mutation, and the acceptance runs
    # (1000 programs) are a separate thing.  A mutation that needs more than a
    # handful of random programs to show up is a mutation the random suite
    # should not be credited with catching.
    "raw":      dict(n=8, length=250, raw=1.0, lu=0.0, br=0.0, mul=0.0,
                    seed=0xA6000000),
    "loaduse":  dict(n=8, length=250, raw=1.0, lu=1.0, br=0.0, mul=0.0,
                    seed=0xA7000000),
    "branch":   dict(n=8, length=250, raw=1.0, lu=1.0, br=0.12, mul=0.0,
                    seed=0xA8000000),
    # A14.  mul is 0.12 and not higher on purpose: a 34-cycle divide is 34
    # cycles in which nothing else is exercised, so a denser stream would trade
    # away the hazard coverage that makes these programs worth running at all.
    # A21.  B and Zbkb at a density that makes them the majority of the
    # program, because the mutations here are single-operation bugs and a
    # 29-way choice means each operation appears rarely at a low density.
    "bitmanip": dict(n=8, length=250, raw=1.0, lu=1.0, br=0.10, mul=0.0,
                     bm=0.35, seed=0xB1000000),
    "muldiv":   dict(n=8, length=250, raw=1.0, lu=1.0, br=0.12, mul=0.12,
                    seed=0xA1400000),
}


def mirror_rtl(work, name, edits):
    """Copy the RTL tree, apply `edits`, return (dir, ok)."""
    d = os.path.join(work, "rtl_" + name)
    for p in t4.RTL + [os.path.join(ROOT, x) for x in MIRROR_EXTRA]:
        dst = os.path.join(d, os.path.relpath(p, ROOT))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(p, dst)
    for rel, old, new in edits:
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            return d, f"{rel} is not in the build's source list"
        s = open(fp).read()
        n = s.count(old)
        if n == 0:
            return d, f"anchor not found in {rel}"
        # AN AMBIGUOUS ANCHOR IS WORSE THAN A MISSING ONE, and until A14 this
        # went unchecked.  `replace(old, new, 1)` hits whichever match comes
        # first in the file, so a second match somewhere unrelated makes the
        # mutation silently break a DIFFERENT part of the design -- and the
        # verdict then says something true about a bug nobody meant to inject.
        # A14 created exactly that: rvntt_muldiv is instantiated from the same
        # two forwarded operands, with the same port names and the same spacing,
        # as rvntt_branch, so A8's `.a (ex_rs1_fwd),` anchor began matching the
        # multiplier first.  It surfaced as a PARTIAL -- the right smell for
        # entirely the wrong reason.
        if n > 1:
            return d, (f"anchor matches {n} times in {rel} -- ambiguous, so the "
                       f"mutation would land on whichever comes first")
        open(fp, "w").write(s.replace(old, new, 1))
    return d, None


def build(work, name, rtl_dir, image):
    """Build the traced simulator from `rtl_dir`.  Returns (exe, error)."""
    build_dir = os.path.join(work, "obj_" + name)
    srcs = [os.path.join(rtl_dir, os.path.relpath(p, ROOT)) for p in t4.RTL]
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4", "-Wall",
           "--top-module", "rvntt_trace_top",
           "--Mdir", build_dir, "--prefix", "Vrvntt_trace_top",
           "-CFLAGS", "-DVTOP=Vrvntt_trace_top",
           '-GINIT_FILE="%s"' % image, "-GWORDS=16384"] + srcs + a5.TRACE_RTL + [a5.TB]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        tail = r.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        return None, " / ".join(tail)
    return os.path.join(build_dir, "Vrvntt_trace_top"), None


# Depth is picked from the deepest property, never from a default -- the rule
# rtl/core/CLAUDE.md states for every proof here.  rvntt_muldiv is the first
# module whose deepest property is a LATENCY rather than a dependency distance:
# a divide presents its result on its 34th EX cycle, so nothing about it can be
# observed before step 34.
FORMAL_DEPTH = {"rvntt_muldiv": 37}
FORMAL_DEPTH_DEFAULT = 8


def run_formal(work, name, rtl_dir, design):
    """Run SymbiYosys on the (possibly mutated) design.  True if it PASSES."""
    src = os.path.join(rtl_dir, "rtl/core", design + ".sv")
    if not os.path.exists(src):
        src = os.path.join(rtl_dir, "rtl/soc", design + ".sv")
    # with_deps resolves packages against the real tree, so a mutated module
    # is read against the unmutated package -- which is what is wanted: no
    # mutation here touches rv32i_pkg.
    srcs = with_deps(src)
    wd = os.path.join(work, "formal_%s_%s" % (name, design))
    os.makedirs(wd, exist_ok=True)
    sby = os.path.join(wd, design + ".sby")
    reads = "\n".join("read -formal %s" % os.path.basename(s) for s in srcs)
    open(sby, "w").write(
        "[options]\nmode bmc\ndepth %d\n\n[engines]\nsmtbmc bitwuzla\n\n"
        "[script]\nread -define FORMAL\n%s\nprep -top %s\n\n[files]\n%s\n"
        % (FORMAL_DEPTH.get(design, FORMAL_DEPTH_DEFAULT),
           reads, design, "\n".join(srcs)))
    r = subprocess.run(["sby", "-f", sby], cwd=wd,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def run_rvfi(rtl_dir, check):
    """Run ONE riscv-formal check against the mutated tree.  True if it PASSES.

    One check per manifest entry rather than the whole set, for the same reason
    the random suites here are eight programs and not a thousand: the entry is a
    claim about WHICH check sees the bug, and running all 43 would let one broad
    check be credited with everything.  It also keeps the cost sane -- the full
    set is about 40s wall, a single check two to ten.
    """
    r = subprocess.run([sys.executable, RVFI_RUNNER, "--rtl-dir", rtl_dir,
                        "--only", check, "-j", "1"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode not in (0, 1):
        # Exit 2 means the design would not elaborate or the solver gave up.
        # rvntt_rvfi.sv is not in the simulator's source list, so build() cannot
        # vet a mutation to it -- and a mutation that does not compile must not
        # be counted as caught.  Loud, not silent.
        raise RuntimeError("riscv-formal could not run %s:\n%s"
                           % (check, r.stdout.decode("utf-8", "replace")[-2000:]))
    return r.returncode == 0


def run_program_test(exe, elf, work, image, tag):
    """True if the commit logs match (i.e. the test PASSES)."""
    return a5.run_one(exe, elf, work, image, tag, verbose=False)


class Fixture:
    """Everything that does not depend on the mutation, built once."""

    def __init__(self, work, image):
        self.work = work
        self.image = image
        self.elfs = {}

    def directed_elf(self, prog):
        if prog not in self.elfs:
            src = dict(dr.PROGRAMS)[prog]
            self.elfs[prog] = dr.compile_s(src, self.work, "d_" + prog)
        return self.elfs[prog]

    def a4_elf(self):
        if "a4" not in self.elfs:
            self.elfs["a4"] = t4.build_elf(self.work)
        return self.elfs["a4"]

    def riscv_elf(self, suite, name):
        key = "rv_%s_%s" % (suite, name)
        if key not in self.elfs:
            elf, err = rvt.compile_test(suite, name, self.work)
            if elf is None:
                raise RuntimeError("compiling %s failed: %s" % (key, err))
            self.elfs[key] = elf
        return self.elfs[key]

    def csr_elf(self, name):
        key = "csr_" + name
        if key not in self.elfs:
            self.elfs[key] = ct.compile_s(
                os.path.join(ROOT, "sw/tests", name + ".S"), self.work, key)
        return self.elfs[key]

    def random_elfs(self, suite):
        key = "r_" + suite
        if key not in self.elfs:
            cfg = RANDOM_SUITES[suite]
            out = []
            for i in range(cfg["n"]):
                seed = cfg["seed"] + i
                src = gen_random_prog.generate(seed, cfg["length"], None,
                                               cfg["raw"], cfg["lu"], cfg["br"],
                                               cfg["mul"], cfg.get("bm", 0.0))
                out.append(a5.assemble(src, self.work, "%s_%d" % (key, i)))
            self.elfs[key] = out
        return self.elfs[key]


def run_test(kind, fx, exe, rtl_dir, work, mut_name, quiet=True):
    """Run one named test.  True = PASSED (so False = caught the mutation)."""
    # A failing cosim prints its whole first-divergence report.  That is the
    # right behaviour for the acceptance runs and the wrong one here, where a
    # failure is the EXPECTED outcome and there may be dozens of them: the
    # verdict table is the report.  The baseline run is not quiet, because
    # there a failure is real and its detail is the point.
    if quiet:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return _run_test(kind, fx, exe, rtl_dir, work, mut_name)
    return _run_test(kind, fx, exe, rtl_dir, work, mut_name)


def run_soc(rtl_dir, work, mut_name):
    """Build and run the A12 SoC testbench from a mirrored RTL tree.

    Unlike the core tests this one has its own Verilator build (a different top,
    a different memory image), so it gets its own object directory per mutation
    -- sharing one would have each mutation silently rebuild over the last.
    """
    build = os.path.join(work, "obj_soc_" + mut_name)
    r = subprocess.run([sys.executable, SOC_RUNNER,
                        "--rtl-dir", rtl_dir, "--build-dir", build],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    # A mutation that fails to BUILD proves nothing -- it has to produce a
    # design that elaborates and then misbehaves.  Distinguish the two.
    if "SOC_TB_OK" not in out and "SOC_TB_FAIL" not in out:
        raise RuntimeError("the SoC testbench did not run for %s:\n%s"
                           % (mut_name, out[-2500:]))
    return r.returncode == 0


def run_bench(rtl_dir, work, mut_name):
    """Build and run the A13 benchmark image on a mirrored RTL tree.

    Deliberately tiny counts: this is asking whether the mutation is VISIBLE,
    not how fast the core is, and 30 Dhrystone runs plus one CoreMark iteration
    already exercise every path a full run does.  One block, because the
    block-to-block reproducibility check is about the machine being
    deterministic, which no mutation here is meant to break.
    """
    build = os.path.join(work, "obj_bench_" + mut_name)
    r = subprocess.run([sys.executable, BENCH_RUNNER,
                        "--rtl-dir", rtl_dir, "--build-dir", build,
                        "--dhry-runs", "30", "--iterations", "1",
                        "--blocks", "1", "--max-cycles", "20000000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    # As with run_soc: a mutation that does not elaborate proves nothing.  The
    # testbench prints BENCH_TB_OK or BENCH_TB_FAIL once the design has built
    # and run, so its absence means the build died.
    if "BENCH_TB_OK" not in out and "BENCH_TB_FAIL" not in out:
        raise RuntimeError("the benchmark testbench did not run for %s:\n%s"
                           % (mut_name, out[-2500:]))
    return r.returncode == 0


def _run_test(kind, fx, exe, rtl_dir, work, mut_name):
    if kind.startswith("formal:"):
        return run_formal(work, mut_name, rtl_dir, kind.split(":", 1)[1])
    if kind.startswith("rvfi:"):
        return run_rvfi(rtl_dir, kind.split(":", 1)[1])
    if kind == "soc":
        return run_soc(rtl_dir, work, mut_name)
    if kind == "bench":
        return run_bench(rtl_dir, work, mut_name)
    if kind.startswith("directed:"):
        prog = kind.split(":", 1)[1]
        return run_program_test(exe, fx.directed_elf(prog), work, fx.image, prog)
    if kind.startswith("riscv:"):
        suite, name = kind.split(":", 1)[1].split("/")
        ok, _out = rvt.run_rtl(exe, fx.riscv_elf(suite, name), work, fx.image)
        return ok
    if kind.startswith("csr:"):
        name = kind.split(":", 1)[1]
        elf = fx.csr_elf(name)
        extra = []
        if name == "a9_minstret":
            # The expected value comes from Spike and is a property of the
            # PROGRAM, so it is computed once against unmutated behaviour and
            # reused: a mutation must not be allowed to move the goalposts.
            if "a9_minstret_expect" not in fx.elfs:
                fx.elfs["a9_minstret_expect"] = ct.spike_commits_before(elf, "probe")
            extra = ["--expect", str(fx.elfs["a9_minstret_expect"]), "--reg", "9"]
        ok, _out = ct.run(exe, elf, work, fx.image, extra)
        return ok
    if kind.startswith("cocotb:"):
        # A21.  The A3 decoder equivalence sweep -- 10^6 random words, RTL
        # against model/rv32i_ref.py -- is the ONLY thing here that can see a
        # reserved-field mutation, because no functional test ever emits a
        # reserved encoding.  It costs about 45 seconds, which is why exactly
        # one mutation names it.
        #
        # It runs against the MUTATED rtl dir, which means the flat wrapper and
        # the package have to come from there too.
        design = kind.split(":", 1)[1]
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "tb/cocotb/run_cocotb.py"),
                            "--design", design, "--rtl-dir", rtl_dir],
                           cwd=os.path.join(ROOT, "tb/cocotb"),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode == 0

    if kind == "a4":
        return run_program_test(exe, fx.a4_elf(), work, fx.image, "a4")
    if kind.startswith("random:"):
        suite = kind.split(":", 1)[1]
        for i, elf in enumerate(fx.random_elfs(suite)):
            if not run_program_test(exe, elf, work, fx.image, "%s#%d" % (suite, i)):
                return False
        return True
    raise ValueError("unknown test kind: " + kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default=None, help="only mutations for this step")
    ap.add_argument("--only", default=None, help="one mutation by name")
    ap.add_argument("--check-anchors", action="store_true",
                    help="verify every edit's search text still matches its "
                         "file exactly once, and stop.  Takes a tenth of a "
                         "second; the full run takes a quarter of an hour.")
    a = ap.parse_args()

    muts = [m for m in MUTATIONS
            if (a.step is None or m["step"] == a.step)
            and (a.only is None or m["name"] == a.only)]
    if not muts:
        print("no mutations selected")
        return 2

    # ---- the anchor pre-flight.
    #
    # WHY THIS EXISTS, AND WHY IT IS SEPARATE FROM THE RUN.  A mutation whose
    # search text no longer appears in the file mutates nothing, and the harness
    # correctly reports it as NO-OP -- but only after building and running
    # everything else, a quarter of an hour later, in a report long enough that
    # the two NO-OP lines landed above run_regress.py's output tail and were
    # invisible until that truncation was fixed.
    #
    # ANCHORS HAVE NOW GONE STALE FIVE TIMES IN THIS PROJECT, every time for the
    # same reason: a later step edited the line a mutation was anchored to.  The
    # check is a string search.  It costs a tenth of a second and it is the
    # difference between "your rename broke two mutations" arriving now and
    # arriving after the next full regression.
    stale = []
    srcs = {}
    for m in muts:
        for path, old_text, _new_text in m["edits"]:
            full = os.path.join(ROOT, path)
            if path not in srcs:
                srcs[path] = io.open(full, encoding="utf-8").read()
            n = srcs[path].count(old_text)
            if n != 1:
                stale.append((m["name"], path, n))
    if a.check_anchors or stale:
        print("anchor check: %d edit(s) across %d mutation(s)"
              % (sum(len(m["edits"]) for m in muts), len(muts)))
        for name, path, n in stale:
            print("  STALE  %-46s %s matches %d time(s)" % (name, path, n))
        if stale:
            print("\n%d STALE ANCHOR(S) -- these mutations would mutate nothing "
                  "and be reported as NO-OP after a full run.  A later step "
                  "almost certainly edited the line they anchor to; re-read the "
                  "file and refresh the search text." % len(stale))
            return 1
        print("  all anchors match exactly once")
        if a.check_anchors:
            return 0

    work = tempfile.mkdtemp(prefix="mutate_")
    problems = 0
    try:
        image = os.path.join(work, "image.hex")
        open(image, "w").write("00000000\n")
        fx = Fixture(work, image)

        # ---- baseline: unmutated RTL must pass everything the manifest names.
        wanted = sorted({t for m in muts for t in m["caught"]})
        base_dir, err = mirror_rtl(work, "base", [])
        assert err is None, err
        base_exe, err = build(work, "base", base_dir, image)
        if base_exe is None:
            print("BASELINE BUILD FAILED: " + err)
            return 1
        print("baseline (unmutated RTL):")
        for kind in wanted:
            ok = run_test(kind, fx, base_exe, base_dir, work, "base",
                          quiet=False)
            print("  %-28s %s" % (kind, "pass" if ok else "FAIL <-- stuck-at-fail"))
            if not ok:
                problems += 1
        if problems:
            print("\nbaseline is not clean; mutation results would be meaningless")
            return 1

        # ---- the mutations.
        print("\n%-32s %-10s %s" % ("mutation", "verdict", "caught by"))
        print("-" * 96)
        for m in muts:
            d, err = mirror_rtl(work, m["name"], m["edits"])
            if err:
                print("%-32s %-10s %s" % (m["name"], "NO-OP", err))
                problems += 1
                continue
            exe, err = build(work, m["name"], d, image)
            if exe is None:
                print("%-32s %-10s %s" % (m["name"], "BUILD-ERR", err))
                print("    A mutation that does not compile proves nothing; "
                      "reformulate it so every bit stays referenced.")
                problems += 1
                continue

            caught_by, missed = [], []
            for kind in m["caught"]:
                if run_test(kind, fx, exe, d, work, m["name"]):
                    missed.append(kind)
                else:
                    caught_by.append(kind)

            if not m["caught"]:
                print("%-32s %-10s (no catcher declared -- see the manifest)"
                      % (m["name"], "UNCHECKED"))
            elif not caught_by:
                print("%-32s %-10s declared: %s" % (m["name"], "ESCAPED",
                                                    ", ".join(m["caught"])))
                problems += 1
            elif missed:
                print("%-32s %-10s caught: %s | NOT by: %s"
                      % (m["name"], "PARTIAL", ", ".join(caught_by),
                         ", ".join(missed)))
                problems += 1
            else:
                print("%-32s %-10s %s" % (m["name"], "CAUGHT",
                                          ", ".join(caught_by)))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n=== %s ===" % ("ALL MUTATIONS CAUGHT AS DECLARED" if problems == 0
                            else "%d PROBLEM(S)" % problems))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
