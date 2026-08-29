// ============================================================================
// rvntt_forward -- operand forwarding for the EX stage (plan A6).
//
// Resolves EX/MEM -> EX and MEM/WB -> EX for both ALU operands, with the
// younger producer winning when both match, and no forwarding at all from a
// producer whose rd is x0.
//
// WHAT THE THREE DISTANCES LOOK LIKE.  Take a consumer in EX during cycle T:
//
//   distance 1  producer is in MEM   -> FWD_MEM
//   distance 2  producer is in WB    -> FWD_WB
//   distance 3  producer wrote the register file on the edge into T, and the
//               consumer read it in ID during T-1 -- covered by the register
//               file's WRITE-THROUGH, not by this module.  That is why
//               rvntt_regfile has a write-through path and why the random
//               program generator's RAW_DISTANCE is 3.
//
// So the forwarding network and the write-through together cover every
// distance, and there is no gap at 3 that a test would have to avoid.
//
// LOADS ARE EXCLUDED FROM FWD_MEM.  A load in MEM does have its data by then
// (rvntt_ram registers the address in EX), but forwarding it would run the
// BRAM output through the sign-extender, the forward mux, the ALU and back to
// the BRAM address input inside one cycle.  A7's interlock stalls that case for
// one cycle instead, after which the load is in WB and FWD_WB -- a register
// output -- supplies it.  `mem_mem_read` is what implements the exclusion here;
// rvntt_hazard.sv is what makes the exclusion safe.
//
// GATED ON uses_rs1 / uses_rs2.  Forwarding into an operand the instruction
// does not read would be harmless on its own, since the source mux would not
// select it -- but the same predicate decides whether A7 STALLS, and a stall on
// a register field that is really part of an immediate (LUI's rs1, for
// instance) is a phantom stall: invisible in a functional test and visible only
// as an IPC discrepancy much later.  Sharing one predicate between the two
// makes it impossible for them to disagree.
//
// rs3 is deliberately absent.  It is read only by the Xkntt R4-type
// instructions, which no stage executes yet (rvntt_core's dbg_unsupported fires
// on them), so a forwarding path for it could not be tested and would be
// exactly the kind of logic that looks verified and is not.  It goes in with
// the coprocessor interface, together with its own tests.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
// ============================================================================
`default_nettype none

module rvntt_forward (
    // ---- the consumer, in EX ----
    input  wire  [4:0] ex_rs1_addr,
    input  wire  [4:0] ex_rs2_addr,
    input  wire        ex_uses_rs1,
    input  wire        ex_uses_rs2,

    // ---- producer at distance 1, in MEM ----
    input  wire        mem_valid,
    input  wire        mem_reg_write,
    input  wire        mem_mem_read,    // a load: its result is not forwardable
    input  wire  [4:0] mem_rd_addr,

    // ---- producer at distance 2, in WB ----
    input  wire        wb_valid,
    input  wire        wb_reg_write,
    input  wire  [4:0] wb_rd_addr,

    output rv32i_pkg::fwd_sel_e fwd_a,
    output rv32i_pkg::fwd_sel_e fwd_b
);

  // A producer can supply a value only if it is really going to write a real
  // register.  The rd != 0 term is the "rd == x0 suppression": x0 must read as
  // zero even when the instruction one slot ahead nominally wrote it.
  wire mem_supplies = mem_valid && mem_reg_write && !mem_mem_read &&
                      (mem_rd_addr != 5'd0);
  wire wb_supplies  = wb_valid  && wb_reg_write  && (wb_rd_addr  != 5'd0);

  always_comb begin
    fwd_a = rv32i_pkg::FWD_REG;
    if (ex_uses_rs1) begin
      if      (mem_supplies && (mem_rd_addr == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_MEM;
      else if (wb_supplies  && (wb_rd_addr  == ex_rs1_addr)) fwd_a = rv32i_pkg::FWD_WB;
    end
  end

  always_comb begin
    fwd_b = rv32i_pkg::FWD_REG;
    if (ex_uses_rs2) begin
      if      (mem_supplies && (mem_rd_addr == ex_rs2_addr)) fwd_b = rv32i_pkg::FWD_MEM;
      else if (wb_supplies  && (wb_rd_addr  == ex_rs2_addr)) fwd_b = rv32i_pkg::FWD_WB;
    end
  end

`ifdef FORMAL
  // The properties below are deliberately NOT a second copy of the priority
  // chain above.  Restating a priority encoder as a priority encoder proves
  // only that it was typed twice.  Each of these is a separate claim about the
  // result, and the two interesting ones -- priority and completeness -- are
  // the ones the plan's A6 acceptance test is about.
  //
  // The "can supply" predicates are rebuilt HERE FROM THE RAW INPUTS rather
  // than reusing mem_supplies / wb_supplies, and in De Morgan form so the two
  // are not the same text twice.  Sharing those wires would make every
  // property below blind to a bug inside them, which is not hypothetical:
  // tb/mutate/run_mutation.py found exactly that, with a broken supply
  // condition sailing past properties that were checking it against itself.
  wire f_mem_can = !(!mem_valid || !mem_reg_write || mem_mem_read ||
                     (mem_rd_addr == 5'd0));
  wire f_wb_can  = !(!wb_valid  || !wb_reg_write  || (wb_rd_addr == 5'd0));

  wire f_mem_hit_a = f_mem_can && ex_uses_rs1 && (mem_rd_addr == ex_rs1_addr);
  wire f_wb_hit_a  = f_wb_can  && ex_uses_rs1 && (wb_rd_addr  == ex_rs1_addr);
  wire f_mem_hit_b = f_mem_can && ex_uses_rs2 && (mem_rd_addr == ex_rs2_addr);
  wire f_wb_hit_b  = f_wb_can  && ex_uses_rs2 && (wb_rd_addr  == ex_rs2_addr);

  always_comb begin
    // 1. PRIORITY.  Both producers hold the register: the younger one wins.
    //    `add x1,..; add x1,..; add x6,x1,x0` is exactly this case.
    a_priority_a: assert (!(f_mem_hit_a && f_wb_hit_a) || fwd_a == rv32i_pkg::FWD_MEM);
    a_priority_b: assert (!(f_mem_hit_b && f_wb_hit_b) || fwd_b == rv32i_pkg::FWD_MEM);

    // 2. COMPLETENESS.  If any producer can supply the operand, the register
    //    file's stale copy must not be used.  This is the property that fails
    //    if a match condition is dropped.
    a_complete_a: assert (!(f_mem_hit_a || f_wb_hit_a) || fwd_a != rv32i_pkg::FWD_REG);
    a_complete_b: assert (!(f_mem_hit_b || f_wb_hit_b) || fwd_b != rv32i_pkg::FWD_REG);

    // 3. SOUNDNESS.  A chosen source must actually hold the register.
    a_sound_mem_a: assert (fwd_a != rv32i_pkg::FWD_MEM || f_mem_hit_a);
    a_sound_wb_a:  assert (fwd_a != rv32i_pkg::FWD_WB  || f_wb_hit_a);
    a_sound_mem_b: assert (fwd_b != rv32i_pkg::FWD_MEM || f_mem_hit_b);
    a_sound_wb_b:  assert (fwd_b != rv32i_pkg::FWD_WB  || f_wb_hit_b);

    // 4. x0 READS ZERO.  Nothing is ever forwarded into a source that names x0,
    //    whatever the instruction ahead did with its rd field.
    a_x0_a: assert (ex_rs1_addr != 5'd0 || fwd_a == rv32i_pkg::FWD_REG);
    a_x0_b: assert (ex_rs2_addr != 5'd0 || fwd_b == rv32i_pkg::FWD_REG);

    // 5. NO PHANTOM FORWARDING into an operand the instruction does not read.
    a_uses_a: assert (ex_uses_rs1 || fwd_a == rv32i_pkg::FWD_REG);
    a_uses_b: assert (ex_uses_rs2 || fwd_b == rv32i_pkg::FWD_REG);

    // 6. SYMMETRY.  The two ports are the same function of their inputs, so
    //    equal addresses that are both read must select equal sources.  This
    //    catches a copy-paste slip between the two always_comb blocks -- the
    //    single most likely bug in a module shaped like this one.
    a_symmetry: assert (!(ex_uses_rs1 && ex_uses_rs2 &&
                          (ex_rs1_addr == ex_rs2_addr)) || (fwd_a == fwd_b));

    // 7. A LOAD IN MEM IS NEVER A FORWARD SOURCE.  A7 depends on this: the
    //    interlock is what makes the exclusion safe, and the exclusion is what
    //    makes a broken interlock produce a stale value rather than the load's
    //    ADDRESS forwarded as its data.
    a_no_load_fwd: assert (!(mem_valid && mem_mem_read) ||
                           (fwd_a != rv32i_pkg::FWD_MEM && fwd_b != rv32i_pkg::FWD_MEM));
  end
`endif

endmodule

`default_nettype wire
