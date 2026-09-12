// ============================================================================
// rvntt_trace_top -- rvntt_core_sim_top plus the commit-log monitor.  A
// separate top so nothing under rtl/ references a module containing $fopen.
// ============================================================================
`default_nettype none

module rvntt_trace_top #(
    parameter int          WORDS     = 16384,
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
    output logic [31:0] dbg_store_addr,
    output logic [31:0] dbg_store_data,
    output logic [3:0]  dbg_store_be
);

  rvntt_core_sim_top #(
      .WORDS(WORDS), .BASE(BASE), .RESET_PC(RESET_PC), .INIT_FILE(INIT_FILE)
  ) u_dut (
      .clk (clk), .rst_n (rst_n),
      .commit_valid (commit_valid), .commit_pc (commit_pc),
      .commit_insn (commit_insn),   .commit_reg_write (commit_reg_write),
      .commit_rd (commit_rd),       .commit_wdata (commit_wdata),
      .dbg_unsupported (dbg_unsupported),
      .dbg_store_addr (dbg_store_addr), .dbg_store_data (dbg_store_data),
      .dbg_store_be (dbg_store_be)
  );

  rvntt_trace u_trace (
      .clk (clk),
      .commit_valid (commit_valid), .commit_pc (commit_pc),
      .commit_insn (commit_insn),   .commit_reg_write (commit_reg_write),
      .commit_rd (commit_rd),       .commit_wdata (commit_wdata)
  );

endmodule

`default_nettype wire
