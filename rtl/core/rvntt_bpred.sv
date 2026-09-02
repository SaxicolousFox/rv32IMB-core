// ============================================================================
// rvntt_bpred -- the branch predictor of docs/a19-bpred-spec.md (plan A19).
//
// THE SPECIFICATION IS THE AUTHORITY, NOT THIS FILE.  model/bpred.py implements
// the same document independently and tb/cosim/cycle_model.py predicts cycle
// spans from it; where the two disagree, docs/a19-bpred-spec.md decides which
// is wrong.  That is the whole reason the specification was written first, and
// it is why the section numbers below are references rather than decoration.
//
// TIMING IS THE ONE THING THAT CANNOT BE READ OFF THE SPECIFICATION'S
// PSEUDOCODE, so it is stated here.  The lookup runs ONE ADDRESS AHEAD:
// `lookup_pc` is the core's `pc_next`, and the prediction registered from it
// describes the address `pc_q` will hold NEXT cycle.  So the core's PC mux sees
// a registered pred_taken / pred_target and needs only a 2:1 mux, while the
// array read and the tag compare end at the prediction register instead of at
// the PC.  That is spec section 2, and MODS_A A19 calls it "the one real timing
// risk in this document" -- done the naive way it is a new critical path in IF
// on a design A17 has just spent six implementation runs giving margin to.
//
// WHY THERE IS NO PATTERN-HISTORY TABLE AND NO RESET SWEEP.  The two-bit
// counter lives inside the BTB entry.  An earlier draft had a separate
// 256-entry PHT and cleared both arrays with a 256-cycle sweep after reset,
// because distributed RAM cannot be reset -- and that sweep would have been a
// correctness problem twice over: model/bpred.py would have needed to count
// cycles since reset to know whether the predictor was awake, and riscv-formal
// at BMC depth 14 would have proved 43 checks over a predictor that had not yet
// done anything.  Folding the counter costs 0.3% on Dhrystone and nothing
// anywhere else (spec section 10) and leaves ONE thing needing a reset: the
// valid bits.  Everything else in the entry is read only when its valid bit is
// set, and the RAS is read only when its count is non-zero, so no uninitialised
// memory is ever read.
// ============================================================================
`default_nettype none

module rvntt_bpred #(
    parameter int BTB_ENTRIES = 256,
    parameter int RAS_ENTRIES = 8
) (
    input  wire         clk,
    input  wire         rst_n,

    // ---- lookup.  `lookup_pc` is the core's pc_next; the outputs describe it
    // ---- and are valid during the cycle in which it is the fetch address.
    // Word-aligned by construction -- it is a fetch address -- so the bottom
    // two bits index nothing and gate nothing.  The waiver is scoped to the
    // port rather than to the module.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  [31:0] lookup_pc,
    /* verilator lint_on UNUSEDSIGNAL */
    // High when EX redirects.  The address that will be fetched next is the
    // redirect target, and `lookup_pc` is not it -- see rvntt_core.sv, where
    // the lookup deliberately reads only registered sources so the ALU stays
    // out of this module's cone.  The prediction is suppressed rather than
    // allowed to describe the wrong address.
    input  wire         flush,
    output wire         pred_taken,
    output wire  [31:0] pred_target,

    // ---- update, from a control transfer RESOLVING in EX.  Nothing else in
    // ---- the design writes either structure -- spec section 4, and section 8
    // ---- for what that costs and what it buys.
    input  wire         upd_valid,
    input  wire  [31:0] upd_pc,
    input  wire  [1:0]  upd_kind,
    input  wire         upd_taken,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  [31:0] upd_target      // stored as [31:2]; the target of a
    /* verilator lint_on UNUSEDSIGNAL */ // control transfer is word-aligned or
);                                       // it traps before it gets here
  localparam int IDX_W = $clog2(BTB_ENTRIES);
  localparam int TAG_W = 32 - 2 - IDX_W;
  localparam int RAS_W = $clog2(RAS_ENTRIES);

  // Entry layout, from bit 0 up: target[31:2], cnt, kind, tag.
  localparam int T_LSB = 0;
  localparam int C_LSB = 30;
  localparam int K_LSB = 32;
  localparam int G_LSB = 34;
  localparam int ENT_W = G_LSB + TAG_W;

  // No reset, so this infers dual-port distributed RAM: one synchronous write
  // and two asynchronous reads.  The second read port is not a luxury -- the
  // update is a read-modify-write of the counter, and it has to see the entry
  // at the UPDATE index while the lookup is reading a different one.
  logic [ENT_W-1:0]       btb_q [0:BTB_ENTRIES-1];
  // ...and this is the only state that needs clearing.  Flip-flops, because
  // that is what a reset means.
  logic [BTB_ENTRIES-1:0] btb_valid_q;

  wire [IDX_W-1:0] lk_i = lookup_pc[2+IDX_W-1:2];
  wire [TAG_W-1:0] lk_t = lookup_pc[31:2+IDX_W];
  wire [IDX_W-1:0] up_i = upd_pc[2+IDX_W-1:2];
  wire [TAG_W-1:0] up_t = upd_pc[31:2+IDX_W];

  wire [ENT_W-1:0] lk_e = btb_q[lk_i];
  // The update reads the entry only for its counter, its target and its tag:
  // the KIND it writes is the one EX just computed, not the one that is there.
  /* verilator lint_off UNUSEDSIGNAL */
  wire [ENT_W-1:0] up_e = btb_q[up_i];
  /* verilator lint_on UNUSEDSIGNAL */

  // ---- update -- spec section 4 -------------------------------------------
  wire        up_hit  = btb_valid_q[up_i] && (up_e[G_LSB +: TAG_W] == up_t);
  wire [1:0]  up_cnt  = up_e[C_LSB +: 2];
  wire        up_isbr = (upd_kind == rv32i_pkg::BP_BRANCH);

  // A fresh entry starts WEAKLY TAKEN: it is being allocated by an execution
  // that was just observed taken, and that is the value a separate
  // weakly-not-taken counter would have reached on the same event.
  wire [1:0] cnt_taken = (up_hit && up_isbr)
                         ? ((up_cnt == 2'b11) ? 2'b11 : up_cnt + 2'b01)
                         : 2'b10;
  wire [1:0] cnt_ntaken = (up_cnt == 2'b00) ? 2'b00 : up_cnt - 2'b01;

  // Written on a taken resolution -- which allocates -- and on a NOT-taken
  // branch that already has an entry, which only moves the counter.  A branch
  // that has never been taken is never written at all, which is what keeps it
  // as free as it is today.
  wire btb_we = upd_valid && (upd_taken || (up_hit && up_isbr));

  wire [ENT_W-1:0] btb_wdata =
      upd_taken ? {up_t, upd_kind, cnt_taken,  upd_target[31:2]}
                : {up_t, upd_kind, cnt_ntaken, up_e[T_LSB +: 30]};

  always_ff @(posedge clk) begin
    if (btb_we) btb_q[up_i] <= btb_wdata;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)                      btb_valid_q        <= '0;
    else if (upd_valid && upd_taken) btb_valid_q[up_i]  <= 1'b1;
  end

  // ---- return address stack ------------------------------------------------
  logic [31:0]      ras_q [0:RAS_ENTRIES-1];
  logic [RAS_W-1:0] ras_sp_q;
  logic [RAS_W:0]   ras_cnt_q;

  wire up_call = upd_valid && (upd_kind == rv32i_pkg::BP_CALL);
  wire up_ret  = upd_valid && (upd_kind == rv32i_pkg::BP_RET);

  always_ff @(posedge clk) begin
    if (up_call) ras_q[ras_sp_q] <= upd_pc + 32'd4;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ras_sp_q  <= '0;
      ras_cnt_q <= '0;
    end else if (up_call) begin
      ras_sp_q  <= ras_sp_q + 1'b1;
      if (ras_cnt_q != RAS_ENTRIES[RAS_W:0]) ras_cnt_q <= ras_cnt_q + 1'b1;
    end else if (up_ret) begin
      ras_sp_q  <= ras_sp_q - 1'b1;
      if (ras_cnt_q != '0) ras_cnt_q <= ras_cnt_q - 1'b1;
    end
  end

  wire [31:0] ras_top = ras_q[ras_sp_q - 1'b1];

  // ---- prediction, registered -- spec section 3 ----------------------------
  wire        lk_hit  = btb_valid_q[lk_i] && (lk_e[G_LSB +: TAG_W] == lk_t);
  wire [1:0]  lk_kind = lk_e[K_LSB +: 2];
  // Only the counter's high bit decides -- "taken" is `cnt >= 2` -- so only the
  // high bit is read.
  wire        lk_cnt1 = lk_e[C_LSB + 1];
  wire        lk_ret  = (lk_kind == rv32i_pkg::BP_RET);

  // Written as a case over all four kinds rather than a nest of ternaries,
  // because that is the shape of the specification's own table (section 1) and
  // the two should be diffable by eye.  `unique` with all four arms present and
  // no default: lk_kind is two bits, so a default would be unreachable code in
  // the one place where unreachable code is indistinguishable from a bug.
  logic taken_c;
  always_comb begin
    unique case (lk_kind)
      rv32i_pkg::BP_BRANCH: taken_c = lk_hit && lk_cnt1;
      rv32i_pkg::BP_RET:    taken_c = lk_hit && (ras_cnt_q != '0);
      rv32i_pkg::BP_JUMP:   taken_c = lk_hit;
      rv32i_pkg::BP_CALL:   taken_c = lk_hit;
    endcase
  end

  wire [31:0] target_c = lk_ret ? ras_top : {lk_e[T_LSB +: 30], 2'b00};

  logic        pred_taken_q;
  logic [31:0] pred_target_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pred_taken_q  <= 1'b0;
      pred_target_q <= 32'h0;
    end else begin
      pred_taken_q  <= taken_c && !flush;
      pred_target_q <= target_c;
    end
  end

`ifdef RVNTT_ABSTRACT_BPRED
  // ==========================================================================
  // FOR riscv-formal ONLY, and it makes that proof STRONGER rather than weaker.
  //
  // The claim A19 has to defend is that the predictor is architecturally
  // invisible -- it changes when instructions are fetched, never which ones
  // retire.  Proving that against THIS predictor proves it for the predictions
  // this predictor happens to make.  Proving it against a free-running pair of
  // signals proves it for every prediction any predictor could ever make,
  // including a misaligned target, a prediction of taken on an `addi`, and a
  // different wrong answer every cycle.  That is the property, stated exactly.
  //
  // What is NOT proved this way: anything about the predictor's own logic.
  // That is a performance question, and it is answered by
  // tb/formal/rvntt_bpred.sby on this module, by model/bpred.py through
  // tb/cosim/cycle_model.py's span check, and by the A19 mutations.  The
  // boundary is recorded here rather than left to be inferred -- the same
  // discipline A15 applied to RVNTT_ABSTRACT_MULDIV.
  // ==========================================================================
  (* anyseq *) logic        f_pred_taken;
  (* anyseq *) logic [31:0] f_pred_target;
  assign pred_taken  = f_pred_taken;
  assign pred_target = f_pred_target;
