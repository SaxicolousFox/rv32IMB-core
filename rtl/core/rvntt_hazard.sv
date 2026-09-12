// ============================================================================
// rvntt_hazard -- the load-use interlock.
//
//   if EX.mem_read && (EX.rd == ID.rs1 || EX.rd == ID.rs2) && EX.rd != 0
//   then stall IF and ID for one cycle and inject a bubble into EX.
//
// One cycle suffices because FWD_MEM deliberately excludes loads: with the
// stall, the consumer reaches EX while the load is in WB, and FWD_WB carries
// it from a register output.  The predicate is uses_rs1/uses_rs2, not the raw
// field, so a non-source field (LUI's insn[19:15]) cannot cause a phantom
// stall -- which no commit-log diff would see (tb/cosim/cycle_model.py does).
//
// Package references are fully qualified with no `import` (Yosys).
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

  // A load whose result something could observe: `lw x0, ...` does not stall.
  wire ex_pending_load = ex_valid && ex_mem_read && (ex_rd_addr != 5'd0);

  assign stall = id_valid && ex_pending_load &&
                 ((id_uses_rs1 && (id_rs1_addr == ex_rd_addr)) ||
                  (id_uses_rs2 && (id_rs2_addr == ex_rd_addr)));

`ifdef FORMAL
  // Rebuilt from the raw inputs rather than reusing ex_pending_load.
  wire f_load_visible = !(!ex_valid || !ex_mem_read || (ex_rd_addr == 5'd0));
  wire f_reads_it     = (id_uses_rs1 && (id_rs1_addr == ex_rd_addr)) ||
                        (id_uses_rs2 && (id_rs2_addr == ex_rd_addr));

  always_comb begin
    // 1. Completeness: every real load-use hazard stalls (data corruption otherwise).
    a_complete: assert (!(id_valid && f_load_visible && f_reads_it) || stall);

    // 2. Soundness: a stall implies a real hazard (phantom stalls otherwise).
    a_sound: assert (!stall || (id_valid && f_load_visible && f_reads_it));

    // 3. No stall on x0.
    a_x0: assert ((ex_rd_addr != 5'd0) || !stall);

    // 4. No stall on a non-source field.
    a_no_phantom: assert ((id_uses_rs1 || id_uses_rs2) || !stall);

    // 5. Only loads stall; an ALU result at distance 1 is forwarded.
    a_only_loads: assert (ex_mem_read || !stall);
  end
`endif

endmodule

`default_nettype wire
