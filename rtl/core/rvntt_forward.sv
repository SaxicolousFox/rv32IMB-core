// ============================================================================
// rvntt_forward -- operand forwarding for the EX stage.
//
// Resolves EX/MEM -> EX (distance 1, FWD_MEM) and MEM/WB -> EX (distance 2,
// FWD_WB) for both operands; the younger producer wins, and a producer whose
// rd is x0 never supplies.  Distance 3 is covered by the register file's
// write-through, not by this module.
//
// Loads are excluded from FWD_MEM: forwarding load data from MEM would put the
// BRAM output on the ALU input path inside one cycle.  rvntt_hazard stalls that
// case for one cycle instead, after which FWD_WB supplies it.
//
// Gated on uses_rs1/uses_rs2 -- the same predicate the interlock uses, so the
// two cannot disagree and a register field that is really part of an immediate
// (LUI's rs1 bits) cannot cause a phantom stall.
//
// Package references are fully qualified with no `import` (Yosys).
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

  // A producer supplies only if it really writes a real register (rd != x0).
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
  // The "can supply" predicates are rebuilt from the raw inputs in De Morgan
  // form rather than reusing mem_supplies/wb_supplies: a property that reuses
  // the DUT's own intermediate signal cannot detect a bug in that signal.
  wire f_mem_can = !(!mem_valid || !mem_reg_write || mem_mem_read ||
                     (mem_rd_addr == 5'd0));
  wire f_wb_can  = !(!wb_valid  || !wb_reg_write  || (wb_rd_addr == 5'd0));

  wire f_mem_hit_a = f_mem_can && ex_uses_rs1 && (mem_rd_addr == ex_rs1_addr);
  wire f_wb_hit_a  = f_wb_can  && ex_uses_rs1 && (wb_rd_addr  == ex_rs1_addr);
  wire f_mem_hit_b = f_mem_can && ex_uses_rs2 && (mem_rd_addr == ex_rs2_addr);
  wire f_wb_hit_b  = f_wb_can  && ex_uses_rs2 && (wb_rd_addr  == ex_rs2_addr);

  always_comb begin
    // 1. Priority: when both producers hold the register, the younger wins.
    a_priority_a: assert (!(f_mem_hit_a && f_wb_hit_a) || fwd_a == rv32i_pkg::FWD_MEM);
    a_priority_b: assert (!(f_mem_hit_b && f_wb_hit_b) || fwd_b == rv32i_pkg::FWD_MEM);

    // 2. Completeness: a stale register is never read while a producer is in flight.
    a_complete_a: assert (!(f_mem_hit_a || f_wb_hit_a) || fwd_a != rv32i_pkg::FWD_REG);
    a_complete_b: assert (!(f_mem_hit_b || f_wb_hit_b) || fwd_b != rv32i_pkg::FWD_REG);

    // 3. Soundness: a chosen source actually holds the register.
    a_sound_mem_a: assert (fwd_a != rv32i_pkg::FWD_MEM || f_mem_hit_a);
    a_sound_wb_a:  assert (fwd_a != rv32i_pkg::FWD_WB  || f_wb_hit_a);
    a_sound_mem_b: assert (fwd_b != rv32i_pkg::FWD_MEM || f_mem_hit_b);
    a_sound_wb_b:  assert (fwd_b != rv32i_pkg::FWD_WB  || f_wb_hit_b);

    // 4. x0 reads zero: nothing is forwarded into a source naming x0.
    a_x0_a: assert (ex_rs1_addr != 5'd0 || fwd_a == rv32i_pkg::FWD_REG);
    a_x0_b: assert (ex_rs2_addr != 5'd0 || fwd_b == rv32i_pkg::FWD_REG);

    // 5. No forwarding into an operand the instruction does not read.
    a_uses_a: assert (ex_uses_rs1 || fwd_a == rv32i_pkg::FWD_REG);
    a_uses_b: assert (ex_uses_rs2 || fwd_b == rv32i_pkg::FWD_REG);

    // 6. Symmetry: equal addresses that are both read select equal sources.
    a_symmetry: assert (!(ex_uses_rs1 && ex_uses_rs2 &&
                          (ex_rs1_addr == ex_rs2_addr)) || (fwd_a == fwd_b));

    // 7. A load in MEM is never a forward source.
    a_no_load_fwd: assert (!(mem_valid && mem_mem_read) ||
                           (fwd_a != rv32i_pkg::FWD_MEM && fwd_b != rv32i_pkg::FWD_MEM));
  end
`endif

endmodule

`default_nettype wire