`else
  assign pred_taken  = pred_taken_q;
  assign pred_target = pred_target_q;
`endif

`ifdef FORMAL
  // ==========================================================================
  // The properties are about the MECHANISM, not the sizes, so tb/formal's run
  // instantiates this module with BTB_ENTRIES = 8 and RAS_ENTRIES = 4.  A
  // 256-entry array makes the solver carry 256 x 56 bits of state for
  // properties that say nothing about any particular entry.
  // ==========================================================================
  logic f_past_valid;
  initial assume (!rst_n);
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  // THE INTERFACE CONTRACT, and it was found by this proof rather than written
  // down first.  a_target_aligned failed at step 5 over a CALL resolving at a
  // misaligned upd_pc, which put a misaligned return address on the RAS and
  // predicted a misaligned target from it.  The core cannot do that -- a pc is
  // aligned or the fetch trapped, and a target is aligned or the misaligned-
  // target trap fired and gated upd_valid off -- but "the core cannot do that"
  // is exactly the kind of claim that belongs on both sides of a module
  // boundary.  It is ASSUMED here and ASSERTED where the module is
  // instantiated, so neither side is taking the other on trust.
  always_comb if (upd_valid) begin
    assume (upd_pc[1:0] == 2'b00);
    assume (upd_target[1:0] == 2'b00);
  end

  // Index and tag together ARE the word address.  This is what makes a hit the
  // exact instruction rather than an alias, and it is what lets EX skip a "was
  // this really a branch?" check -- so it is asserted rather than argued.
  always_comb
    a_index_and_tag_are_the_address:
      assert ({lk_t, lk_i} == lookup_pc[31:2]);

  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n) begin
      // A target that is not word-aligned would make the core fetch a
      // misaligned instruction from a PREDICTION -- an architectural fault
      // invented by a performance structure.  Storing target[31:2] is what
      // prevents it; this says so.
      if (pred_taken_q)
        a_target_aligned: assert (pred_target_q[1:0] == 2'b00);

      // Nothing is predicted taken out of an invalid entry.  With no reset
      // sweep this is the ONLY thing standing between a cold machine and
      // whatever the distributed RAM powers up holding.
      if (pred_taken_q)
        a_taken_needs_a_hit: assert ($past(lk_hit));

      // The occupancy is a count of what is in the array.  A count that can
      // exceed it -- or wrap below zero -- makes the emptiness test lie and the
      // predictor return to an address that was never pushed.
      a_ras_count_bounded: assert (ras_cnt_q <= RAS_ENTRIES[RAS_W:0]);

      // The counter saturates; it does not wrap.  A wrap turns a strongly taken
      // branch into a strongly not-taken one on a single not-taken execution,
      // which is the classic two-bit-counter bug and is invisible to every
      // architectural check there is.
      if ($past(upd_valid) && $past(up_isbr) && $past(up_hit)) begin
        if ($past(upd_taken))
          a_counter_up:   assert ($past(cnt_taken)  >= $past(up_cnt));
        else
          a_counter_down: assert ($past(cnt_ntaken) <= $past(up_cnt));
      end
    end
  end
`endif

endmodule

`default_nettype wire
