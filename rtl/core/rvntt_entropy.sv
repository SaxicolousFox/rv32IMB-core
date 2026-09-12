// ============================================================================
// rvntt_entropy -- the noise source for Zkr.
//
//   STUB = 1  (simulation, formal, cosimulation): `stub_bit` is driven by the
//             testbench.  No combinational loop exists in the design.
//   STUB = 0  (synthesis only): RINGS ring oscillators of different, pairwise
//             coprime odd lengths, sampled by the system clock and XORed.
//             Coprime lengths stop a single ring injection-locking to the
//             sampling clock and emitting a periodic sequence.
//
// DONT_TOUCH and KEEP_HIERARCHY stop the chains being folded or flattened
// away; ASYNC_REG marks the synchroniser; the loop must also be excluded from
// timing analysis by fpga/constraints/entropy_ring.xdc.  The sampled bit is
// metastable by construction -- that jitter is the signal -- and two
// synchroniser flops make it a legal logic value.
// ============================================================================
`default_nettype none

module rvntt_entropy #(
    // 1 for every simulation and formal build; 0 only in the bitstream.
    parameter bit STUB  = 1,
    parameter int RINGS = 3
) (
    input  wire clk,
    input  wire rst_n,
    // STUB=1 only.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire stub_bit,      // STUB=1 only: the testbench's raw sample
    /* verilator lint_on UNUSEDSIGNAL */

    output wire sample,        // one raw bit, synchronised
    output wire sample_valid   // one per clock, always -- the source free-runs
);

  logic raw_sync_q;

  generate
    if (STUB) begin : g_stub
      // The two synchroniser stages are kept so the STUB and RING builds have
      // the same latency.
      logic meta_q;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          meta_q     <= 1'b0;
          raw_sync_q <= 1'b0;
        end else begin
          meta_q     <= stub_bit;
          raw_sync_q <= meta_q;
        end
      end
    end else begin : g_ring
      // synthesis-only
      // UNOPTFLAT and ALWCOMBORDER are Verilator objecting to a combinational
      // loop, which is correct: it is one, deliberately.
      /* verilator lint_off UNOPTFLAT */
      /* verilator lint_off ALWCOMBORDER */
      logic [RINGS-1:0] ring_out;
      for (genvar r = 0; r < RINGS; r++) begin : g_r
        // 13, 19, 33 ... -- odd (an even length latches, it does not
        // oscillate) and pairwise coprime.
        localparam int LEN = 13 + 4 * r * r + 2 * r;
        (* DONT_TOUCH = "TRUE", KEEP_HIERARCHY = "YES" *)
        logic [LEN-1:0] chain;
        always_comb begin
          chain[0] = ~chain[LEN-1];
          for (int i = 1; i < LEN; i++) chain[i] = ~chain[i-1];
        end
        assign ring_out[r] = chain[LEN-1];
      end
      /* verilator lint_on ALWCOMBORDER */
      /* verilator lint_on UNOPTFLAT */

      (* ASYNC_REG = "TRUE" *) logic meta_q;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          meta_q     <= 1'b0;
          raw_sync_q <= 1'b0;
        end else begin
          meta_q     <= ^ring_out;
          raw_sync_q <= meta_q;
        end
      end
    end
  endgenerate

  assign sample       = raw_sync_q;
  assign sample_valid = 1'b1;

endmodule

`default_nettype wire
