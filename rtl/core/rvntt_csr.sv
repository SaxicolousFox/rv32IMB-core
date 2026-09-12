// ============================================================================
// rvntt_csr -- the machine-mode CSR file, counters and trap state.
//
// Implemented: mstatus (MIE/MPIE; MPP fixed at 11), misa, mie, mtvec (direct
// mode only), mscratch, mepc, mcause, mtval, mip (reads zero), mcycle/h,
// minstret/h and their user shadows, mcountinhibit, mhpmcounter3..8 with
// mhpmevent3..8 (9..31 decoded, read zero, non-trapping), mvendorid/marchid/
// mimpid/mhartid (zero), and Zkr's `seed`.  satp, pmp*, medeleg, mideleg and
// mnstatus are absent and trap as illegal, which is the case riscv-tests'
// p-environment is written for.
//
// minstret is counted in EX, not WB: a CSR read in EX must see every
// instruction before it, and counting at WB leaves two uncounted.  A write to
// minstret (either half) suppresses the writing instruction's own increment.
// mcycle is a real cycle counter and does not agree with Spike's.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_csr (
    input  wire         clk,
    input  wire         rst_n,

    // ---- access from EX -----------------------------------------------
    input  wire  [11:0] addr,
    input  wire         wen,          // this instruction writes the CSR
    input  wire  [31:0] wdata,
    output logic [31:0] rdata,
    output logic        illegal,      // no such CSR, or a write to a read-only one

    // ---- retirement accounting ----------------------------------------
    // Asserted for each instruction that passes EX without trapping.
    input  wire         instret_bump,

    // ---- Zihpm event bus ----------------------------------------------
    // One bit per countable event (rv32i_pkg::HPM_EV_* minus one), driven by
    // rvntt_core; nothing here knows what the events mean.
    input  wire  [5:0]  hpm_event,

    // ---- Zkr's entropy source -----------------------------------------
    // The state machine lives in rvntt_seed.sv; this file owns only the
    // access rule, because only it sees `wen`.
    input  wire  [31:0] seed_rdata,
    output wire         seed_rd_en,

    // ---- trap entry and return ----------------------------------------
    input  wire         trap_en,
    // trap_pc[1:0] is dropped: IALIGN is 32, so mepc[1:0] read as zero.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  [31:0] trap_pc,      // mepc: the address of the faulting insn
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire  [4:0]  trap_cause,
    input  wire  [31:0] trap_val,     // mtval
    input  wire         mret_en,

    output logic [31:0] mtvec_o,
    output logic [31:0] mepc_o
);

  // ---------------------------------------------------------------- numbers
  // Zkr's `seed`: machine-mode, and read-write-only (see below).
  localparam logic [11:0] CSR_SEED      = 12'h015;

  localparam logic [11:0] CSR_MSTATUS   = 12'h300;
  localparam logic [11:0] CSR_MISA      = 12'h301;
  localparam logic [11:0] CSR_MIE       = 12'h304;
  localparam logic [11:0] CSR_MTVEC     = 12'h305;
  localparam logic [11:0] CSR_MSCRATCH  = 12'h340;
  localparam logic [11:0] CSR_MEPC      = 12'h341;
  localparam logic [11:0] CSR_MCAUSE    = 12'h342;
  localparam logic [11:0] CSR_MTVAL     = 12'h343;
  localparam logic [11:0] CSR_MIP       = 12'h344;

  localparam logic [11:0] CSR_MCYCLE    = 12'hB00;
  localparam logic [11:0] CSR_MINSTRET  = 12'hB02;
  localparam logic [11:0] CSR_MCYCLEH   = 12'hB80;
  localparam logic [11:0] CSR_MINSTRETH = 12'hB82;

  // Zicntr's user-mode read-only shadows (addr[11:10] == 11, so the generic
  // read-only rule covers them).
  localparam logic [11:0] CSR_CYCLE     = 12'hC00;
  localparam logic [11:0] CSR_INSTRET   = 12'hC02;
  localparam logic [11:0] CSR_CYCLEH    = 12'hC80;
  localparam logic [11:0] CSR_INSTRETH  = 12'hC82;

  // ---- Zihpm --------------------------------------------------------------
  // mcountinhibit at 0x320; mhpmevent3..31 at 0x323..0x33F; mhpmcounter3..31
  // at 0xB03..0xB1F (high halves 0xB83..0xB9F); user shadows 0xC03..0xC1F /
  // 0xC83..0xC9F.  Six counters are implemented; 9..31 read zero without
  // trapping, as the spec requires of unimplemented counters.
  localparam logic [11:0] CSR_MCOUNTINHIBIT = 12'h320;
  localparam int          HPM_FIRST = 3;    // mhpmcounter3
  localparam int          HPM_N     = 6;    // ... through mhpmcounter8
  localparam int          HPM_LAST  = HPM_FIRST + HPM_N - 1;

  localparam logic [11:0] CSR_MVENDORID = 12'hF11;
  localparam logic [11:0] CSR_MARCHID   = 12'hF12;
  localparam logic [11:0] CSR_MIMPID    = 12'hF13;
  localparam logic [11:0] CSR_MHARTID   = 12'hF14;

  // MXL = 1 (RV32), extension bits I and M.
  //
  // misa.B (bit 1) is deliberately not set although B is implemented:
  // riscv-config 3.18.3 cannot express the B letter and derives misa from the
  // single-letter extensions only, so setting it invalidates the RISCOF
  // config.  arch-test selects the B suites by regex on the ISA string, so
  // nothing is lost.  This constant and tb/riscof/rvntt/rvntt_isa.yaml's
  // reset-val must move together (tb/unit/test_isa_consistency.py checks).
  localparam logic [31:0] MISA_VALUE = 32'h4000_1100;

  // ---------------------------------------------------------------- state
  logic        mstatus_mie_q, mstatus_mpie_q;
  logic [31:2] mtvec_base_q;
  logic [31:0] mscratch_q;
  logic [31:2] mepc_q;
  logic [31:0] mcause_q;
  logic [31:0] mtval_q;
  logic [31:0] mie_q;
  logic [63:0] mcycle_q;
  logic [63:0] minstret_q;

  // mcountinhibit's bit 1 (TM) is read-only zero: there is no mtime.
  logic [63:0] mhpmcounter_q [HPM_N];
  logic [3:0]  mhpmevent_q   [HPM_N];
  logic        inhibit_cy_q, inhibit_ir_q;
  logic [HPM_N-1:0] inhibit_hpm_q;

  // One-hot event mask per counter, registered (a measured timing fix: the
  // event mux sat after ex_redirect).  A function of mhpmevent_q and
  // inhibit_hpm_q only; written on the same edge as they are, so behaviour is
  // identical to the 6:1 mux it replaces.
  logic [rv32i_pkg::HPM_EV_COUNT-1:0] hpm_watch_q [HPM_N];

  // MPP is read-only 11: with only M-mode there is no other legal value.
  wire [31:0] mstatus_rd = {19'b0, 2'b11, 3'b0, mstatus_mpie_q, 3'b0,
                            mstatus_mie_q, 3'b0};

  // mtvec MODE is hardwired to direct; there are no interrupt sources.
  assign mtvec_o = {mtvec_base_q, 2'b00};

  // IALIGN is 32; masking on the way in makes a read-back after `csrw mepc`
  // return the masked value.
  assign mepc_o = {mepc_q, 2'b00};

  // ---- Zihpm address decode -------------------------------------------------
  // The counter index lives in addr[4:0] for all four counter address ranges.
  wire [4:0] hpm_idx   = addr[4:0];
  wire       hpm_impl  = (hpm_idx >= HPM_FIRST[4:0]) && (hpm_idx <= HPM_LAST[4:0]);
  // Only meaningful when hpm_impl; guarded at every use.
  wire [2:0] hpm_sel   = hpm_idx[2:0] - HPM_FIRST[2:0];

  wire hpm_mlo = (addr >= 12'hB03) && (addr <= 12'hB1F);   // mhpmcounter3..31
  wire hpm_mhi = (addr >= 12'hB83) && (addr <= 12'hB9F);   // mhpmcounter3..31h
  wire hpm_ulo = (addr >= 12'hC03) && (addr <= 12'hC1F);   // hpmcounter3..31
  wire hpm_uhi = (addr >= 12'hC83) && (addr <= 12'hC9F);   // hpmcounter3..31h
  wire hpm_evt = (addr >= 12'h323) && (addr <= 12'h33F);   // mhpmevent3..31
  wire hpm_any = hpm_mlo || hpm_mhi || hpm_ulo || hpm_uhi || hpm_evt;

  // The mask a counter watches, from its selector and its inhibit bit.
  function automatic logic [rv32i_pkg::HPM_EV_COUNT-1:0]
      hpm_mask(input logic [3:0] ev, input logic inhibit);
    if (inhibit || ev == rv32i_pkg::HPM_EV_NONE || ev > rv32i_pkg::HPM_EV_MAX)
      hpm_mask = '0;
    else
      hpm_mask = rv32i_pkg::HPM_EV_COUNT'(1) << (ev - 4'd1);
  endfunction

  wire [31:0] mcountinhibit_rd =
      {{(32-HPM_N-3){1'b0}}, inhibit_hpm_q, inhibit_ir_q, 1'b0, inhibit_cy_q};

  // ---------------------------------------------------------------- read
  logic known;

  always_comb begin
    rdata = 32'h0;
    known = 1'b1;
    unique case (addr)
      CSR_MSTATUS:   rdata = mstatus_rd;
      CSR_MISA:      rdata = MISA_VALUE;
      CSR_MIE:       rdata = mie_q;
      CSR_MTVEC:     rdata = mtvec_o;
      CSR_MSCRATCH:  rdata = mscratch_q;
      CSR_MEPC:      rdata = mepc_o;
      CSR_MCAUSE:    rdata = mcause_q;
      CSR_MTVAL:     rdata = mtval_q;
      CSR_MIP:       rdata = 32'h0;      // no interrupt sources exist
      CSR_MCYCLE,    CSR_CYCLE:     rdata = mcycle_q[31:0];
      CSR_MCYCLEH,   CSR_CYCLEH:    rdata = mcycle_q[63:32];
      CSR_MINSTRET,  CSR_INSTRET:   rdata = minstret_q[31:0];
      CSR_MINSTRETH, CSR_INSTRETH:  rdata = minstret_q[63:32];
      CSR_MVENDORID, CSR_MARCHID, CSR_MIMPID, CSR_MHARTID: rdata = 32'h0;
      CSR_MCOUNTINHIBIT: rdata = mcountinhibit_rd;
      CSR_SEED:      rdata = seed_rdata;
      default: begin
        rdata = 32'h0;
        // Decoded but zero for the unimplemented indices 9..31.
        known = hpm_any;
        if (hpm_impl) begin
          if      (hpm_mlo || hpm_ulo) rdata = mhpmcounter_q[hpm_sel][31:0];
          else if (hpm_mhi || hpm_uhi) rdata = mhpmcounter_q[hpm_sel][63:32];
          else if (hpm_evt)            rdata = {28'b0, mhpmevent_q[hpm_sel]};
        end
      end
    endcase
  end

  // addr[11:10] == 11 marks a read-only CSR; writing one is illegal.
  wire read_only = (addr[11:10] == 2'b11);

  // ---- `seed` is read-write-only ----------------------------------------
  // Reading `seed` destroys entropy, so the architecture requires every access
  // to be a write (`csrrs rd, seed, x0` traps).  `wen` is exactly "this
  // instruction writes the CSR".
  wire seed_access  = (addr == CSR_SEED);
  wire seed_illegal = seed_access && !wen;

  assign illegal = !known || (wen && read_only) || seed_illegal;

  // The consuming read, gated on the access being legal so a trapping access
  // does not eat a seed.
  assign seed_rd_en = seed_access && wen;

  // ---------------------------------------------------------------- write
  // No trap gate: it would form the loop illegal -> trap -> write-enable ->
  // illegal, and both illegal conditions already force do_write low.
  wire do_write = wen && known && !read_only;

  // Either half counts as a write to the counter.
  wire minstret_written = do_write &&
                          (addr == CSR_MINSTRET || addr == CSR_MINSTRETH);

  // Same rule for an HPM counter.  hpm_ulo/hpm_uhi are read-only.
  wire hpm_written = do_write && hpm_impl && (hpm_mlo || hpm_mhi);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mstatus_mie_q  <= 1'b0;
      mstatus_mpie_q <= 1'b0;
      mtvec_base_q   <= 30'b0;
      mscratch_q     <= 32'h0;
      mepc_q         <= 30'b0;
      mcause_q       <= 32'h0;
      mtval_q        <= 32'h0;
      mie_q          <= 32'h0;
      mcycle_q       <= 64'h0;
      minstret_q     <= 64'h0;
      inhibit_cy_q   <= 1'b0;
      inhibit_ir_q   <= 1'b0;
      inhibit_hpm_q  <= '0;
      for (int i = 0; i < HPM_N; i++) begin
        mhpmcounter_q[i] <= 64'h0;
        mhpmevent_q[i]   <= rv32i_pkg::HPM_EV_NONE;
        hpm_watch_q[i]   <= '0;
      end
    end else begin
      if (!inhibit_cy_q) mcycle_q <= mcycle_q + 64'd1;
      if (instret_bump && !minstret_written && !inhibit_ir_q)
        minstret_q <= minstret_q + 64'd1;

      // One-hot AND-OR of registered masks: a single reduction between the
      // event bus and the counter's clock enable.
      for (int i = 0; i < HPM_N; i++) begin
        if (|(hpm_watch_q[i] & hpm_event) &&
            !(hpm_written && hpm_sel == i[2:0]))
          mhpmcounter_q[i] <= mhpmcounter_q[i] + 64'd1;
      end

      // Trap entry and MRET come first; an explicit write in the same cycle
      // cannot happen, because the trapping instruction's own write is
      // suppressed by the trap.
      if (trap_en) begin
        mepc_q         <= trap_pc[31:2];
        mcause_q       <= {27'b0, trap_cause};
        mtval_q        <= trap_val;
        mstatus_mpie_q <= mstatus_mie_q;
        mstatus_mie_q  <= 1'b0;
      end else if (mret_en) begin
        mstatus_mie_q  <= mstatus_mpie_q;
        mstatus_mpie_q <= 1'b1;
      end else if (do_write) begin
        unique case (addr)
          CSR_MSTATUS: begin
            mstatus_mie_q  <= wdata[3];
            mstatus_mpie_q <= wdata[7];
          end
          CSR_MIE:      mie_q        <= wdata;
          CSR_MTVEC:    mtvec_base_q <= wdata[31:2];
          CSR_MSCRATCH: mscratch_q   <= wdata;
          CSR_MEPC:     mepc_q       <= wdata[31:2];
          CSR_MCAUSE:   mcause_q     <= wdata;
          CSR_MTVAL:    mtval_q      <= wdata;
          CSR_MCYCLE:   mcycle_q[31:0]    <= wdata;
          CSR_MCYCLEH:  mcycle_q[63:32]   <= wdata;
          // Partial assignments: writing one half must not disturb the other.
          CSR_MINSTRET:  minstret_q[31:0]  <= wdata;
          CSR_MINSTRETH: minstret_q[63:32] <= wdata;
          CSR_MCOUNTINHIBIT: begin
            inhibit_cy_q  <= wdata[0];
            // wdata[1] is TM and is dropped: there is no mtime to inhibit.
            inhibit_ir_q  <= wdata[2];
            inhibit_hpm_q <= wdata[HPM_LAST:HPM_FIRST];
            for (int i = 0; i < HPM_N; i++)
              hpm_watch_q[i] <= hpm_mask(mhpmevent_q[i],
                                         wdata[HPM_FIRST + i]);
          end
          // MISA, MIP and the read-only group are WARL at their only value;
          // the HPM ranges land here too.
          default: begin
            if (hpm_impl) begin
              if      (hpm_mlo) mhpmcounter_q[hpm_sel][31:0]  <= wdata;
              else if (hpm_mhi) mhpmcounter_q[hpm_sel][63:32] <= wdata;
              // The selector is WARL over 0..HPM_EV_MAX; an out-of-range
              // write leaves it unchanged.
              else if (hpm_evt && wdata[3:0] <= rv32i_pkg::HPM_EV_MAX && wdata[31:4] == '0) begin
                mhpmevent_q[hpm_sel] <= wdata[3:0];
                hpm_watch_q[hpm_sel] <= hpm_mask(wdata[3:0],
                                                 inhibit_hpm_q[hpm_sel]);
              end
            end
          end
        endcase
      end
    end
  end

`ifdef FORMAL
  // Access-rule properties (storage is covered by riscv-tests).  Nothing here
  // reuses the module's own `known` / `read_only` wires.
  wire f_is_read_only = (addr[11] & addr[10]);

  always_comb begin
    // `seed` is read-write-only, rebuilt from the raw inputs.
    if ((addr == 12'h015) && !wen)
      a_seed_read_only_traps: assert (illegal);

    // ...and the consuming read fires only for a legal read-write access.
    if (seed_rd_en) begin
      a_seed_consume_is_a_write: assert (wen);
      a_seed_consume_is_seed:    assert (addr == 12'h015);
    end
    if ((addr != 12'h015) || !wen)
      a_seed_no_stray_consume: assert (!seed_rd_en);

    // 1. Every read-only address rejects a write.
    a_ro_write_illegal: assert (!(wen && f_is_read_only) || illegal);

    // 2. An illegal access never changes state.
    a_illegal_no_write: assert (!illegal || !do_write);

    // 3. A read-only CSR is never written.
    a_ro_never_written: assert (!f_is_read_only || !do_write);

    // 4. mepc and mtvec are always 4-byte aligned on the way out.
    a_mepc_aligned:  assert (mepc_o[1:0]  == 2'b00);
    a_mtvec_aligned: assert (mtvec_o[1:0] == 2'b00);

    // 5. MPP reads as 11 unconditionally.
    a_mpp_fixed: assert (mstatus_rd[12:11] == 2'b11);
  end

  // Sequential properties.  An assertion inside always_ff sees the values of
  // the cycle that is ending; $past is unconstrained at the first step, hence
  // f_past_valid.
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  always_ff @(posedge clk) begin
    if (f_past_valid && rst_n && $past(rst_n)) begin
      // A trap disables interrupts and saves the previous enable.
      if ($past(trap_en)) begin
        a_trap_disables:  assert (!mstatus_mie_q);
        a_trap_saves_mie: assert (mstatus_mpie_q == $past(mstatus_mie_q));
      end
      // A write to minstret lands and suppresses the writer's own increment.
      if ($past(do_write) && $past(addr) == CSR_MINSTRET &&
          !$past(trap_en) && !$past(mret_en)) begin
        a_minstret_write_lands: assert (minstret_q[31:0] == $past(wdata));
      end
      if ($past(do_write) && $past(addr) == CSR_MINSTRETH &&
          !$past(trap_en) && !$past(mret_en)) begin
        a_minstreth_write_lands: assert (minstret_q[63:32] == $past(wdata));
        a_minstreth_keeps_low: assert (minstret_q[31:0] == $past(minstret_q[31:0]));
      end

      // MRET is the exact inverse of trap entry.
      if ($past(mret_en) && !$past(trap_en)) begin
        a_mret_restores: assert (mstatus_mie_q == $past(mstatus_mpie_q));
        a_mret_sets_mpie: assert (mstatus_mpie_q);
      end
    end
  end
`endif

endmodule

`default_nettype wire
