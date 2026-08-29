// ============================================================================
// rvntt_core_sim_top -- rvntt_core plus rvntt_ram, for simulation.
//
// The whole SoC A4 needs: one core, one dual-ported memory, and the retirement
// trace brought out to the testbench.  No UART, no GPIO, no address decoder --
// A12 builds those.  Keeping this separate from a future synthesisable top
// means the simulation memory can be sized and initialised from a plusarg
// without that machinery ever reaching a bitstream.
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
    output logic        commit_is_ecall,
    output logic        dbg_unsupported
);

  logic [31:0] imem_addr, imem_rdata;
  logic [31:0] dmem_addr, dmem_wdata, dmem_rdata;
  logic [3:0]  dmem_be;

  rvntt_core #(.RESET_PC(RESET_PC)) u_core (
      .clk (clk), .rst_n (rst_n),
      .imem_addr (imem_addr), .imem_rdata (imem_rdata),
      .dmem_addr (dmem_addr), .dmem_wdata (dmem_wdata),
      .dmem_be (dmem_be),     .dmem_rdata (dmem_rdata),
      .commit_valid (commit_valid), .commit_pc (commit_pc),
      .commit_insn (commit_insn),   .commit_reg_write (commit_reg_write),
      .commit_rd (commit_rd),       .commit_wdata (commit_wdata),
      .commit_is_ecall (commit_is_ecall),
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
