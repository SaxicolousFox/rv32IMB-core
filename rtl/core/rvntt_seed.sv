// ============================================================================
// rvntt_seed -- Zkr's `seed` CSR (0x015) and its state machine (MODS_A2 A29,
// piece 3), with the noise source and the health tests underneath it.
//
// `seed` IS NOT A NORMAL CSR, and every one of these is a way to get it wrong:
//
//   * It is READ-WRITE-ONLY.  An access that does not WRITE is an illegal
//     instruction -- so `csrrs rd, seed, x0`, the ordinary way to read a CSR,
//     traps.  Software must use `csrrw`/`csrrwi`, or `csrrs`/`csrrc` with a
//     non-zero source.  The write's VALUE is ignored; requiring the write is
//     how the architecture makes "reading is destructive" impossible to do by
//     accident.  rvntt_csr.sv enforces this, because only it sees `wen`.
//   * A successful read CONSUMES the entropy.  Two consecutive ES16 reads must
//     not return the same bits from the same buffer.
//   * The status lives in [31:30] and the entropy in [15:0]; [29:16] are
//     reserved and read zero.
//   * DEAD LATCHES.  Once a health test fails the source is out of service
//     until reset -- which is both Zkr's requirement and SP 800-90B's.
//
// STATE MACHINE
//
//   BIST -- from reset, while the start-up test runs.  SP 800-90B requires a
//           run of samples to pass the continuous tests before the source is
//           used; BIST_SAMPLES is that run.  Reads return BIST and no entropy.
//   WAIT -- the source is live but fewer than 16 bits are buffered.
//   ES16 -- 16 bits are ready.  A read returns them and empties the buffer.
//   DEAD -- a health test failed.  Terminal.
//
// WHAT THIS DOES NOT DO, and it is in docs/a29-zkr.md as well because it is the
// kind of gap that gets forgotten: there is NO CRYPTOGRAPHIC CONDITIONING.
// Sixteen raw samples are shifted into a register and returned.  A certified
// design would run a vetted conditioning component and claim an entropy rate
// established by the SP 800-90B estimation track.  This claims nothing of the
// sort -- see the uncertified statement.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
// ============================================================================
`default_nettype none

module rvntt_seed #(
    parameter bit STUB         = 1,      // 1 everywhere but the bitstream
    parameter int BIST_SAMPLES = 1024,   // SP 800-90B start-up test length
    parameter int REP_CUTOFF   = 21,
    parameter int AP_WINDOW    = 1024,
    parameter int AP_CUTOFF    = 589
) (
    input  wire         clk,
    input  wire         rst_n,

    // The testbench's raw sample; ignored unless STUB.
    input  wire         stub_bit,

    // A LEGAL seed access that reads -- i.e. a csrrw-class access to 0x015 that
    // did not trap.  rvntt_csr.sv is what decides that; this module only
    // consumes.  Asserted for exactly one cycle per access.
    input  wire         rd_en,
    output wire  [31:0] rdata
);

  localparam logic [1:0] ST_BIST = 2'b00;
  localparam logic [1:0] ST_WAIT = 2'b01;
  localparam logic [1:0] ST_ES16 = 2'b10;
  localparam logic [1:0] ST_DEAD = 2'b11;

  localparam int BIST_W = $clog2(BIST_SAMPLES + 1);

  // ==========================================================================
  // THE SOURCE IS SAMPLED ONLY WHEN IT IS NEEDED, and that is not an
  // optimisation -- it is what makes the design usable at all.
  // ==========================================================================
  // SP 800-90B's repetition-count cutoff is C = 1 + ceil(-log2(alpha)/H) with a
  // recommended alpha of 2^-20, so a CORRECTLY FUNCTIONING source trips it
  // about once every 2^20 samples BY CONSTRUCTION.  That is the standard's
  // design point, not a defect in the source.
  //
  // Zkr's DEAD, however, LATCHES until reset.  Put those two facts together
  // with a free-running sampler at 96.246 MHz and the entropy source takes
  // itself permanently out of service after about ELEVEN MILLISECONDS of
  // ordinary operation.  That was measured, not predicted: the ideal-source
  // scenario in tb/unit/test_entropy_health.py went DEAD at 42 000 samples on
  // a run of 23 identical bits, which for a fair coin is a perfectly ordinary
  // 6% event over that length.
  //
  // Sampling only to refill the buffer costs 16 samples per seed read instead
  // of one per clock, which moves the expected trip from 2^20 CYCLES to 2^20/16
  // = 65 536 READS.  The limitation does not go away -- it cannot, without
  // choosing a different alpha or giving up DEAD's latching -- and it is stated
  // as a number in docs/a29-zkr.md rather than left to be discovered.
  //
  // During BIST the sampler free-runs, because the start-up test is a run of
  // BIST_SAMPLES consecutive samples and gating it would make it take forever.
  wire want_sample;
  wire sample, sample_valid_raw;
  rvntt_entropy #(.STUB(STUB)) u_noise (
      .clk          (clk),
      .rst_n        (rst_n),
      .stub_bit     (stub_bit),
      .sample       (sample),
      .sample_valid (sample_valid_raw)
  );
  wire sample_valid = sample_valid_raw && want_sample;

  wire health_fail;
  rvntt_entropy_health #(
      .REP_CUTOFF (REP_CUTOFF),
      .AP_WINDOW  (AP_WINDOW),
      .AP_CUTOFF  (AP_CUTOFF)
  ) u_health (
      .clk          (clk),
      .rst_n        (rst_n),
      .sample       (sample),
      .sample_valid (sample_valid),
      .fail         (health_fail)
  );

  logic [BIST_W-1:0] bist_q;       // samples seen since reset, saturating
  logic [15:0]       buf_q;
  logic [4:0]        fill_q;       // 0..16
  logic              dead_q;

  wire bist_done = (bist_q >= BIST_W'(BIST_SAMPLES));
  wire full      = (fill_q == 5'd16);

  // Free-running through the start-up test, then only to refill.  DEAD stops it
  // entirely: a source that is out of service must not keep consuming.
  assign want_sample = !dead_q && (!bist_done || !full);

  // The read that consumes.  Only an ES16 read takes the bits: a read in BIST,
  // WAIT or DEAD returns its status and leaves the buffer alone, which is what
  // makes polling free.
  wire consume = rd_en && bist_done && !dead_q && full;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bist_q <= BIST_W'(0);
      buf_q  <= 16'h0;
      fill_q <= 5'd0;
      dead_q <= 1'b0;
    end else begin
      // DEAD is checked first and set unconditionally: a failure during BIST is
      // still a failure, and the start-up test is exactly the window in which
      // it is most likely.
      if (health_fail) dead_q <= 1'b1;

      if (!bist_done && sample_valid) bist_q <= bist_q + BIST_W'(1);

      // The buffer empties on the consuming read and fills from the source.
      // Both can happen in the same cycle -- the source free-runs and does not
      // know about reads -- so the fill is computed from the POST-consume
      // count rather than being an else-if, which would drop a sample every
      // time software polled at the wrong moment.
      if (consume) begin
        fill_q <= sample_valid ? 5'd1 : 5'd0;
        buf_q  <= {15'h0, sample};
      end else if (sample_valid && !full) begin
        fill_q <= fill_q + 5'd1;
        buf_q  <= {buf_q[14:0], sample};
      end
    end
  end

  logic [1:0] status;
  always_comb begin
    if      (dead_q)     status = ST_DEAD;
    else if (!bist_done) status = ST_BIST;
    else if (full)       status = ST_ES16;
    else                 status = ST_WAIT;
  end

  // [29:16] are reserved and read zero; entropy appears ONLY with ES16, so a
  // status misread cannot hand software stale bits and have them look fresh.
  assign rdata = {status, 14'h0, (status == ST_ES16) ? buf_q : 16'h0};

`ifdef FORMAL
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n) begin
      // 1. DEAD IS TERMINAL.  The whole security value of a health test is that
      //    a failed source cannot come back.
      if ($past(dead_q)) a_dead_latches: assert (dead_q);

      // 2. A HEALTH FAILURE REACHES DEAD IMMEDIATELY, not eventually.
      if ($past(health_fail)) a_fail_means_dead: assert (dead_q);

      // 3. A CONSUMING READ EMPTIES THE BUFFER.  Without this, two reads in a
      //    row return the same 16 bits and software cannot tell -- the exact
      //    failure that makes an RNG look like it works.
      if ($past(consume)) a_read_consumes: assert (fill_q <= 5'd1);
    end
  end

  always_comb begin
    // 4. ENTROPY ONLY WITH ES16.  Reserved bits zero, and no bits at all in
    //    BIST, WAIT or DEAD.
    a_reserved_zero: assert (rdata[29:16] == 14'h0);
    if (rdata[31:30] != ST_ES16) a_no_bits_unless_es16: assert (rdata[15:0] == 16'h0);
    // 5. DEAD OUTRANKS EVERYTHING, including a full buffer.
    if (dead_q) a_dead_status: assert (rdata[31:30] == ST_DEAD);
    a_fill_bounded: assert (fill_q <= 5'd16);
  end
`endif

endmodule

`default_nettype wire
