// ============================================================================
// rvntt_entropy -- the noise source (MODS_A2 A29, piece 1).
//
// THE ONE MODULE IN THIS PROJECT THAT ITS OWN METHODS CANNOT VERIFY, and
// MODS_A2 3.7 says so in advance rather than discovering it here.  A ring
// oscillator is a combinational loop: Verilator will not simulate it
// meaningfully, Yosys and SymbiYosys cannot represent it at all, and Vivado
// will delete it unless told not to.  So the module is built so that
// EVERYTHING AROUND IT is verifiable and the loop itself is confined to one
// generate arm that simulation never takes.
//
//   STUB = 1  (simulation, formal, cosimulation, and the regression)
//             `stub_bit` is driven by the testbench.  No loop exists in the
//             elaborated design at all -- not disabled, ABSENT.
//   STUB = 0  (synthesis only)
//             RINGS independent inverter chains of DIFFERENT ODD LENGTHS,
//             each sampled by the system clock, XORed together.
//
// WHY SEVERAL RINGS OF DIFFERENT LENGTHS AND NOT ONE.  A single ring sampled by
// a clock it is asynchronous to will INJECTION-LOCK to that clock and emit a
// periodic sequence -- which passes a casual eyeball test and carries no
// entropy whatsoever.  Coprime lengths make the combined period the product
// rather than the minimum, and the XOR of independent sources is at least as
// unpredictable as the best of them.  This does not make the output good; it
// removes the specific failure that would make it look good while being empty.
//
// WHAT THE ATTRIBUTES ARE FOR, individually, because a missing one fails
// SILENTLY in a different way each time:
//   DONT_TOUCH      stops the inverter chain being constant-folded to a
//                   tautology and deleted.
//   KEEP_HIERARCHY  stops the ring being flattened into surrounding logic,
//                   after which DONT_TOUCH on the net no longer protects it.
//   ASYNC_REG       on the two-flop synchroniser, so the metastability-prone
//                   capture flop is placed adjacent to its successor.
// The combinational loop must also be excluded from timing analysis, which is
// a CONSTRAINT and not an attribute: see fpga/constraints/entropy_ring.xdc.
//
// THE SAMPLED BIT IS METASTABLE BY CONSTRUCTION.  That is the point -- the
// entropy is the jitter between the ring's edge and the sampling edge.  Two
// synchroniser flops make it a legal logic value before anything else sees it;
// they do not and cannot remove the uncertainty that was the signal.
// ============================================================================
`default_nettype none

module rvntt_entropy #(
    // 1 for every simulation and formal build in this project; 0 only in the
    // bitstream.  tb/unit/test_entropy_health.py drives the stub directly.
    parameter bit STUB  = 1,
    parameter int RINGS = 3
) (
    input  wire clk,
    input  wire rst_n,
    // STUB=1 only.  Unused in the ring build, which is the whole point of the
    // parameter -- so the waiver is scoped to the port rather than the module.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire stub_bit,      // STUB=1 only: the testbench's raw sample
    /* verilator lint_on UNUSEDSIGNAL */

    output wire sample,        // one raw bit, synchronised
    output wire sample_valid   // one per clock, always -- the source free-runs
);

  logic raw_sync_q;

  generate
    if (STUB) begin : g_stub
      // No loop, no attributes, nothing for a tool to object to.  The two
      // synchroniser stages are kept so the STUB and RING builds have the same
      // LATENCY -- a stub that responded a cycle sooner would make every
      // health-test timing measured against it wrong by two samples.
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
      // loop, which is CORRECT: it is one, deliberately, and Verilator cannot
      // simulate it meaningfully.  Waived here and nowhere else, so the warning
      // stays live for every other line in the tree.  MODS_A2 3.7 predicted
      // exactly this and is why STUB exists.
      /* verilator lint_off UNOPTFLAT */
      /* verilator lint_off ALWCOMBORDER */
      logic [RINGS-1:0] ring_out;
      for (genvar r = 0; r < RINGS; r++) begin : g_r
        // 13, 17, 19 ... -- odd, and pairwise coprime so the combined period is
        // the product.  An even length is not an oscillator at all; it latches.
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
