// ============================================================================
// rvntt_muldiv -- the M extension's functional unit (MODS_A A14), and the first
// user of the generic multi-cycle EX handshake.
//
// THE HANDSHAKE IS THE POINT, NOT THE ARITHMETIC.  MODS_A A14 asks for plan
// §8 I1's design (a) -- "stall IF/ID/EX for L-1 cycles" -- built GENERICALLY,
// with the latency a property of the unit rather than of `M`, so that the Xkntt
// Tier-1 unit inherits the mechanism instead of growing a second one.  The
// contract this module implements, and the one I1's unit must implement too:
//
//   * `req` is high for EVERY cycle the instruction is in EX.  It is not a
//     pulse.  The unit starts when it sees `req` while idle.
//   * `done` is high on the LAST of those cycles, and `result` is valid on
//     exactly that cycle and no other.
//   * The core stalls on `req && !done`.  Nothing else in the core needs to
//     know what kind of unit this is.
//   * If `req` drops while the unit is running, the instruction was flushed:
//     abort.  Nothing here holds architectural state, so an abort costs only
//     the cycles already spent.
//   * OCCUPANCY IS AT LEAST 2.  `done` is a function of registered state, so a
//     one-cycle unit cannot be expressed here -- it would not need this
//     mechanism anyway.
//
// OPERANDS ARE CAPTURED ON THE START CYCLE.  `a` and `b` come from the
// forwarding muxes, which are re-evaluated every cycle; while the pipeline is
// stalled the older instructions behind this one drain out of MEM and WB, so on
// the second stall cycle FWD_MEM stops matching, on the third FWD_WB stops
// matching, and the mux falls back to the ID-time register value, which is
// stale by two instructions.  A unit that re-read its inputs would compute with
// the right operands on cycle 0 and the wrong ones after -- silently, because
// the answer is wrong only when a producer happened to be close enough behind.
//
// THE ENABLE IS LOAD-BEARING FOR THE DIVIDER AND STRUCTURALLY REDUNDANT FOR THE
// MULTIPLIER, which is worth stating because it is not obvious and because it
// was established by fault injection rather than by reasoning.  A mutation
// removing the enable from m_a_q/m_b_q ESCAPED every test in the tree, and
// escaped correctly: the multiplier is a three-deep register CHAIN whose
// latency equals its depth, so the value read on the done cycle is the product
// of the operands that were present on the START cycle whatever the enable
// does -- the later reloads are still in flight behind it.  The divider's state
// is a LOOP, not a chain, so reloading `divisor_q` or `quo_q` mid-operation
// destroys it.  The enable is written the same way for both anyway, because a
// register that only happens to be safe is a register the next latency change
// makes unsafe.  tb/mutate/run_mutation.py carries the finding.
//
// The bug that mutation was aiming at is real, and lives one level up: it is
// rvntt_core wiring this unit to id_ex_q.rs1_data instead of to the forwarding
// muxes.  That mutation is in the manifest and is caught.
//
// THE LATENCIES ARE DATA-INDEPENDENT BY CONSTRUCTION.  The divider runs its 32
// iterations whatever the operands are.  An early-out on a small dividend would
// be free performance and would make tb/cosim/cycle_model.py unbuildable -- the
// model predicts the span from the retired instruction stream, and a latency it
// cannot compute from the opcode alone is a latency it cannot predict.  (It is
// also the constant-time property plan §B4 wants, but that is not why.)
//
// NO RESET ON THE MULTIPLIER PIPELINE, DELIBERATELY.  DSP48E1's A, B, M and P
// registers have only a SYNCHRONOUS reset; an asynchronous one -- which is what
// the rest of this core uses -- forces Vivado to build the register in fabric
// instead of packing it into the DSP, which is the whole point of the three
// stages.  Their contents are meaningless until `done`, and `done` comes from
// `active_q`, which is reset.  Declaration initialisers keep four-state
// simulators from propagating X out of a unit that is not running.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
// ============================================================================
`default_nettype none

module rvntt_muldiv #(
    // MODS_A2 A25.  The EX-occupancy contract, DEFAULTED FROM THE PACKAGE so
    // that the core, the cycle model and Spike keep taking the one number
    // rv32i_pkg holds.  It is a parameter rather than a localparam for exactly
    // one reason: A25 has to answer "what does one register level of the
    // product pipeline actually cost in nanoseconds", and that is a question
    // only a real post-route run can answer -- fpga/scripts/synth_ooc.sh
    // sweeps it out of context.  NOTHING INSTANTIATES THIS MODULE WITH A
    // DIFFERENT VALUE, and tb/unit/test_isa_consistency.py checks that.
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
    // `logic` and not `wire`: driven procedurally by the arithmetic below, and
    // continuously by the free value under RVNTT_ABSTRACT_MULDIV.
    output logic [31:0] result
);

  localparam int DIV_CYCLES = rv32i_pkg::MULDIV_DIV_CYCLES;

  // Register levels on the PRODUCT side.  Occupancy L gives L-1 clock edges
  // between the operands appearing and the result being read; one of those is
  // spent capturing the operands, so the product pipeline gets L-2.
  //
  //   L = 4 : operands | product | delay      <- A14's arrangement
  //   L = 3 : operands | product              <- A25's target
  //   L = 2 : operands, then combinational    <- the multiply after a flop
  //
  // At L = 2 the 33x33 is combinational from the OPERAND REGISTER, not from
  // the module input.  That distinction is the whole reason the operand
  // capture is kept rather than the product register: the alternative puts a
  // 33x33 multiplier directly behind the forwarding mux, which is where the
  // core's critical path already is.
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

  // `req` is in the term rather than assumed.  Without it, a request that
  // dropped on the very cycle the counter reached its target -- an abort
  // arriving exactly at the finish line -- would raise `done` for an
  // instruction that is no longer in EX.  The core would not act on it, because
  // it computes `ex_stall = req && !done` and latches nothing when req is low,
  // so this is harmless in the pipeline as it stands and would stop being
  // harmless the moment a second consumer read `done` directly.  The proof
  // found it at step 4 -- one AND gate, and the module's stated contract
  // becomes literally true instead of nearly true.
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
      // Back-to-back is not a special case: the next instruction's first EX
      // cycle is the cycle after this one, and `start` fires there because
      // active_q is low again.
      active_q <= 1'b0;
    end else begin
      cnt_q <= cnt_q + 6'd1;
    end
  end

  // ==========================================================================
  // MUL / MULH / MULHSU / MULHU -- one 33x33 signed multiplier
  // ==========================================================================
  // All four want the full 64-bit product and differ only in how the operands
  // are interpreted and which half is returned, so there is one multiplier and
  // a sign-extension rule.  Extending each 32-bit operand to 33 bits -- with
  // its sign bit if the operation reads it as signed, with zero otherwise --
  // turns "signed x unsigned" into an ordinary signed multiply, which is the
  // only way MULHSU is not a third datapath.
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

  // The product of two operands that each fit in 32 bits needs at most 64, so
  // bits 65:64 are redundant sign extension of the 33x33 form.  Scoped rather
  // than waived, so UNUSEDSIGNAL stays live for the rest of the module.
  /* verilator lint_off UNUSEDSIGNAL */
  wire signed [65:0] m_prod = m_a_q * m_b_q;
  /* verilator lint_on UNUSEDSIGNAL */

  always_ff @(posedge clk) begin
    // Only the operand registers take an enable, so that Vivado sees plain
    // unconditional M and P registers to pack.
    if (start) begin
      m_a_q  <= a_ext;
      m_b_q  <= b_ext;
      m_hi_q <= (op[1:0] != 2'b00);      // everything but MUL wants the top half
    end
  end

  // The product pipeline, MUL_PIPE deep.  Written as a chain rather than as
  // two named registers so that the latency really is a parameter: A14's
  // comment says the three stages exist so Vivado can pack AREG/BREG, MREG and
  // PREG, and dropping one by hand while leaving MUL_CYCLES at 4 would make
  // `done` fire a cycle after the result was already stale.
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
  // The loop runs on unsigned magnitudes and the signs are reapplied at the
  // end, which is what makes one loop serve all four operations.  The two
  // mandated edge cases are handled very differently and it is worth being
  // explicit about which is which:
  //
  //   DIVISION BY ZERO is a genuine special case and is detected at capture:
  //   the loop's answer for a zero divisor is meaningless, so `div_by_zero_q`
  //   bypasses it entirely.  Quotient is all ones, remainder is the ORIGINAL
  //   dividend -- signed, not its magnitude, which is why dividend_q is kept.
  //
  //   SIGNED OVERFLOW, -2^31 / -1, IS NOT A SPECIAL CASE HERE, and adding one
  //   would be dead logic.  |-2^31| is 0x80000000 in 32-bit two's complement,
  //   the loop divides it by 1 to give a magnitude of 0x80000000 and a
  //   remainder of 0, and the sign rule (negative iff exactly one operand is
  //   negative) says positive -- so the result is 0x80000000 unnegated, which
  //   is the -2^31 the specification mandates.  The remainder is 0 with the
  //   dividend's sign applied to it, which is still 0.  Both fall out of the
  //   general path.  There is a directed test for it anyway, precisely because
  //   "it happens to work" is a claim that needs checking rather than asserting.
  //
  // NEITHER CASE TRAPS.  RISC-V division raises no exceptions at all, so there
  // is no trap output on this module and nothing in rvntt_core gates one.
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
  // can exceed 32 bits; the retained value never does -- when the subtract does
  // not fit, bit 32 is provably zero, and when it does, the difference is less
  // than the divisor.
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
  // The arithmetic, abstracted away -- for riscv-formal only (MODS_A A15)
  // ==========================================================================
  // A COMBINATIONAL 33x33 MULTIPLIER IS THE CLASSIC WORST CASE FOR A SAT
  // SOLVER, and riscv-formal unrolls the whole core fourteen times.  Adding
  // this unit took the 43 RV32I checks from about 40 seconds for the entire set
  // to several hundred seconds EACH -- and the arithmetic is in the cone of the
  // RVFI outputs whether or not any check reads it, so the cost is paid by
  // every check for the benefit of none.
  //
  // MODS_A 3.2 anticipated trouble here and named the wrong cause: it expected
  // the 34-cycle DIVIDER to force the BMC depth from 14 to about 46.  Depth is
  // not the problem and raising it would not have helped.
  //
  // What is abstracted, and what is emphatically NOT.  `result` becomes a free
  // value -- a fresh unconstrained 32-bit number every cycle.  The SEQUENCER is
  // untouched: `active_q`, `cnt_q`, `is_div_q` and therefore `done` are exactly
  // the real ones, so the pipeline's stall behaviour, its bubble insertion and
  // its retirement timing are all still the real design's.  The 43 checks do
  // not care what M computes; they care that the pipeline stays self-consistent
  // around it, and that is preserved exactly rather than approximated.
  //
  // This is MODS_A 3.2's route 2 -- "prove the divider standalone, and let
  // riscv-formal establish only that the pipeline retires it correctly" -- with
  // the abstraction placed where it actually pays.  The arithmetic is proved by
  // tb/formal/rvntt_muldiv.sby, by the eight rv32um tests, and by lockstep
  // cosimulation against Spike; none of those three is weakened by this define,
  // because none of them sets it.
  //
  // A DELIBERATELY SEPARATE DEFINE FROM `RISCV_FORMAL`.  The standalone proof
  // compiles this file with FORMAL and must see the real arithmetic; naming the
  // abstraction after the tool that needs it makes it impossible to switch on
  // by accident and greppable when a result looks too good.
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
  // The properties that make this module's contract checkable on its own, at
  // whatever depth its own latency needs -- MODS_A §3.2 route 2, which exists
  // because a 34-cycle divider does not fit riscv-formal's depth-14 window.
  //
  // f_started_q: with an asynchronous reset the flop's value DURING the reset
  // cycle is its initialiser, not its reset value, so without this guard the
  // solver may invent a first step in which the unit is already running.  The
  // same shape, and the same fix, as rvntt_rvfi.sv.
  /* verilator lint_off PROCASSINIT */
  logic f_started_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) f_started_q <= 1'b0;
    else        f_started_q <= 1'b1;
  end

  // A STANDALONE PROOF NEEDS A RESET, and this module is the one place in the
  // core where that is not automatic: the multiplier's pipeline registers are
  // deliberately unreset so Vivado can pack them into the DSP (see the header),
  // so with rst_n left free the solver simply starts from a state the hardware
  // cannot reach -- a `done` on step 0 over arbitrary register contents -- and
  // every property below becomes a claim about nothing.  The first run of this
  // proof failed on exactly that, at step 0, before reaching any arithmetic.
  //
  // One cycle of reset is enough because rst_n is asynchronous: the sequencer's
  // registers are at their reset values DURING step 0, not from step 1.
  initial assume (!rst_n);

  always_comb begin
    // 1. THE HANDSHAKE.  `done` never fires without a request, which is what
    //    lets the core write `stall = req && !done` and nothing more.  Now
    //    structural rather than emergent -- see the assignment -- so this
    //    documents the contract rather than discovering it.
    a_done_needs_req:   assert (!done || req);
    // 1b. ... and it never reports an operation that did not start inside this
    //     trace, which is what makes every property below a statement about a
    //     state the hardware can actually be in.
    a_done_needs_start: assert (!done || f_started_op_q);
    // 2. The counter never runs past the operation's own latency.  A counter
    //    that wrapped would produce a second, spurious `done`.
    a_cnt_bounded:    assert (!active_q || (cnt_q <= target));
  end

  // AN OPERATION THAT ACTUALLY STARTED INSIDE THE TRACE.  With rst_n a free
  // input and the datapath registers deliberately unreset (see the header), the
  // solver is otherwise free to invent a `done` on step 0 over arbitrary
  // register contents and every property below would be a lie about a state the
  // hardware cannot reach.  The DECLARATION INITIALISER is the load-bearing
  // half, not the reset: an asynchronous reset leaves the flop at its init
  // value during the reset cycle itself.
  /* verilator lint_off PROCASSINIT */
  logic f_started_op_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if      (!rst_n)        f_started_op_q <= 1'b0;
    else if (start)         f_started_op_q <= 1'b1;
    else if (!req || done)  f_started_op_q <= 1'b0;
  end

  // ---- THE DIVIDER, PROVED (MODS_A 3.2 route 2) ---------------------------
  // riscv-formal cannot reach this: a 34-cycle unit needs a BMC window nothing
  // else in that check set needs, and the multiplier beside it is abstracted
  // away there entirely.  So the divider is proved here, standing alone, at
  // whatever depth its own latency requires -- which is exactly what 3.2's
  // second route says to do.
  //
  // THE OBVIOUS PROPERTY DOES NOT SOLVE, AND THE REASON IS WORTH KNOWING.  The
  // natural statement of correctness is the algebraic identity
  //
  //      quotient * divisor + remainder == dividend,   remainder < divisor
  //
  // which pins the answer completely.  Written that way it asks the solver to
  // prove a 32-step shift-and-subtract loop equivalent to a symbolic 32x32
  // multiply -- multiplier equivalence, which is the canonical hard instance
  // for SAT.  bitwuzla reached step 34 in five seconds and then sat on that one
  // query for a quarter of an hour without returning.  Raising the depth,
  // changing the solver, or waiting longer would not have helped: the SHAPE of
  // the query is the problem, not its size.
  //
  // THE SAME STATEMENT, WITH NO MULTIPLICATION ANYWHERE.  Track the algebra
  // alongside the loop in ghost registers whose recurrences are shifts and adds,
  // and assert an INVARIANT after every iteration rather than a conclusion after
  // the last.  With D the dividend magnitude, V the divisor and k the iteration
  // count, the loop maintains
  //
  //      rem_q + Q_k*V == D >> (32-k)          and      rem_q < V
  //      quo_q         == (D << k) | Q_k
  //
  // where Q_k is the quotient so far.  Carrying Q_k*V, D >> (32-k), D << k and
  // Q_k as ghosts -- each updated by a shift and at most one add -- turns both
  // lines into 64-bit comparisons.  At k = 32 the low half of D << k is zero,
  // so the second line collapses to quo_q == Q and the first to r + Q*V == D:
  // the original identity, DERIVED rather than asserted, and it proves in
  // seconds instead of not at all.
  //
  // WHAT THIS IS AND IS NOT A PROOF OF.  It is a complete proof of the MAGNITUDE
  // LOOP -- the part that is 32 cycles of subtle state and the only part a
  // directed test cannot enumerate.  It says nothing about the sign fixup or the
  // quotient/remainder selection, which are a handful of combinational gates
  // covered by rv32um's eight tests, by a14_muldiv.S's four-way sign matrix and
  // by 1000 random programs against Spike.  That boundary is stated here rather
  // than left to be discovered, as MODS_A 3.2 asks.
  //
  // The ghosts are driven from `fits` and `divisor_q`, which the design also
  // uses -- so a mutation to `fits` itself moves both together.  That case is
  // covered by a_div_bounded: an inverted `fits` stops subtracting and the
  // remainder passes the divisor immediately.  Two mutations in
  // tb/mutate/run_mutation.py check that this proof is not vacuous.
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
      // 64 bits so that a mutated loop cannot hide inside a wrap: the honest
      // value never exceeds D, but the recurrence alone bounds Q_k*V only by
      // V*2^32.
      f_acc_q <= (f_acc_q << 1) + (fits ? {32'd0, divisor_q} : 64'd0);
      f_dhi_q <= {f_dhi_q[30:0], f_dlo_q[31]};
      f_dlo_q <= {f_dlo_q[30:0], 1'b0};
      f_qk_q  <= {f_qk_q[30:0], fits};
    end
  end

  // Whether the operation in flight reads its operands as signed.  Kept only
  // for the proof -- the datapath does not need it, because the signedness is
  // already baked into neg_quo_q, neg_rem_q and the magnitudes -- but the
  // remainder's SIGN RULE cannot be stated without it.  The first version of
  // a_rem_sign omitted it and failed at step 34 on a DIVU with a dividend above
  // 2^31: there the top bit of the dividend is a magnitude bit, not a sign, so
  // "the remainder agrees with the dividend in sign" is not a statement about
  // that operation at all.  A property that is true of only half the operations
  // it is asserted over is a wrong property, not a found bug.
  logic f_signed_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if      (!rst_n) f_signed_q <= 1'b0;
    else if (start)  f_signed_q <= d_is_signed;
  end

  // THE INVARIANT, after every iteration rather than only at the end.
  always_comb if (f_started_q && f_started_op_q && active_q && is_div_q
                  && !div_by_zero_q && (cnt_q >= 6'd1)) begin
    a_div_inv_rem: assert ({32'd0, rem_q} + f_acc_q == {32'd0, f_dhi_q});
    a_div_inv_quo: assert (quo_q == (f_dlo_q | f_qk_q));
    a_div_bounded: assert (rem_q < divisor_q);
  end

  always_comb if (f_started_q && f_started_op_q && done && is_div_q) begin
    if (!div_by_zero_q) begin
      // The conclusion the invariant delivers, restated at the finish line so a
      // reader does not have to run the induction in their head.
      a_div_quotient: assert (quo_q == f_qk_q);
      a_div_identity: assert ({32'd0, rem_q} + f_acc_q ==
                              {32'd0, f_dividend_mag});
      // Rounding toward zero, as the sign rule it implies: the remainder never
      // disagrees with the dividend in sign.
      a_rem_sign:     assert (!f_signed_q || (rem_mag == 32'd0) ||
                              (rem_mag[31] == dividend_q[31]));
    end else begin
      // The two mandated divide-by-zero results.  No trap: this module has no
      // trap output at all, which is the strongest form of "it cannot".
      a_divz_quot: assert (want_rem_q || (result == 32'hFFFF_FFFF));
      a_divz_rem:  assert (!want_rem_q || (result == dividend_q));
    end
  end

  // 3. LATENCY IS DATA-INDEPENDENT.  Stated as a property rather than left to
  //    inspection of the loop, because it is what tb/cosim/cycle_model.py
  //    depends on and nothing else would notice its loss.
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
