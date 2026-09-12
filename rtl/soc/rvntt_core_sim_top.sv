// ============================================================================
// rvntt_core_sim_top -- rvntt_core plus rvntt_ram, for simulation.  No UART,
// no GPIO, no address decoder; the memory is sized and initialised from a
// parameter and the retirement trace is brought out to the testbench.
// ============================================================================
`default_nettype none

module rvntt_core_sim_top #(
    parameter int          WORDS     = 16384,          // 64 KB
    parameter logic [31:0] BASE      = 32'h8000_0000,
    parameter logic [31:0] RESET_PC  = 32'h8000_0000,
    parameter string       INIT_FILE = ""
) (
    input  wire         clk,
    input  wire         rst_n,

    output logic        commit_valid,
    output logic [31:0] commit_pc,
    output logic [31:0] commit_insn,
    output logic        commit_reg_write,
    output logic [4:0]  commit_rd,
    output logic [31:0] commit_wdata,
    output logic        dbg_unsupported,

    // The data-store bus, so a testbench can watch for the write to `tohost`
    // that ends a riscv-tests program (a store is issued from EX and nothing
    // past EX is squashed, so a write seen here architecturally happened).
    output logic [31:0] dbg_store_addr,
    output logic [31:0] dbg_store_data,
    output logic [3:0]  dbg_store_be
);

  logic [31:0] imem_addr, imem_rdata;
  logic [31:0] dmem_addr, dmem_wdata, dmem_rdata;
  logic [3:0]  dmem_be;

  assign dbg_store_addr = dmem_addr;
  assign dbg_store_data = dmem_wdata;
  assign dbg_store_be   = dmem_be;

  // A deterministic stand-in for the noise source: a free-running LFSR with
  // no entropy, so every simulation is reproducible and the health tests do
  // not kill a stuck source.  The health tests themselves are driven at the
  // rvntt_seed level by tb/unit/test_entropy_health.py.
  /* verilator lint_off PROCASSINIT */
  logic [15:0] stub_lfsr_q = 16'hACE1;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) stub_lfsr_q <= 16'hACE1;
    else        stub_lfsr_q <= {stub_lfsr_q[14:0],
                                stub_lfsr_q[15] ^ stub_lfsr_q[13] ^
                                stub_lfsr_q[12] ^ stub_lfsr_q[10]};
  end

  rvntt_core #(.RESET_PC(RESET_PC)) u_core (
      .entropy_stub_bit (stub_lfsr_q[0]),
      .clk (clk), .rst_n (rst_n),
      .imem_addr (imem_addr), .imem_rdata (imem_rdata),
      .dmem_addr (dmem_addr), .dmem_wdata (dmem_wdata),
      .dmem_be (dmem_be),     .dmem_rdata (dmem_rdata),
      .commit_valid (commit_valid), .commit_pc (commit_pc),
      .commit_insn (commit_insn),   .commit_reg_write (commit_reg_write),
      .commit_rd (commit_rd),       .commit_wdata (commit_wdata),
      .dbg_unsupported (dbg_unsupported)
  );

  rvntt_ram #(.WORDS(WORDS), .BASE(BASE), .INIT_FILE(INIT_FILE)) u_ram (
      .clk (clk),
      .addr_a (imem_addr), .rdata_a (imem_rdata),
      .addr_b (dmem_addr), .wdata_b (dmem_wdata),
      .be_b (dmem_be),     .rdata_b (dmem_rdata)
  );

endmodule

`default_nettype wire
