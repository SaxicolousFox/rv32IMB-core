// ============================================================================
// rvntt_entropy_health -- SP 800-90B's two MANDATORY continuous health tests
// (MODS_A2 A29, piece 2).
//
// THIS IS WHERE THE SECURITY VALUE IS, AND IT IS THE PART THAT CAN BE VERIFIED.
// MODS_A2 3.7 makes the split explicit: a ring oscillator is a combinational
// loop that Verilator will not simulate meaningfully and Yosys cannot handle at
// all, so the noise source is the first module in this project outside the
// reach of its own primary methods.  Everything else -- these two tests, the
// `seed` state machine, the conditioning -- is ordinary synchronous logic and
// gets ordinary formal and simulation coverage against a STUB source the
// testbench can make stuck, biased or ideal at will.
//
// A HEALTH TEST THAT HAS NEVER BEEN OBSERVED TO FIRE IS NOT A HEALTH TEST.
// tb/unit/test_entropy_health.py drives this stuck-at-0, stuck-at-1 and biased
// 7:1 and requires each to be caught; tb/formal/rvntt_entropy_health proves the
// stuck case at BMC depth.  Both were fault-injected.
//
// ---------------------------------------------------------------------------
// THE CUTOFFS ARE DERIVED, NOT QUOTED.
// ---------------------------------------------------------------------------
// Health-test cutoffs are exactly the kind of constant this project has learned
// not to write from memory -- and the widely-cited "821" for a 1024-sample
// adaptive proportion window is for a different assumed entropy rate.  Both
// defaults below are computed in tb/unit/test_entropy_health.py from the
// standard's own definitions, and the test FAILS if the RTL disagrees with the
// computation, so the numbers cannot drift:
//
//   REPETITION (SP 800-90B 4.4.1)   C = 1 + ceil(-log2(alpha) / H)
//                                     = 1 + ceil(20 / 1) = 21
//   ADAPTIVE   (SP 800-90B 4.4.2)   C = min{ C : P[Bin(W,2^-H) >= C] <= alpha }
//                                     = 589 for W = 1024, H = 1, alpha = 2^-20
//
// with H = 1 bit per sample (a binary source) and alpha = 2^-20, the standard's
// recommended false-positive rate.  589 is 4.8 sigma on Bin(1024, 0.5), whose
// mean is 512 and whose standard deviation is 16.
//
// H = 1 IS AN ASSUMPTION AND NOT A MEASUREMENT.  Establishing the real entropy
// rate is the SP 800-90B estimation track, which needs a large sample captured
// from the physical device and is NOT part of this step -- see the uncertified
// statement in docs/a29-zkr.md.  Assuming H = 1 makes the cutoffs TIGHTER than
// a lower rate would, so the tests are conservative in the safe direction.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
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

    // Sticky.  SP 800-90B requires a failed continuous test to take the source
    // permanently out of service, and Zkr's `seed` requires DEAD to latch until
    // reset -- the same requirement arriving from two directions.
    output wire fail
);

  localparam int RUN_W = $clog2(REP_CUTOFF + 1);
  localparam int WIN_W = $clog2(AP_WINDOW);
  localparam int CNT_W = $clog2(AP_WINDOW + 1);

  // ==========================================================================
  // Repetition count test -- SP 800-90B 4.4.1
  // ==========================================================================
  // "If the current sample equals the previous one, increment the counter; if
  // the counter reaches the cutoff, the test fails."  The counter starts at 1
  // for the first sample, so a run of REP_CUTOFF identical samples fails.
  logic             prev_q;
  logic             have_prev_q;
  logic [RUN_W-1:0] run_q;
  logic             rep_fail_q;

  wire run_continues = have_prev_q && (sample == prev_q);
  wire [RUN_W-1:0] run_next = run_continues ? (run_q + RUN_W'(1)) : RUN_W'(1);

  // ==========================================================================
  // Adaptive proportion test -- SP 800-90B 4.4.2
  // ==========================================================================
  // The standard designates the FIRST sample of each window as the value A and
  // counts how many of the window's samples equal it.  THIS IMPLEMENTATION
  // COUNTS BOTH VALUES AND FAILS IF EITHER REACHES THE CUTOFF, which is
  // strictly stronger -- whenever the mandated test fails, so does this one --
  // and the reason is a blind spot that fault injection found rather than
  // reasoning:
  //
  //   A PERIODIC SOURCE WHOSE PERIOD DIVIDES THE WINDOW HAS A FIXED PHASE, so
  //   the "first sample" is the same value in EVERY window.  Feed the mandated
  //   test seven ones and a zero, forever, with a 1024-sample window: 1024 is a
  //   multiple of 8, the designated A is whatever phase 0 happens to be, and if
  //   that is the MINORITY value the count is 128 against a cutoff of 589 and
  //   the test never fires.  Not once, not eventually -- never.
  //
  //   And that is not a contrived input.  It is the characteristic failure of
  //   the hardware this module is guarding: a ring oscillator sampled by a
  //   clock it is asynchronous to injection-locks to that clock and emits a
  //   short periodic sequence.  The mandated test can be structurally blind to
  //   the single most likely way this noise source dies.
  //
  // Counting both values costs one comparator and doubles the false-positive
  // rate from 2^-20 to 2^-19 per window, which against a 65 536-read service
  // life is nothing.  tb/unit/test_entropy_health.py's PERIODIC scenario is
  // the regression for it.
  logic [WIN_W-1:0] ap_pos_q;       // position within the window
  logic [CNT_W-1:0] ap_ones_q;      // occurrences of 1 so far
  logic             ap_fail_q;

  wire at_window_start = (ap_pos_q == WIN_W'(0));
  wire [CNT_W-1:0] ap_ones_now =
      (at_window_start ? CNT_W'(0) : ap_ones_q) + (sample ? CNT_W'(1) : CNT_W'(0));
  wire at_window_end = (ap_pos_q == WIN_W'(AP_WINDOW - 1));
  // The window is full when at_window_end is evaluated, so the zero count is
  // the window length minus the ones.
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
  // The properties are about the MECHANISM, so tb/formal instantiates this with
  // a small window -- the same trick rvntt_bpred's proof uses for its BTB.  A
  // 1024-deep window would make the adaptive test's counterexample 1024 steps
  // long and prove nothing the mechanism does not already show.
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  // THE MACHINE STARTS FROM RESET.  Without this, `rst_n` is a free input the
  // solver holds high forever, the registers keep their arbitrary step-0 values
  // and every state invariant below is trivially violated at step 2 -- which is
  // exactly what happened.  rvntt_bpred and rvntt_muldiv both carry the same
  // line for the same reason.
  initial assume (!rst_n);

  // 1. STICKY.  SP 800-90B takes a failed source permanently out of service and
  //    Zkr's DEAD latches until reset.  One property, both requirements.
  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && $past(fail))
      a_fail_is_sticky: assert (fail);
  end

  // 2. THE REPETITION TEST FIRES ON A STUCK SOURCE, within its cutoff.  This is
  //    the one that matters: it is the reason the module exists, and asserting
  //    it here is what stops the simulation test from being the only evidence.
  //    Stated as a bound on the RUN COUNTER rather than as a temporal
  //    "eventually", because the counter IS the mechanism -- MODS_A A15's
  //    lesson about stating the invariant instead of the conclusion.

  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && $past(sample_valid) &&
        $past(run_next) >= RUN_W'(REP_CUTOFF))
      a_rep_fires: assert (rep_fail_q);
  end

  // 3. NO SPURIOUS FAILURE.  A health test that fires without cause takes a
  //    working source out of service, which is a denial of service dressed as
  //    caution.  Nothing can fail before a sample has even been presented.
  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n && !$past(have_prev_q) &&
        !$past(sample_valid))
      a_no_fail_without_samples: assert (!fail);
  end

  // 4. THE WINDOW COUNTER IS A COUNTER.  If it can exceed the window it is
  //    checking, the adaptive test compares a count against the wrong
  //    denominator -- the same shape of bug as rvntt_bpred's RAS occupancy.
  // ---- the STATE invariants -------------------------------------------
  // GUARDED ON RESET HAVING HAPPENED, and the reason is worth writing down
  // because it is a general trap and it caught three assertions here at once.
  // sby's BMC starts from an ARBITRARY initial state; these registers have no
  // declaration initialisers, so at step 0 the run counter and the window
  // counters hold whatever the solver likes.  An invariant about STATE has to
  // be claimed from the first reset onward, not from step 0 -- which is why
  // rvntt_bpred's own counter bounds sit inside the same guard.
  always_ff @(posedge clk) if (f_past_valid && $past(rst_n) && rst_n) begin
    // WIN_W'(AP_WINDOW) IS ZERO, and that is not a typo in the standard -- it
    // is what a size cast does when the value needs exactly one more bit than
    // the target.  $clog2(1024) is 10, so 10'(1024) wraps to 0 and the first
    // version of this assertion read `ap_pos_q < 0`: VACUOUSLY FALSE at every
    // parameterisation, including the shipped one.  It was found the first time
    // the proof was run, which is the argument for running it at a small window
    // rather than reasoning about the big one.
    a_window_bounded: assert (ap_pos_q <= WIN_W'(AP_WINDOW - 1));
    a_count_bounded:  assert (ap_ones_q <= CNT_W'(AP_WINDOW));
    // The run counter never passes the cutoff without the test having fired --
    // the invariant the repetition test IS, rather than the conclusion it
    // reaches.  MODS_A A15's lesson, applied to a much smaller circuit.
    a_run_bounded:    assert (run_q <= RUN_W'(REP_CUTOFF) || rep_fail_q);
  end
`endif

endmodule

`default_nettype wire
