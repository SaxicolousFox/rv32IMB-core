// ============================================================================
// rvntt_csr -- the machine-mode CSR file, counters and trap state (plan A9).
//
// Plan §1.5's subset, machine mode only: mstatus, mtvec, mepc, mcause, mie,
// mip, mcycle/mcycleh, minstret/minstreth -- plus mscratch, mtval, misa,
// mhartid and the mvendorid/marchid/mimpid triple, which §1.5 does not list but
// which riscv-tests' own p-environment reads before it will run a single test.
//
// WHAT IS DELIBERATELY ABSENT, and why that is safe.  satp, the pmp* registers,
// medeleg, mideleg and mnstatus are NOT implemented, so accessing one raises an
// illegal-instruction trap.  That is not a gap the riscv-tests environment
// trips over -- it is the case it is written for.  Each of its INIT_ macros
// points mtvec at the label immediately after itself before touching an
// optional CSR, so a trap lands on the next line and the test carries on.  An
// M-only core is expected to take that path.
//
// Two of them are load-bearing exceptions to that: `mie` is written BEFORE
// DELEGATE_NO_TRAPS re-points mtvec, so an illegal-instruction trap there would
// jump backwards into an infinite loop; and `mhartid` is read before mtvec has
// been set at all, so a trap there would vector to address 0.  Both are
// implemented.
//
// minstret IS COUNTED IN EX, NOT IN WB, and that is the single least obvious
// decision in this file.  A CSR access executes in EX and must report the
// number of instructions retired BEFORE it; counting at WB leaves its two
// immediate predecessors uncounted at that moment.  Adding them back from the
// pipeline registers works -- every trap resolves in EX, so anything past EX is
// guaranteed to retire -- and it is WRONG the first time software writes the
// counter, because the write already accounts for everything ahead of it and
// the correction then double-counts.  riscv-tests' instret_overflow says so in
// one line: `csrwi minstret, 0; csrr a0, minstret` must read 0, and the
// in-flight version reads 2.
//
// Counting in EX has neither problem.  The two counts are identical -- every
// instruction that passes EX retires -- and the value a CSR read sees is
// already complete, so there is nothing to correct.
//
// A WRITE TO minstret SUPPRESSES THAT INSTRUCTION'S OWN INCREMENT, which is
// what makes the written value the one the next instruction reads.  A write to
// minstreth counts as a write to minstret for this purpose; the spec is
// explicit and the test checks it.
//
// mcycle is a real cycle counter and therefore does NOT agree with Spike, whose
// mcycle advances once per instruction.  Nothing compares it; minstret is the
// one plan A9 makes a claim about.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
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
    // Asserted for each instruction that passes EX without trapping.  See the
    // header for why this is not the WB-stage retirement signal.
    input  wire         instret_bump,

    // ---- trap entry and return ----------------------------------------
    input  wire         trap_en,
    // trap_pc's low two bits are dropped: IALIGN is 32, so mepc[1:0] read as
    // zero and masking on the way IN is what makes a read-back after
    // `csrw mepc` return the masked value, which is what the spec requires.
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

  // Zicntr's user-mode read-only shadows.  Their addresses have bits [11:10]
  // == 11, so the generic read-only rule below rejects a write to them without
  // needing a special case.
  localparam logic [11:0] CSR_CYCLE     = 12'hC00;
  localparam logic [11:0] CSR_INSTRET   = 12'hC02;
  localparam logic [11:0] CSR_CYCLEH    = 12'hC80;
  localparam logic [11:0] CSR_INSTRETH  = 12'hC82;

  localparam logic [11:0] CSR_MVENDORID = 12'hF11;
  localparam logic [11:0] CSR_MARCHID   = 12'hF12;
  localparam logic [11:0] CSR_MIMPID    = 12'hF13;
  localparam logic [11:0] CSR_MHARTID   = 12'hF14;

  // MXL = 1 (RV32) in bits 31:30, extension bits I and -- as of A14 (MODS_A) --
  // M in bit 12.  The X bit stays CLEAR even though the decoder recognises
  // Xkntt: no stage executes it yet, and misa is a claim about what the hart
  // can RUN, not about what it can decode.  M is set for exactly the opposite
  // reason: rvntt_muldiv executes it.
  //
  // This is not cosmetic.  tb/riscof/rvntt/rvntt_isa.yaml reads misa to decide
  // which arch-test suites are selected, so this constant and that file's
  // reset value must move together or RISCOF silently tests the wrong set.
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

  // MPP is read-only 11.  With only M-mode implemented there is no other legal
  // value, and the privileged spec's own way of discovering that is to write a
  // mode to MPP and read it back -- so hardwiring it is the correct answer, not
  // a shortcut.
  wire [31:0] mstatus_rd = {19'b0, 2'b11, 3'b0, mstatus_mpie_q, 3'b0,
                            mstatus_mie_q, 3'b0};

  // mtvec MODE is hardwired to 0 (direct).  Vectored mode only changes where
  // INTERRUPTS vector, and this core has no interrupt sources; a WARL field may
  // legally reject a value it does not support.
  assign mtvec_o = {mtvec_base_q, 2'b00};

  // IALIGN is 32, so mepc[1:0] read as zero.  Masking on the way in rather
  // than on the way out means a read-back after `csrw mepc` sees the masked
  // value, which is what the spec requires and what riscv-tests checks.
  assign mepc_o = {mepc_q, 2'b00};

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
      default: begin
        rdata = 32'h0;
        known = 1'b0;
      end
    endcase
  end

  // Bits [11:10] == 11 marks a read-only CSR in the standard address map, so
  // this one rule covers every counter shadow and the mvendorid group without
  // listing them.  Writing one is an illegal instruction, not a silent no-op.
  wire read_only = (addr[11:10] == 2'b11);

  assign illegal = !known || (wen && read_only);

  // ---------------------------------------------------------------- write
  // NO TRAP GATE IS NEEDED HERE, and the reason is worth stating because the
  // obvious design has a combinational loop in it.  `illegal` feeds the core's
  // trap logic, so gating the write on "no trap" would close the loop
  // illegal -> trap -> write-enable -> illegal.  It is unnecessary: the only
  // way a Zicsr instruction can trap is by being an illegal CSR access, and
  // both of those conditions -- unknown CSR, or a write to a read-only one --
  // already force do_write low on their own.
  wire do_write = wen && known && !read_only;

  // Either half counts as a write to the counter, so both suppress the
  // increment this instruction would otherwise contribute.
  wire minstret_written = do_write &&
                          (addr == CSR_MINSTRET || addr == CSR_MINSTRETH);

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
    end else begin
      mcycle_q <= mcycle_q + 64'd1;
      if (instret_bump && !minstret_written) minstret_q <= minstret_q + 64'd1;

      // Trap entry and MRET come FIRST in this if-chain, and an explicit write
      // to the same register in the same cycle cannot happen: a trap is taken
      // by the instruction in EX, and the CSR write is that same instruction's,
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
          // These two partial assignments come AFTER the increment above and
          // therefore override it for the bits they cover -- but the increment
          // is already suppressed by minstret_written, so the untouched half
          // simply holds.  Writing the low half must not disturb the high one.
          CSR_MINSTRET:  minstret_q[31:0]  <= wdata;
          CSR_MINSTRETH: minstret_q[63:32] <= wdata;
          // MISA, MIP and the read-only group: WARL, and every value this core
          // supports is the one it already has.
          default: ;
        endcase
      end
    end
  end

`ifdef FORMAL
  // The properties here are about the ACCESS RULES rather than about storage:
  // "does a register hold what was written" is what the directed tests and
  // riscv-tests cover far better, while the rules below are the ones whose
  // failure is a silently wrong privilege decision.
  //
  // As in rvntt_forward and rvntt_hazard, nothing below reuses the module's own
  // `known` / `read_only` wires -- a property written in terms of the signal it
  // is checking cannot detect a bug in that signal.
  wire f_is_read_only = (addr[11] & addr[10]);

  always_comb begin
    // 1. Every read-only address rejects a write.  This is the whole of the
    //    "CSRRS with rs1 = x0 must not write" contract on the counter shadows:
    //    if the write-enable were computed wrongly upstream, this fires.
    a_ro_write_illegal: assert (!(wen && f_is_read_only) || illegal);

    // 2. An illegal access never changes state.
    a_illegal_no_write: assert (!illegal || !do_write);

    // 3. A read-only CSR is never written, however `wen` was derived.
    a_ro_never_written: assert (!f_is_read_only || !do_write);

    // 4. mepc and mtvec are always 4-byte aligned on the way out, whatever was
    //    written.  IALIGN is 32 here, and a misaligned mtvec would send every
    //    trap to a fetch fault.
    a_mepc_aligned:  assert (mepc_o[1:0]  == 2'b00);
    a_mtvec_aligned: assert (mtvec_o[1:0] == 2'b00);

    // 5. MPP reads as 11 unconditionally.  Software discovers which privilege
    //    modes exist by writing MPP and reading it back, so a writable MPP on
    //    an M-only core is a lie told to the OS.
    a_mpp_fixed: assert (mstatus_rd[12:11] == 2'b11);
  end

  // The sequential properties, and so the ones that need a clocked block.
  //
  // TWO THINGS ABOUT $past HERE COST A CYCLE TO LEARN.  An assertion inside an
  // always_ff sees the values of the cycle that is ENDING, not the ones the
  // edge produces -- so `mstatus_mie_q` below is cycle T and `$past(trap_en)`
  // is cycle T-1, which is exactly the relationship wanted.  And `$past` is a
  // register: at the very first step its content is unconstrained in BMC, not
  // the input's initial value, so without `f_past_valid` the solver simply
  // invents a trap that never happened.  That is what the first counterexample
  // here turned out to be.
  logic f_past_valid = 1'b0;
  always_ff @(posedge clk) f_past_valid <= 1'b1;

  always_ff @(posedge clk) begin
    if (f_past_valid && rst_n && $past(rst_n)) begin
      // A trap leaves interrupts disabled and remembers whether they were
      // enabled.  Getting this backwards is invisible until the first nested
      // trap, which is a long way from here.
      if ($past(trap_en)) begin
        a_trap_disables:  assert (!mstatus_mie_q);
        a_trap_saves_mie: assert (mstatus_mpie_q == $past(mstatus_mie_q));
      end
      // A write to minstret must LAND, and must suppress the increment the
      // writing instruction would otherwise contribute -- which is the whole
      // content of riscv-tests' instret_overflow, and the property an
      // in-flight correction cannot satisfy.  Stated as "the next value is
      // exactly what was written", which is false if the increment survives.
      if ($past(do_write) && $past(addr) == CSR_MINSTRET &&
          !$past(trap_en) && !$past(mret_en)) begin
        a_minstret_write_lands: assert (minstret_q[31:0] == $past(wdata));
      end
      if ($past(do_write) && $past(addr) == CSR_MINSTRETH &&
          !$past(trap_en) && !$past(mret_en)) begin
        a_minstreth_write_lands: assert (minstret_q[63:32] == $past(wdata));
        // and the low half is untouched: writing one half must not disturb
        // the other, which a naive 64-bit write would get wrong.
        a_minstreth_keeps_low: assert (minstret_q[31:0] == $past(minstret_q[31:0]));
      end

      // MRET is its exact inverse.
      if ($past(mret_en) && !$past(trap_en)) begin
        a_mret_restores: assert (mstatus_mie_q == $past(mstatus_mpie_q));
        a_mret_sets_mpie: assert (mstatus_mpie_q);
      end
    end
  end
`endif

endmodule

`default_nettype wire
