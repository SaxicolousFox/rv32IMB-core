// ============================================================================
// rvntt_tier1_probe -- MODS_A2 A24.  A REPRESENTATIVE Tier-1 butterfly, built
// to be synthesised out of context and timed.  IT IS NOT THE COPROCESSOR.
//
// WHAT THIS IS FOR.  MODS_A2 §3.3 concluded that Tier-1 `Xkntt` cannot live in
// its own clock domain: `docs/isa-spec.md` freezes `kmm` at 4 cycles of EX
// occupancy, and a clock-domain crossing costs two to three synchroniser flops
// in each direction before the unit does any arithmetic at all.  So Tier 1
// shares the core's clock, and therefore THE CORE'S Fmax IS A HARD FLOOR THAT
// THIS DATAPATH MUST MEET.  Committing the core to a frequency the butterfly
// cannot reach is the §9 failure mode -- discovered at M15, two tracks away
// from its cause.  This module exists so that number is known BEFORE A26 and
// A27 commit the core to anything.
//
// WHAT THIS IS NOT.  It is not §B1's or §B2's design and must never be
// instantiated by anything that ships.  It lives in rtl/probe/ rather than
// rtl/ntt/ for exactly that reason: rtl/ntt/ is Track B's, and a probe sitting
// in it would eventually be mistaken for a starting point.  Track B is free to
// build something completely different; what carries across is the FREQUENCY,
// not the RTL.
//
// PESSIMISTIC IN THE RIGHT WAY, which is the only way a probe is worth
// running.  The three operations sharing one datapath -- `kmm`, `kbfct`,
// `kbfgs` -- are the frozen contract's own three, so the operand-select mux in
// front and the result mux behind are real, not elided.  `kbfgs` drags a
// Barrett reduction in beside the Montgomery one; a probe that timed `kmm`
// alone would report a number the full instruction set cannot hold.  A single
// butterfly, not P lanes, because lanes are independent and add area rather
// than depth.
//
// TWO STRUCTURAL DECISIONS WERE MADE BY MEASUREMENT, NOT BY REASONING, and
// both are recorded because the first arrangement of this file closed at
// 78 MHz and would have capped the whole round if it had been believed:
//
//   1. THE BARRETT PATH RUNS BESIDE THE MONTGOMERY PATH, NOT AFTER IT.
//      `kbfgs` computes a' = barrett(a + b) and b' = mont(z * (b - a)), and
//      NEITHER depends on the other.  Putting Barrett in the last segment --
//      the obvious reading of "then the conditional add/subtract" -- gave a
//      13.638 ns path with two dependent DSPs in series and no register
//      between them.  Starting it in the multiply segment costs nothing and
//      removes the entire chain.  The first partition's routed path is kept at
//      docs/a24-tier1-probe.md; it is the finding, not a false start.
//
//   2. WHICH MULTIPLIES GET A DSP IS DECIDED BY THE CONSTANT'S POPULATION
//      COUNT, and that too was measured rather than reasoned.  The first
//      attempt said "only the variable x variable multiply gets a DSP" and
//      pushed every constant into fabric.  That is wrong for BARR_V = 20159 =
//      0x4EBF, which has ELEVEN set bits: the shift-add tree it becomes was
//      9.492 ns with eight CARRY4 levels, WORSE than the DSP it replaced.  It
//      is right for q = 3329 (four set bits) and for QINV = 62209 truncated to
//      its low 16 bits (seven, and no high carries to propagate).  So:
//
//        z * b            variable x variable, 16x16      -> DSP
//        (a + b) * BARR_V constant, 11 set bits           -> DSP
//        p[15:0] * QINV   constant, low 16 bits only      -> fabric
//        t * q            constant, 4 set bits            -> fabric
//
//      This part has 240 DSP48E1s and one butterfly uses two of them, so the
//      choice costs nothing a real Tier-1 unit would not also spend.
//
//      THE ATTRIBUTES ARE ON MODULE-LEVEL SIGNALS, NOT ON THE MODULE.  A
//      module-level `use_dsp = "no"` with a signal-level `"yes"` inside it did
//      NOT behave as the precedence rules suggest -- the netlist came back with
//      DSP=0 and 769 LUTs.  The cell histogram said so, which is the whole
//      reason synth_ooc.tcl prints one; it is the same lesson as A14's DSP=0
//      report, arriving from the opposite direction.  TAKE THE VERDICT FROM
//      THE ARTEFACT.
//
// WHAT IT DELIBERATELY DOES NOT MEASURE, so the number is not over-claimed:
// the forwarding mux that delivers `rs1`/`rs2` and the writeback mux that
// captures `result` are the CORE's paths, outside this module and outside the
// OOC run.  This reports the unit's internal register-to-register frequency.
// The core's own Fmax is measured where it always has been -- a full SoC
// implementation run -- and A24's stop rule compares the two with margin
// precisely because they are measured separately.
//
// ALL THREE FROZEN LATENCIES ARE HONOURED AT ONCE, WHICH IS NOT THE SAME AS
// BUILDING THREE PIPELINES.  `docs/isa-spec.md` gives `kmm` 4 cycles of EX
// occupancy and `kbfct`/`kbfgs` 5.  Occupancy L means the result is valid L-1
// clock edges after the operands appear, so the natural reading is "kmm needs a
// pipeline one stage shorter" -- and that reading is WRONG and expensive.  A
// four-stage pipeline serves both: `kmm` does not use segment D at all (its
// result is `mont_c` sign-extended), so it TAPS OUT after segment C, three
// edges in, and `kbfct`/`kbfgs` take the fourth.  One datapath, one clock, two
// latencies, and the extra logic is one 32-bit mux on an output that is already
// muxed.
//
// The alternative -- shortening the pipeline to three stages so that every
// operation finishes in four -- fuses the multiply into the Montgomery
// reduction and was MEASURED at below 100 MHz, against 116.875 for the tapped
// arrangement.  That is why STAGES is a parameter: it is the evidence for the
// structural claim, not a knob anyone would turn.
//
//   STAGES = 4 : the real arrangement, kmm at 4 and kbfct/kbfgs at 5
//   STAGES = 3 : every operation in 4, by fusing segments -- the cost of
//                taking the frozen table's shorter number literally
//   STAGES = 2 : margin probe; nothing needs it
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).  This module deliberately imports NOTHING:
// it must synthesise standing alone, in a staging directory, with no core.
// ============================================================================
`default_nettype none

module rvntt_tier1_probe #(
    // 4 = what `kbfct`/`kbfgs` allow, 3 = what `kmm` allows, 2 = margin probe.
    parameter int STAGES = 4
) (
    input  wire         clk,
    input  wire         rst_n,

    // The multi-cycle EX handshake `rvntt_muldiv` defines, verbatim -- `req`
    // high for every cycle in EX, `done` on the last.  Tier 1 inherits the
    // mechanism rather than growing a second one (MODS_A A14), so the probe
    // carries the same ports; the sequencer is a shift register here because
    // this unit has no variable-latency operation.
    input  wire         req,
    input  wire  [1:0]  op,          // 0 = kmm, 1 = kbfct, 2 = kbfgs
    input  wire  [31:0] rs1,
    // rs2[31:16] is UNUSED and that is the frozen contract, not an oversight:
    // docs/isa-spec.md §4.1 says "`rs1[31:16]` and `rs2[31:16]` are ignored"
    // for `kmm`, and §4.2/§4.3 take only `rs2[15:0]` as zeta.  The port stays
    // 32 bits wide because a Tier-1 unit reads the register file through the
    // core's forwarding network, which delivers whole registers.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  [31:0] rs2,
    /* verilator lint_on UNUSEDSIGNAL */

    output wire         done,
    output wire  [31:0] result
);

  // Plan §1.3's constants.  Written here rather than referenced from
  // rv32i_pkg so the probe stands alone; model/modarith.py is the authority
  // and tb/probe/test_tier1_probe.py checks this module against it.
  localparam logic signed [15:0] Q      = 16'sd3329;
  localparam logic        [15:0] QINV   = 16'd62209;    // q^-1 mod 2^16
  localparam logic signed [15:0] BARR_V = 16'sd20159;   // ((1<<26) + q/2) / q

  localparam logic [1:0] OP_KMM   = 2'd0;
  localparam logic [1:0] OP_KBFCT = 2'd1;
  localparam logic [1:0] OP_KBFGS = 2'd2;

  // ==========================================================================
  // Segment A -- operand select, and the two independent sums.
  // ==========================================================================
  // `kbfgs` multiplies (b - a) and NOT (a - b).  That is Deviation 1 in
  // docs/isa-spec.md and the root CLAUDE.md's first known error in the plan:
  // implementing it the other way yields a working forward NTT and a silently
  // broken inverse.  It is repeated here because a probe that got it backwards
  // would still time correctly and would then be copied.
  wire signed [15:0] a_in = $signed(rs1[15:0]);
  wire signed [15:0] b_in = $signed(rs1[31:16]);
  wire signed [15:0] z_in = $signed(rs2[15:0]);

  logic signed [15:0] x_sel;    // the multiplier's data operand
  always_comb begin
    case (op)
      OP_KBFCT: x_sel = b_in;                  // t = mont(z * b)
      OP_KBFGS: x_sel = 16'(b_in - a_in);      // b' = mont(z * (b - a))
      OP_KMM:   x_sel = a_in;                  // rd = mont(rs1 * rs2)
      default:  x_sel = a_in;
    endcase
  end

  // (a + b) is `kbfgs`'s Barrett input and is computed here, in the SAME
  // segment as the operand select, so the Barrett chain starts one full stage
  // earlier than the naive reading of the spec would put it.  See decision 1
  // in the header.
  wire signed [15:0] sum_ab = 16'(a_in + b_in);

  logic signed [15:0] s1_x, s1_z, s1_a, s1_sum;
  logic        [1:0]  s1_op;
  always_ff @(posedge clk) begin
    s1_x   <= x_sel;
    s1_z   <= z_in;
    s1_a   <= a_in;
    s1_sum <= sum_ab;
    s1_op  <= op;
  end

  // ==========================================================================
  // Segment B -- the one real multiply, and Barrett's quotient estimate.
  // ==========================================================================
  // Registers on both sides let Vivado absorb them into the DSP48E1's A/B and
  // M/P registers, which is the difference between a multiplier that closes far
  // above this core's clock and one that does not (rvntt_muldiv records the
  // same thing for the M extension).
  (* use_dsp = "yes" *) wire signed [31:0] prod_c = s1_x * s1_z;

  // Barrett's first half, t = (V*a + (1<<25)) >> 26, running CONCURRENTLY with
  // the multiply above rather than after the Montgomery result.  BARR_V has
  // eleven set bits, so this goes on a DSP too -- see decision 2 in the header.
  (* use_dsp = "yes" *) wire signed [31:0] bq_mul = 32'(s1_sum) * 32'(BARR_V);
  wire signed [15:0] bq_c = 16'((bq_mul + 32'sd33554432) >>> 26);

  logic signed [31:0] s2_prod;
  logic signed [15:0] s2_a, s2_sum, s2_bq;
  logic        [1:0]  s2_op;

  // ==========================================================================
  // Segment C -- both reductions finish here, in parallel.
  // ==========================================================================
  //   Montgomery: t = (int16_t)(p * QINV);  (p - (int32_t)t * Q) >> 16
  // Two dependent multiplies, both by constants, both in fabric.  A24 names
  // this carry chain as the thing being looked for.
  // Both constants are small enough that fabric beats a DSP: QINV's product is
  // truncated to 16 bits so no high carries propagate, and q = 0xD01 has four
  // set bits.  Stated explicitly so a later synthesis heuristic cannot quietly
  // put a 4 ns DSP hop in the middle of this chain.
  // WRITTEN AS SHIFTS AND ADDS, NOT AS `*`, AND THAT IS LOAD-BEARING.
  // `use_dsp = "no"` did NOT keep these out of a DSP48E1 -- not on a net
  // declaration with an inline assignment, and not on an always_comb variable
  // either.  Both attempts came back with DSP48E1=4 in the cell histogram, and
  // the resulting 4.023 ns DSP hop in the middle of a two-multiply DEPENDENT
  // chain was the whole critical path (9.715 ns, 103 MHz).  Attributes were
  // asked twice and answered neither time; structure answers once.  This is
  // also how a real Montgomery unit for a fixed q is built, so nothing is
  // being distorted to flatter the number.
  //
  // THE CONSTANT STAYS THE ONE SOURCE OF TRUTH.  The first version of this
  // wrote the shift positions out by hand -- `+ (p_lo << 8) + (p_lo << 9) ...`
  // -- and guarded them with an elaboration check comparing a SECOND hand-
  // written bit list against QINV.  That check was vacuous: mistyping a shift
  // in the datapath left it silent, as fault injection showed, because it was
  // comparing one transcription against another rather than either against the
  // datapath.  Deriving the shifts from the constant removes the transcription
  // instead of guarding it.
  function automatic logic [31:0] mul_by_const(input logic [31:0] x,
                                               input logic [15:0] k);
    logic [31:0] acc;
    begin
      acc = '0;
      for (int i = 0; i < 16; i++) acc = acc + (k[i] ? (x << i) : 32'd0);
      mul_by_const = acc;
    end
  endfunction

  wire [15:0] p_lo = s2_prod[15:0];
  wire [15:0] mont_t = 16'(mul_by_const(32'(p_lo), QINV));  // low half is all we need

  wire signed [31:0] mont_tq =
         $signed(mul_by_const(32'($signed(mont_t)), 16'(Q)));
  wire signed [31:0] mont_d = s2_prod - mont_tq;
  wire signed [15:0] mont_c = 16'(mont_d >>> 16);   // exact: low 16 bits are zero

  // Barrett's second half, same treatment.
  wire signed [15:0] barr_c =
         16'(s2_sum - $signed(mul_by_const(32'($signed(s2_bq)), 16'(Q))));

  logic signed [15:0] s3_m, s3_barr, s3_a;
  logic        [1:0]  s3_op;

  // ==========================================================================
  // Segment D -- the butterfly proper, and the result mux.
  // ==========================================================================
  logic [31:0] y_c;
  always_comb begin
    case (s3_op)
      // rd = { (a - t)[15:0], (a + t)[15:0] }
      OP_KBFCT: y_c = {16'(s3_a - s3_m), 16'(s3_a + s3_m)};
      // rd = { b'[15:0], a'[15:0] }
      OP_KBFGS: y_c = {s3_m, s3_barr};
      // rd = sext32(montgomery_reduce(...))
      OP_KMM:   y_c = {{16{s3_m[15]}}, s3_m};
      default:  y_c = {{16{s3_m[15]}}, s3_m};
    endcase
  end

  logic [31:0] s4_y;

  // `kmm`'s early tap.  Segment D is a butterfly and a mux; `kmm` needs neither,
  // so its result is ready one full stage earlier.  This is what buys the
  // frozen table's 4 cycles without shortening the pipeline for everyone else.
  wire        s3_is_kmm = (s3_op == OP_KMM);
  wire [31:0] kmm_early = {{16{s3_m[15]}}, s3_m};

  // ==========================================================================
  // The optional register stages.
  // ==========================================================================
  // STAGES counts registers from operand capture to `result`.  Stage 1 and the
  // last stage are always present; the ones between are dropped as STAGES
  // falls, fusing the segments they separated.  Fusing from the middle rather
  // than the end keeps the operand capture and the result register -- the two
  // that the handshake contract actually requires -- in every configuration.
  //
  //   STAGES=4 : A | B | C | D
  //   STAGES=3 : A | B+C | D
  //   STAGES=2 : A | B+C+D
  generate
    if (STAGES >= 3) begin : g_s2_reg
      always_ff @(posedge clk) begin
        s2_prod <= prod_c;
        s2_a    <= s1_a;
        s2_sum  <= s1_sum;
        s2_bq   <= bq_c;
        s2_op   <= s1_op;
      end
    end else begin : g_s2_wire
      always_comb begin
        s2_prod = prod_c;
        s2_a    = s1_a;
        s2_sum  = s1_sum;
        s2_bq   = bq_c;
        s2_op   = s1_op;
      end
    end

    if (STAGES >= 4) begin : g_s3_reg
      always_ff @(posedge clk) begin
        s3_m    <= mont_c;
        s3_barr <= barr_c;
        s3_a    <= s2_a;
        s3_op   <= s2_op;
      end
    end else begin : g_s3_wire
      always_comb begin
        s3_m    = mont_c;
        s3_barr = barr_c;
        s3_a    = s2_a;
        s3_op   = s2_op;
      end
    end
  endgenerate

  always_ff @(posedge clk) begin
    s4_y <= y_c;
  end

  // The result mux the two latencies cost.  `s3_is_kmm` is a REGISTERED opcode
  // bit, not a decode of anything live, so this is a 32-bit 2:1 mux on an
  // already-muxed output and nothing more.
  assign result = s3_is_kmm ? kmm_early : s4_y;

  // ==========================================================================
  // Sequencing -- fixed latency, so a shift register, not a counter.
  // ==========================================================================
  // Reset reaches ONLY this, never the datapath.  DSP48E1's internal A/B/M/P
  // registers have a synchronous reset only, and an asynchronous one forces
  // Vivado to build them in fabric instead of packing them into the DSP --
  // rvntt_muldiv records the same thing for the same reason.  The datapath's
  // contents are meaningless until `done`, and `done` is reset.
  logic [STAGES-1:0] busy_q;
  always_ff @(posedge clk or negedge rst_n) begin
    // `done` clears it as well as `!req`, so a held-high `req` across two
    // back-to-back instructions starts the second one instead of reporting the
    // first one's `done` forever.  rvntt_muldiv's `active_q` does the same
    // thing with a counter; this unit has no variable latency, so a shift
    // register is the whole sequencer.
    if (!rst_n) busy_q <= '0;
    else if (!req || done) busy_q <= '0;         // flushed or finished
    else busy_q <= {busy_q[STAGES-2:0], 1'b1};
  end

  // OCCUPANCY DEPENDS ON THE OPCODE AND ON NOTHING ELSE -- not on the operands,
  // not on the zeta, not on any comparison of a value.  That is the property
  // A30's `Zkt` argument turns on, stated here because this is where it is
  // created; `kmm`, `kbfct` and `kbfgs` are not on the ratified `Zkt` list, but
  // a Tier-1 unit whose timing varied with data would make the whole claim
  // harder to hold and there is no reason to build one.
  assign done = req && (s3_is_kmm ? busy_q[STAGES-2] : busy_q[STAGES-1]);

endmodule

`default_nettype wire
