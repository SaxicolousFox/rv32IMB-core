// Reset synchronizer: asynchronous assert, synchronous release.
//
// Plan A12 calls this out explicitly: releasing a reset asynchronously is a real
// source of nondeterministic FPGA bring-up failures, because different flops come
// out of reset on different clock edges.  Asserting asynchronously is what you
// want (it works with no clock); releasing synchronously is what makes bring-up
// repeatable.
//
// This is also the first formal target in the regression, so it carries SVA.
`default_nettype none

module rvntt_sync_reset #(
    parameter int STAGES = 2       // >=2 for metastability hardening
) (
    input  wire  clk,
    input  wire  arst_n,           // async, active low, from the board/MMCM locked
    output wire  rst_n             // async assert, sync release, active low
);
  // Explicit power-on value.  On Xilinx, flops genuinely do come up at their INIT
  // value, so this is accurate RTL -- and it also gives the formal engine a
  // defined initial state.  Without it, BMC starts from an arbitrary sync_q and
  // reports a spurious counterexample where reset is already released.
  // PROCASSINIT is expected here: the initial value is the FPGA power-on state,
  // and the procedural assignment is the async reset.  Both are intended.
  // SYNCASYNCNET is expected and is the entire purpose of this module: sync_q is
  // clocked synchronously, while the rst_n it produces is consumed as an
  // asynchronous reset elsewhere.  That is the async-assert / sync-release
  // structure, not a clock-domain mistake.
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
  // Safety: the synchronized reset must never be released while the raw
  // asynchronous reset is still asserted.
  always_comb assert (!(rst_n && !arst_n));

  // The release must be synchronous: rst_n may only rise on a clock edge, and
  // only after STAGES cycles of arst_n being high.  Track that with a counter.
  /* verilator lint_off PROCASSINIT */
  logic [7:0] hi_cnt = 8'd0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge arst_n) begin
    if (!arst_n)              hi_cnt <= 8'd0;
    else if (hi_cnt != 8'hFF) hi_cnt <= hi_cnt + 8'd1;
  end

  // rst_n cannot be high until arst_n has been high for at least STAGES edges.
  always_comb if (hi_cnt < STAGES[7:0]) assert (!rst_n);
`endif

endmodule

`default_nettype wire
