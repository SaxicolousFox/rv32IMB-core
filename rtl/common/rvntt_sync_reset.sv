// Reset synchronizer: asynchronous assert, synchronous release.  Releasing a
// reset asynchronously lets different flops leave reset on different clock
// edges, which is a real source of nondeterministic FPGA bring-up.
`default_nettype none

module rvntt_sync_reset #(
    parameter int STAGES = 2       // >=2 for metastability hardening
) (
    input  wire  clk,
    input  wire  arst_n,           // async, active low, from the board/MMCM locked
    output wire  rst_n             // async assert, sync release, active low
);
  // Explicit power-on value (the Xilinx INIT attribute); it also gives the
  // formal engine a defined initial state.  PROCASSINIT and SYNCASYNCNET are
  // both intended: sync_q is clocked synchronously and the rst_n it produces
  // is consumed as an asynchronous reset elsewhere.
  /* verilator lint_off PROCASSINIT */
  /* verilator lint_off SYNCASYNCNET */
  logic [STAGES-1:0] sync_q = '0;
  /* verilator lint_on SYNCASYNCNET */
  /* verilator lint_on PROCASSINIT */

  always_ff @(posedge clk or negedge arst_n) begin
    if (!arst_n) sync_q <= '0;
    else         sync_q <= {sync_q[STAGES-2:0], 1'b1};
  end

  assign rst_n = sync_q[STAGES-1];

`ifdef FORMAL
  // Safety: never released while the raw asynchronous reset is asserted.
  always_comb assert (!(rst_n && !arst_n));

  // The release is synchronous: rst_n may rise only after STAGES cycles of
  // arst_n being high.
  /* verilator lint_off PROCASSINIT */
  logic [7:0] hi_cnt = 8'd0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge arst_n) begin
    if (!arst_n)              hi_cnt <= 8'd0;
    else if (hi_cnt != 8'hFF) hi_cnt <= hi_cnt + 8'd1;
  end

  always_comb if (hi_cnt < STAGES[7:0]) assert (!rst_n);
`endif

endmodule

`default_nettype wire
