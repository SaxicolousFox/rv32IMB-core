// ============================================================================
// rvntt_entropy_health -- SP 800-90B's two mandatory continuous health tests.
//
// The cutoffs are derived, not quoted; tb/unit/test_entropy_health.py
// recomputes both from the standard's definitions and fails if the RTL
// disagrees:
//
//   REPETITION (4.4.1)   C = 1 + ceil(-log2(alpha) / H) = 21
//   ADAPTIVE   (4.4.2)   C = min{ C : P[Bin(W,2^-H) >= C] <= alpha } = 589
//
// with H = 1 bit/sample (an assumption, not a measurement -- tighter than a
// lower rate would be), alpha = 2^-20 and W = 1024.  The widely cited 821 is
// for a different assumed entropy rate.
//
// The adaptive test here counts BOTH values, not only the window's first
// sample as the standard designates: a periodic source whose period divides
// the window has a fixed phase, so if the first sample is the minority value
// the mandated test never fires -- and injection-locking produces exactly such
// a source.  Counting both is strictly stronger.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_entropy_health #(
    parameter int REP_CUTOFF = 21,     // SP 800-90B 4.4.1, H=1, alpha=2^-20
    parameter int AP_WINDOW  = 1024,   // SP 800-90B 4.4.2 window
    parameter int AP_CUTOFF  = 589     // SP 800-90B 4.4.2, H=1, alpha=2^-20
) (
    input  wire clk,
    input  wire rst_n,

    input  wire sample,        // one raw bit from the noise source
    input  wire sample_valid,  // it is a new sample this cycle

    // Sticky: a failed continuous test takes the source out of service until
    // reset (SP 800-90B and Zkr's DEAD both require it).
    output wire fail
);

  localparam int RUN_W = $clog2(REP_CUTOFF + 1);
  localparam int WIN_W = $clog2(AP_WINDOW);
  localparam int CNT_W = $clog2(AP_WINDOW + 1);

  // ==========================================================================
  // Repetition count test -- SP 800-90B 4.4.1
  // ==========================================================================
  // The counter starts at 1, so a run of REP_CUTOFF identical samples fails.
  logic             prev_q;
  logic             have_prev_q;
  logic [RUN_W-1:0] run_q;
  logic             rep_fail_q;

  wire run_continues = have_prev_q && (sample == prev_q);
  wire [RUN_W-1:0] run_next = run_continues ? (run_q + RUN_W'(1)) : RUN_W'(1);

  // ==========================================================================
  // Adaptive proportion test -- SP 800-90B 4.4.2
  // ==========================================================================
  logic [WIN_W-1:0] ap_pos_q;       // position within the window
  logic [CNT_W-1:0] ap_ones_q;      // occurrences of 1 so far
  logic             ap_fail_q;

  wire at_window_start = (ap_pos_q == WIN_W'(0));
  wire [CNT_W-1:0] ap_ones_now =
      (at_window_start ? CNT_W'(0) : ap_ones_q) + (sample ? CNT_W'(1) : CNT_W'(0));
  wire at_window_end = (ap_pos_q == WIN_W'(AP_WINDOW - 1));
  // The window is full when at_window_end is evaluated.
  wire [CNT_W-1:0] ap_zeros_now = CNT_W'(AP_WINDOW) - ap_ones_now;
  wire ap_over = (ap_ones_now  >= CNT_W'(AP_CUTOFF)) ||
                 (ap_zeros_now >= CNT_W'(AP_CUTOFF));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prev_q      <= 1'b0;
      have_prev_q <= 1'b0;
      run_q       <= RUN_W'(0);
      rep_fail_q  <= 1'b0;
      ap_pos_q    <= WIN_W'(0);
      ap_ones_q   <= CNT_W'(0);
      ap_fail_q   <= 1'b0;
    end else if (sample_valid) begin
      // ---- repetition count ----
      prev_q      <= sample;
      have_prev_q <= 1'b1;
      run_q       <= run_next;
      if (run_next >= RUN_W'(REP_CUTOFF)) rep_fail_q <= 1'b1;

      // ---- adaptive proportion ----
      ap_ones_q <= ap_ones_now;
      ap_pos_q  <= at_window_end ? WIN_W'(0) : (ap_pos_q + WIN_W'(1));
      if (at_window_end && ap_over) ap_fail_q <= 1'b1;
    end
  end

  assign fail = rep_fail_q || ap_fail_q;

`ifdef FORMAL
  // tb/formal instantiates this with a small window so counterexamples are
  // short.
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  // The machine starts from reset; otherwise every state invariant is
  // trivially violated from arbitrary step-0 values.
  initial assume (!rst_n);

  // 1. Sticky.
  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && $past(fail))
      a_fail_is_sticky: assert (fail);
  end

  // 2. The repetition test fires within its cutoff, stated on the run
  //    counter (the mechanism) rather than as a temporal "eventually".
  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && $past(sample_valid) &&
        $past(run_next) >= RUN_W'(REP_CUTOFF))
      a_rep_fires: assert (rep_fail_q);
  end

  // 3. No spurious failure before a sample has been presented.
  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && !$past(have_prev_q) &&
        !$past(sample_valid))
      a_no_fail_without_samples: assert (!fail);
  end

  // 4. State invariants, claimed from the first reset onward.  Note
  //    WIN_W'(AP_WINDOW) is zero (a size cast one bit short), so the bound
  //    is written against AP_WINDOW - 1.
  always_ff @(posedge clk) if (f_past_valid && $past(rst_n) && rst_n) begin
    a_window_bounded: assert (ap_pos_q <= WIN_W'(AP_WINDOW - 1));
    a_count_bounded:  assert (ap_ones_q <= CNT_W'(AP_WINDOW));
    // The run counter never passes the cutoff without the test having fired.
    a_run_bounded:    assert (run_q <= RUN_W'(REP_CUTOFF) || rep_fail_q);
  end
`endif

endmodule

`default_nettype wire
