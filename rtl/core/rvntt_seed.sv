// ============================================================================
// rvntt_seed -- Zkr's `seed` CSR (0x015) and its state machine, with the noise
// source and the health tests underneath it.
//
//   * `seed` is read-write-only: an access that does not write is illegal
//     (rvntt_csr enforces it), and the written value is ignored.
//   * A successful ES16 read consumes the entropy and empties the buffer.
//   * Status in [31:30], entropy in [15:0], [29:16] read zero.
//   * DEAD latches until reset.
//
//   BIST -- from reset, while BIST_SAMPLES pass the continuous tests
//   WAIT -- live, fewer than 16 bits buffered
//   ES16 -- 16 bits ready; a read returns them and empties the buffer
//   DEAD -- a health test failed; terminal
//
// There is no cryptographic conditioning and no measured entropy rate: sixteen
// raw samples are shifted into a register and returned.  The source is
// UNCERTIFIED (see the README).
//
// The source is sampled only while refilling.  A correctly functioning source
// trips the repetition cutoff about once per 2^20 samples by construction, and
// DEAD latches; free-running at ~96 MHz that is eleven milliseconds.  Gating
// the sampler moves the expected trip to 2^20/16 = 65 536 reads.  During BIST
// the sampler free-runs.
//
// Package references are fully qualified with no `import` (Yosys).
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

    // A legal seed access that reads (a csrrw-class access to 0x015 that did
    // not trap), asserted for exactly one cycle per access.
    input  wire         rd_en,
    output wire  [31:0] rdata
);

  localparam logic [1:0] ST_BIST = 2'b00;
  localparam logic [1:0] ST_WAIT = 2'b01;
  localparam logic [1:0] ST_ES16 = 2'b10;
  localparam logic [1:0] ST_DEAD = 2'b11;

  localparam int BIST_W = $clog2(BIST_SAMPLES + 1);

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

  // Free-running through the start-up test, then only to refill; DEAD stops
  // it entirely.
  assign want_sample = !dead_q && (!bist_done || !full);

  // Only an ES16 read takes the bits; a read in BIST, WAIT or DEAD returns
  // its status and leaves the buffer alone.
  wire consume = rd_en && bist_done && !dead_q && full;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bist_q <= BIST_W'(0);
      buf_q  <= 16'h0;
      fill_q <= 5'd0;
      dead_q <= 1'b0;
    end else begin
      // A failure during BIST is still a failure.
      if (health_fail) dead_q <= 1'b1;

      if (!bist_done && sample_valid) bist_q <= bist_q + BIST_W'(1);

      // The consuming read and a fill can coincide, so the fill is computed
      // from the post-consume count rather than as an else-if.
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

  // Entropy appears only with ES16, so a status misread cannot hand software
  // stale bits.
  assign rdata = {status, 14'h0, (status == ST_ES16) ? buf_q : 16'h0};

`ifdef FORMAL
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n) begin
      // 1. DEAD is terminal.
      if ($past(dead_q)) a_dead_latches: assert (dead_q);

      // 2. A health failure reaches DEAD immediately.
      if ($past(health_fail)) a_fail_means_dead: assert (dead_q);

      // 3. A consuming read empties the buffer.
      if ($past(consume)) a_read_consumes: assert (fill_q <= 5'd1);
    end
  end

  always_comb begin
    // 4. Entropy only with ES16; reserved bits zero.
    a_reserved_zero: assert (rdata[29:16] == 14'h0);
    if (rdata[31:30] != ST_ES16) a_no_bits_unless_es16: assert (rdata[15:0] == 16'h0);
    // 5. DEAD outranks everything, including a full buffer.
    if (dead_q) a_dead_status: assert (rdata[31:30] == ST_DEAD);
    a_fill_bounded: assert (fill_q <= 5'd16);
  end
`endif

endmodule

`default_nettype wire
