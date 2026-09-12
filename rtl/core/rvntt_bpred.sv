// ============================================================================
// rvntt_bpred -- branch predictor: a direct-mapped BTB whose entries carry a
// 2-bit counter and a transfer kind, plus a return address stack.
// model/bpred.py implements the same design independently and
// tb/cosim/cycle_model.py predicts cycle spans from it.
//
// The lookup runs one address ahead: `lookup_pc` is the address that will be
// fetched next cycle, and the prediction is registered, so the core's PC mux
// sees registered pred_taken/pred_target and the array read and tag compare
// end at the prediction register.
//
// The counter lives inside the BTB entry (no separate pattern-history table
// and no reset sweep), so the only state needing a reset is the valid bits;
// entries and the RAS are read only when valid/non-empty.
// ============================================================================
`default_nettype none

module rvntt_bpred #(
    parameter int BTB_ENTRIES = 256,
    parameter int RAS_ENTRIES = 8
) (
    input  wire         clk,
    input  wire         rst_n,

    // ---- lookup.  `lookup_pc` is the core's next fetch address; the outputs
    // ---- describe it and are valid during the cycle it is fetched.
    // Word-aligned by construction, so the bottom two bits index nothing.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  [31:0] lookup_pc,
    /* verilator lint_on UNUSEDSIGNAL */
    // High when EX redirects: the next fetch is the redirect target, not
    // lookup_pc, so the prediction is suppressed rather than allowed to
    // describe the wrong address.
    input  wire         flush,
    // Hold the registered answer instead of re-looking-up (the front-end
    // stall is a function of the decoded instruction and would drag the
    // decoder in front of the BTB).  An identity, because every piece of
    // state here is written under `upd_valid` alone and rvntt_core proves
    // upd_valid is false during a front stall.  `flush` outranks `hold`.
    input  wire         hold,
    output wire         pred_taken,
    output wire  [31:0] pred_target,
    // Did the lookup find an entry, regardless of the counter?  Observational.
    output wire         pred_hit,

    // ---- update, from a control transfer resolving in EX.  Nothing else
    // ---- writes either structure.
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
  // and two asynchronous reads (the update is a read-modify-write of the
  // counter at a different index from the lookup).
  logic [ENT_W-1:0]       btb_q [0:BTB_ENTRIES-1];
  // The only state that needs clearing.
  logic [BTB_ENTRIES-1:0] btb_valid_q;

  wire [IDX_W-1:0] lk_i = lookup_pc[2+IDX_W-1:2];
  wire [TAG_W-1:0] lk_t = lookup_pc[31:2+IDX_W];
  wire [IDX_W-1:0] up_i = upd_pc[2+IDX_W-1:2];
  wire [TAG_W-1:0] up_t = upd_pc[31:2+IDX_W];

  wire [ENT_W-1:0] lk_e = btb_q[lk_i];
  // The update reads the entry only for its counter, target and tag.
  /* verilator lint_off UNUSEDSIGNAL */
  wire [ENT_W-1:0] up_e = btb_q[up_i];
  /* verilator lint_on UNUSEDSIGNAL */

  // ---- update ---------------------------------------------------------------
  wire        up_hit  = btb_valid_q[up_i] && (up_e[G_LSB +: TAG_W] == up_t);
  wire [1:0]  up_cnt  = up_e[C_LSB +: 2];
  wire        up_isbr = (upd_kind == rv32i_pkg::BP_BRANCH);

  // A fresh entry starts weakly taken.
  wire [1:0] cnt_taken = (up_hit && up_isbr)
                         ? ((up_cnt == 2'b11) ? 2'b11 : up_cnt + 2'b01)
                         : 2'b10;
  wire [1:0] cnt_ntaken = (up_cnt == 2'b00) ? 2'b00 : up_cnt - 2'b01;

  // Written on a taken resolution (which allocates) and on a not-taken branch
  // that already has an entry (which only moves the counter).
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

  // ---- prediction, registered ----------------------------------------------
  wire        lk_hit  = btb_valid_q[lk_i] && (lk_e[G_LSB +: TAG_W] == lk_t);
  wire [1:0]  lk_kind = lk_e[K_LSB +: 2];
  // "taken" is cnt >= 2, so only the high bit is read.
  wire        lk_cnt1 = lk_e[C_LSB + 1];
  wire        lk_ret  = (lk_kind == rv32i_pkg::BP_RET);

  // All four kinds, no default: lk_kind is two bits.
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
  logic        pred_hit_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pred_taken_q  <= 1'b0;
      pred_target_q <= 32'h0;
      pred_hit_q    <= 1'b0;
    end else if (!hold || flush) begin
      pred_taken_q  <= taken_c && !flush;
      pred_target_q <= target_c;
      // After a redirect there was no lookup to hit.
      pred_hit_q    <= lk_hit && !flush;
    end
  end

`ifdef RVNTT_ABSTRACT_BPRED
  // For riscv-formal only: a free-running prediction proves the core is
  // architecturally invariant under every prediction any predictor could
  // make, which is a stronger claim than under this one's.  The predictor's
  // own logic is proved by tb/formal (this module) and checked by
  // model/bpred.py through the cycle model.
  (* anyseq *) logic        f_pred_taken;
  (* anyseq *) logic [31:0] f_pred_target;
  (* anyseq *) logic        f_pred_hit;
  assign pred_taken  = f_pred_taken;
  assign pred_target = f_pred_target;
  assign pred_hit    = f_pred_hit;
`else
  assign pred_taken  = pred_taken_q;
  assign pred_target = pred_target_q;
  assign pred_hit    = pred_hit_q;
`endif

`ifdef FORMAL
  // The properties are about the mechanism, so tb/formal instantiates this
  // with BTB_ENTRIES = 8 and RAS_ENTRIES = 4.
  logic f_past_valid;
  initial assume (!rst_n);
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  // The interface contract: a resolving transfer's pc and target are
  // word-aligned.  Assumed here, asserted where the module is instantiated.
  always_comb if (upd_valid) begin
    assume (upd_pc[1:0] == 2'b00);
    assume (upd_target[1:0] == 2'b00);
  end

  // Index and tag together are the word address, so a hit is the exact
  // instruction rather than an alias.
  always_comb
    a_index_and_tag_are_the_address:
      assert ({lk_t, lk_i} == lookup_pc[31:2]);

  always_ff @(posedge clk) begin
    if (f_past_valid && $past(rst_n) && rst_n) begin
      // A predicted target is word-aligned.
      if (pred_taken_q)
        a_target_aligned: assert (pred_target_q[1:0] == 2'b00);

      // Nothing is predicted taken out of an invalid entry.  With `hold`, a
      // held value's provenance is the edge that wrote it.
      if (pred_taken_q && !$past(hold))
        a_taken_needs_a_hit: assert ($past(lk_hit));

      if ($past(hold) && !$past(flush))
        a_hold_freezes: assert (pred_taken_q  == $past(pred_taken_q) &&
                                pred_target_q == $past(pred_target_q) &&
                                pred_hit_q    == $past(pred_hit_q));

      // The RAS occupancy never exceeds the array.
      a_ras_count_bounded: assert (ras_cnt_q <= RAS_ENTRIES[RAS_W:0]);

      // The counter saturates; it does not wrap.
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
