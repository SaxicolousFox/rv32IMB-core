// ============================================================================
// rvntt_hazard -- the load-use interlock (plan A7).
//
// Plan A7 states the rule exactly:
//
//   if EX.mem_read && (EX.rd == ID.rs1 || EX.rd == ID.rs2) && EX.rd != 0
//   then stall IF and ID for one cycle and inject a bubble into EX.
//
// ONE cycle is enough, and it is worth being explicit about why, because the
// number depends on this pipeline's memory timing rather than on the textbook.
// With the consumer in ID and the load in EX:
//
//   without the stall, the consumer reaches EX while the load is in MEM, and
//   the MEM-stage forward deliberately does not carry load data (see
//   rvntt_forward.sv -- it would put the BRAM output on the ALU's input path);
//
//   with one stall cycle, the consumer reaches EX while the load is in WB, and
//   FWD_WB carries load data from a register output.
//
// So the interlock and the FWD_MEM exclusion are two halves of one decision.
// Neither is correct alone: the exclusion without the interlock silently reads
// a stale register, and the interlock without the exclusion would be a stall
// that buys nothing.
//
// WHY THE PREDICATE IS uses_rs1 / uses_rs2 AND NOT "rs1 != 0".  A register
// field that is not a source still holds bits -- LUI's insn[19:15] is part of
// its immediate -- and stalling on those is a PHANTOM STALL.  It changes no
// architectural state, so a commit-log diff cannot see it; it shows up only as
// an IPC discrepancy, much later, in a measurement that has no obvious link to
// this file.  tb/cosim/cycle_model.py is what makes phantom stalls visible now:
// it predicts the cycle count independently and the testbench compares.
//
// rs3 is deliberately absent, for the same reason it is absent from
// rvntt_forward: it is read only by Xkntt R4-type instructions, which no stage
// executes yet, so an interlock term for it could not be tested.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
// ============================================================================
`default_nettype none

module rvntt_hazard (
    // ---- the consumer, in ID ----
    input  wire        id_valid,
    input  wire        id_uses_rs1,
    input  wire        id_uses_rs2,
    input  wire  [4:0] id_rs1_addr,
    input  wire  [4:0] id_rs2_addr,

    // ---- the producer, in EX ----
    input  wire        ex_valid,
    input  wire        ex_mem_read,
    input  wire  [4:0] ex_rd_addr,

    output logic       stall
);

  // A load whose result some later instruction could actually observe.  rd == 0
  // is excluded because that load's result is discarded, so nothing can depend
  // on it -- stalling for it would be a pure waste of a cycle on an idiom
  // (`lw x0, 0(a0)`) that assemblers do emit.
  wire ex_pending_load = ex_valid && ex_mem_read && (ex_rd_addr != 5'd0);

  assign stall = id_valid && ex_pending_load &&
                 ((id_uses_rs1 && (id_rs1_addr == ex_rd_addr)) ||
                  (id_uses_rs2 && (id_rs2_addr == ex_rd_addr)));

`ifdef FORMAL
  // Rebuilt from the raw inputs rather than reusing ex_pending_load, and in a
  // different form.  Sharing the module's own intermediate wire would make
  // these properties blind to a bug inside it -- exactly the hole the mutation
  // harness found in rvntt_forward's proof (rtl/core/CLAUDE.md).
  wire f_load_visible = !(!ex_valid || !ex_mem_read || (ex_rd_addr == 5'd0));
  wire f_reads_it     = (id_uses_rs1 && (id_rs1_addr == ex_rd_addr)) ||
                        (id_uses_rs2 && (id_rs2_addr == ex_rd_addr));

  always_comb begin
    // 1. COMPLETENESS.  Every real load-use hazard stalls.  This is the
    //    property whose failure corrupts data; everything else below costs
    //    cycles rather than correctness.
    a_complete: assert (!(id_valid && f_load_visible && f_reads_it) || stall);

    // 2. SOUNDNESS.  A stall implies there really was one.  Its failures are
    //    phantom stalls: invisible to a commit-log diff, visible only in IPC.
    a_sound: assert (!stall || (id_valid && f_load_visible && f_reads_it));

    // 3. NO STALL ON x0.  `lw x0, 0(rs1)` discards its result, so nothing can
    //    depend on it -- and every instruction that names x0 as a source would
    //    otherwise match.
    a_x0: assert ((ex_rd_addr != 5'd0) || !stall);

    // 4. NO STALL ON A NON-SOURCE FIELD.  The phantom-stall case that a
    //    functional test cannot see at all.
    a_no_phantom: assert ((id_uses_rs1 || id_uses_rs2) || !stall);

    // 5. ONLY LOADS STALL.  An ALU result at distance 1 is forwarded, never
    //    waited for; a stall there would be correct and slow, which is the
    //    hardest kind of bug to notice.
    a_only_loads: assert (ex_mem_read || !stall);
  end
`endif

endmodule

`default_nettype wire
