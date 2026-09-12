// ============================================================================
// rvntt_muldiv -- the M extension's functional unit, on the generic
// multi-cycle EX handshake:
//
//   * `req` is high for every cycle the instruction is in EX (not a pulse);
//     the unit starts when it sees `req` while idle.
//   * `done` is high on the last of those cycles, and `result` is valid on
//     exactly that cycle.  The core stalls on `req && !done`.
//   * If `req` drops while running, the instruction was flushed: abort.
//   * Occupancy is at least 2 (`done` is a function of registered state).
//
// Operands are captured on the start cycle: they come from the forwarding
// muxes, and during the stall the producers behind this instruction drain out
// of MEM and WB.  The enable is load-bearing for the divider (a loop) and
// structurally redundant for the multiplier (a chain).
//
// Latencies are data-independent by construction: the divider always runs 32
// iterations, so tb/cosim/cycle_model.py can predict every span.
//
// No reset on the multiplier pipeline: DSP48E1 registers have only a
// synchronous reset, and an asynchronous one forces them into fabric.  Their
// contents are meaningless until `done`, which comes from reset state.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_muldiv #(
    // The EX-occupancy contract, defaulted from the package.  A parameter only
    // so fpga/scripts/synth_ooc.sh can sweep it; nothing instantiates this
    // module with a different value (tb/unit/test_isa_consistency.py checks).
    parameter int MUL_CYCLES = rv32i_pkg::MULDIV_MUL_CYCLES
) (
    input  wire         clk,
    input  wire         rst_n,

    // ---- the multi-cycle EX handshake --------------------------------------
    input  wire         req,        // EX holds a muldiv instruction this cycle
    input  wire  [2:0]  op,         // funct3, verbatim; all eight are legal
    input  wire  [31:0] a,          // forwarded rs1, valid on the start cycle
    input  wire  [31:0] b,          // forwarded rs2, valid on the start cycle

    output wire         done,       // last EX cycle; `result` is valid now
    // `logic`: driven procedurally below, and continuously by the free value
    // under RVNTT_ABSTRACT_MULDIV.
    output logic [31:0] result
);

  localparam int DIV_CYCLES = rv32i_pkg::MULDIV_DIV_CYCLES;

  // Register levels on the product side.  Occupancy L gives L-1 edges between
  // the operands appearing and the result being read; one is spent capturing
  // the operands.  At L = 2 the 33x33 is combinational from the OPERAND
  // REGISTER, not from the module input (which would put a multiplier behind
  // the forwarding mux).
  localparam int MUL_PIPE = MUL_CYCLES - 2;

  // ==========================================================================
  // Sequencing
  // ==========================================================================
  logic       active_q;
  logic [5:0] cnt_q;          // cycles elapsed since the start cycle
  logic       is_div_q;

  wire        start     = req && !active_q;
  wire        op_is_div = op[2];
  wire [5:0]  target    = is_div_q ? 6'(DIV_CYCLES - 1) : 6'(MUL_CYCLES - 1);

  // `req` is in the term so an abort arriving exactly at the finish line does
  // not raise `done` for an instruction no longer in EX.
  assign done = req && active_q && (cnt_q == target);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_q <= 1'b0;
      cnt_q    <= 6'd0;
      is_div_q <= 1'b0;
    end else if (!req) begin
      active_q <= 1'b0;              // flushed, or the instruction retired
    end else if (start) begin
      active_q <= 1'b1;
      cnt_q    <= 6'd1;
      is_div_q <= op_is_div;
    end else if (done) begin
      // Back-to-back needs no special case: `start` fires on the next cycle.
      active_q <= 1'b0;
    end else begin
      cnt_q <= cnt_q + 6'd1;
    end
  end

  // ==========================================================================
  // MUL / MULH / MULHSU / MULHU -- one 33x33 signed multiplier
  // ==========================================================================
  // Each operand is extended to 33 bits with its sign bit (if read as signed)
  // or zero, which turns "signed x unsigned" into an ordinary signed multiply.
  //
  //   MUL    000  signed   x signed    low 32
  //   MULH   001  signed   x signed    high 32
  //   MULHSU 010  signed   x unsigned  high 32
  //   MULHU  011  unsigned x unsigned  high 32
  wire a_is_signed = (op[1:0] != 2'b11);   // everything but MULHU
  wire b_is_signed = (op[1]   == 1'b0);    // MUL and MULH only

  wire signed [32:0] a_ext = $signed({a_is_signed & a[31], a});
  wire signed [32:0] b_ext = $signed({b_is_signed & b[31], b});

  /* verilator lint_off PROCASSINIT */
  logic signed [32:0] m_a_q = '0, m_b_q = '0;
  logic               m_hi_q = 1'b0;
  /* verilator lint_on PROCASSINIT */

  // Bits 65:64 are redundant sign extension of the 33x33 form.
  /* verilator lint_off UNUSEDSIGNAL */
  wire signed [65:0] m_prod = m_a_q * m_b_q;
  /* verilator lint_on UNUSEDSIGNAL */

  always_ff @(posedge clk) begin
    // Only the operand registers take an enable, so Vivado sees plain
    // unconditional M and P registers to pack.
    if (start) begin
      m_a_q  <= a_ext;
      m_b_q  <= b_ext;
      m_hi_q <= (op[1:0] != 2'b00);      // everything but MUL wants the top half
    end
  end

  // The product pipeline, MUL_PIPE deep, as a chain so the latency really is
  // the parameter.
  wire [63:0] m_full;
  generate
    if (MUL_PIPE == 0) begin : g_mul_comb
      assign m_full = m_prod[63:0];
    end else begin : g_mul_pipe
      /* verilator lint_off PROCASSINIT */
      logic [63:0] m_pipe_q [MUL_PIPE];
      /* verilator lint_on PROCASSINIT */
      always_ff @(posedge clk) begin
        m_pipe_q[0] <= m_prod[63:0];
        for (int i = 1; i < MUL_PIPE; i++) m_pipe_q[i] <= m_pipe_q[i-1];
      end
      assign m_full = m_pipe_q[MUL_PIPE-1];
    end
  endgenerate

  // ==========================================================================
  // DIV / DIVU / REM / REMU -- radix-2 restoring, on magnitudes
  // ==========================================================================
  // One loop on unsigned magnitudes; the signs are reapplied at the end.
  // Division by zero is detected at capture and bypasses the loop (quotient
  // all ones, remainder the original signed dividend).  Signed overflow
  // (-2^31 / -1) is not a special case: the magnitude loop and the sign rule
  // produce 0x80000000 between them.  Neither traps.
  wire d_is_signed = !op[0];               // DIV (100) and REM (110)
  wire a_neg = d_is_signed && a[31];
  wire b_neg = d_is_signed && b[31];
  wire [31:0] a_mag = a_neg ? (~a + 32'd1) : a;
  wire [31:0] b_mag = b_neg ? (~b + 32'd1) : b;

  /* verilator lint_off PROCASSINIT */
  logic [31:0] divisor_q = '0;
  logic [31:0] rem_q = '0;
  logic [31:0] quo_q = '0;
  logic [31:0] dividend_q = '0;
  logic        neg_quo_q = 1'b0, neg_rem_q = 1'b0;
  logic        div_by_zero_q = 1'b0;
  logic        want_rem_q = 1'b0;
  /* verilator lint_on PROCASSINIT */

  // One restoring step: shift the next dividend bit into the remainder, and
  // subtract the divisor if it fits.  `shifted` is 33 bits because 2*rem + 1
  // can exceed 32; the retained value never does.
  wire [32:0] shifted = {rem_q, quo_q[31]};
  wire [32:0] diff    = shifted - {1'b0, divisor_q};
  wire        fits    = !diff[32];
  wire        iterate = active_q && is_div_q && (cnt_q <= 6'd32);

  always_ff @(posedge clk) begin
    if (start) begin
      divisor_q     <= b_mag;
      rem_q         <= 32'd0;
      quo_q         <= a_mag;
      dividend_q    <= a;
      neg_quo_q     <= a_neg ^ b_neg;
      neg_rem_q     <= a_neg;            // the remainder takes the DIVIDEND's sign
      div_by_zero_q <= (b == 32'd0);
      want_rem_q    <= op[1];            // REM (110) and REMU (111)
    end else if (iterate) begin
      rem_q <= fits ? diff[31:0] : shifted[31:0];
      quo_q <= {quo_q[30:0], fits};
    end
  end

  wire [31:0] quo_mag = neg_quo_q ? (~quo_q + 32'd1) : quo_q;
  wire [31:0] rem_mag = neg_rem_q ? (~rem_q + 32'd1) : rem_q;

  wire [31:0] div_result =
      div_by_zero_q ? (want_rem_q ? dividend_q : 32'hFFFF_FFFF)
                    : (want_rem_q ? rem_mag    : quo_mag);

`ifdef RVNTT_ABSTRACT_MULDIV
  // ==========================================================================
  // The arithmetic, abstracted away -- for riscv-formal only
  // ==========================================================================
  // A combinational 33x33 multiplier unrolled fourteen times is the classic
  // worst case for a SAT solver, and it is in the cone of the RVFI outputs
  // whether or not any check reads it.  `result` becomes a free value; the
  // sequencer (and therefore `done` and every stall) is untouched.  The
  // arithmetic is proved by the standalone FORMAL block below, by rv32um and
  // by cosimulation, none of which set this define.  Deliberately separate
  // from RISCV_FORMAL so it cannot be switched on by accident.
  /* verilator lint_off UNUSEDSIGNAL */
  (* anyseq *) logic [31:0] f_abstract_result;
  /* verilator lint_on UNUSEDSIGNAL */
  assign result = f_abstract_result;
`else
  always_comb begin
    if (is_div_q) result = div_result;
    else          result = m_hi_q ? m_full[63:32] : m_full[31:0];
  end
`endif

`ifdef FORMAL
  // The standalone proof, at depth 37 (a divide presents its result on its
  // 34th EX cycle).
  //
  // f_started_q: with an asynchronous reset the flop's value during the reset
  // cycle is its initialiser, so without this guard the solver may invent a
  // first step in which the unit is already running.
  /* verilator lint_off PROCASSINIT */
  logic f_started_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) f_started_q <= 1'b0;
    else        f_started_q <= 1'b1;
  end

  // A standalone proof needs a reset: the multiplier registers are
  // deliberately unreset, so with rst_n free the solver starts from a state
  // the hardware cannot reach.  One cycle suffices (rst_n is asynchronous).
  initial assume (!rst_n);

  always_comb begin
    // 1. `done` never fires without a request ...
    a_done_needs_req:   assert (!done || req);
    // 1b. ... nor for an operation that did not start inside this trace.
    a_done_needs_start: assert (!done || f_started_op_q);
    // 2. The counter never runs past the operation's latency.
    a_cnt_bounded:    assert (!active_q || (cnt_q <= target));
  end

  // An operation that actually started inside the trace.  The declaration
  // initialiser is the load-bearing half.
  /* verilator lint_off PROCASSINIT */
  logic f_started_op_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if      (!rst_n)        f_started_op_q <= 1'b0;
    else if (start)         f_started_op_q <= 1'b1;
    else if (!req || done)  f_started_op_q <= 1'b0;
  end

  // ---- the divider, proved -----------------------------------------------
  // The obvious statement -- quotient * divisor + remainder == dividend --
  // asks the solver for multiplier equivalence and does not return.  Instead
  // ghost registers track the algebra alongside the loop with shifts and
  // adds, and an invariant is asserted after every iteration.  With D the
  // dividend magnitude, V the divisor and k the iteration count:
  //
  //      rem_q + Q_k*V == D >> (32-k)          and      rem_q < V
  //      quo_q         == (D << k) | Q_k
  //
  // At k = 32 these collapse to the original identity, derived rather than
  // asserted.  This is a complete proof of the magnitude loop; the sign fixup
  // and the quotient/remainder selection are covered by rv32um, a14_muldiv.S
  // and cosimulation.
  wire [31:0] f_dividend_mag = neg_rem_q ? (~dividend_q + 32'd1) : dividend_q;

  logic [63:0] f_acc_q;      // Q_k * V
  logic [31:0] f_dhi_q;      // D >> (32-k)
  logic [31:0] f_dlo_q;      // (D << k) mod 2^32
  logic [31:0] f_qk_q;       // Q_k

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      f_acc_q <= 64'd0; f_dhi_q <= 32'd0; f_dlo_q <= 32'd0; f_qk_q <= 32'd0;
    end else if (start) begin
      f_acc_q <= 64'd0;
      f_dhi_q <= 32'd0;
      f_dlo_q <= a_mag;
      f_qk_q  <= 32'd0;
    end else if (iterate) begin
      // 64 bits so a mutated loop cannot hide inside a wrap.
      f_acc_q <= (f_acc_q << 1) + (fits ? {32'd0, divisor_q} : 64'd0);
      f_dhi_q <= {f_dhi_q[30:0], f_dlo_q[31]};
      f_dlo_q <= {f_dlo_q[30:0], 1'b0};
      f_qk_q  <= {f_qk_q[30:0], fits};
    end
  end

  // Whether the operation in flight reads its operands as signed; the
  // remainder's sign rule cannot be stated without it (a DIVU dividend above
  // 2^31 has a magnitude bit on top, not a sign).
  logic f_signed_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if      (!rst_n) f_signed_q <= 1'b0;
    else if (start)  f_signed_q <= d_is_signed;
  end

  // The invariant, after every iteration.
  always_comb if (f_started_q && f_started_op_q && active_q && is_div_q
                  && !div_by_zero_q && (cnt_q >= 6'd1)) begin
    a_div_inv_rem: assert ({32'd0, rem_q} + f_acc_q == {32'd0, f_dhi_q});
    a_div_inv_quo: assert (quo_q == (f_dlo_q | f_qk_q));
    a_div_bounded: assert (rem_q < divisor_q);
  end

  always_comb if (f_started_q && f_started_op_q && done && is_div_q) begin
    if (!div_by_zero_q) begin
      // The conclusion the invariant delivers, restated at the finish line.
      a_div_quotient: assert (quo_q == f_qk_q);
      a_div_identity: assert ({32'd0, rem_q} + f_acc_q ==
                              {32'd0, f_dividend_mag});
      // Rounding toward zero: the remainder never disagrees with the
      // dividend in sign.
      a_rem_sign:     assert (!f_signed_q || (rem_mag == 32'd0) ||
                              (rem_mag[31] == dividend_q[31]));
    end else begin
      // The two mandated divide-by-zero results.
      a_divz_quot: assert (want_rem_q || (result == 32'hFFFF_FFFF));
      a_divz_rem:  assert (!want_rem_q || (result == dividend_q));
    end
  end

  // 3. Latency is data-independent.
  always_ff @(posedge clk) begin
    if (f_started_q && rst_n && $past(rst_n) && $past(start) && req) begin
      a_lat_mul: assert (is_div_q || (cnt_q == 6'd1));
      a_lat_div: assert (!is_div_q || (cnt_q == 6'd1));
    end
    if (f_started_q && rst_n && $past(rst_n) && $past(active_q) && $past(req)
        && !$past(done) && req) begin
      a_progress: assert (cnt_q == $past(cnt_q) + 6'd1);
    end
  end
`endif

endmodule

`default_nettype wire
