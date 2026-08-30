// ============================================================================
// rvntt_rvfi -- the RISC-V Formal Interface port (A11).
//
// riscv-formal attaches a monitor to a standardised retirement port and checks
// each retired instruction against a formal model of the ISA.  Plan A11 says to
// "wire it out of your existing commit tracer -- the information is the same, in
// a standardized form".  IT IS NOT THE SAME, and the difference is the whole
// reason this is a module rather than a handful of assigns:
//
//   * riscv-formal requires a TRAPPING instruction to be reported, with
//     rvfi_trap set.  rvntt_core squashes a faulting instruction in EX (its
//     "trap invariant"), so it never reaches WB and never appears in the commit
//     stream at all.  That squash is load-bearing -- it is what keeps the commit
//     log comparable with Spike, which prints no line for a trapping
//     instruction, and what makes minstret correct without an in-flight
//     correction -- so it is NOT unpicked here.  Instead this module carries a
//     SECOND, parallel report path for the trapped instruction.
//
//   * rvfi_order must count trapped instructions.  minstret deliberately does
//     not.  Deriving one from the other would be wrong in exactly the case that
//     matters, so the order counter is its own register.
//
// THE TRAPPED INSTRUCTION FITS IN THE HOLE IT LEAVES BEHIND.  An instruction
// that traps in EX at cycle T clears ex_mem_q at the end of T, so mem_wb_q is a
// bubble at T+2 -- precisely the cycle that instruction would have retired.
// This module's two shadow registers are loaded on the same edges as ex_mem_q
// and mem_wb_q, so the trap report emerges in that empty slot.  NRET is 1 and
// the two paths can never collide; the assertions at the bottom say so rather
// than leaving it as a comment.
//
// WHAT COMES FROM THE REAL PIPELINE AND WHAT COMES FROM THE SHADOW.  Everything
// that still exists at WB is taken from the pipeline registers the core
// actually uses -- pc, insn, and the register file's own write port -- so that
// RVFI reports what the machine did rather than what a parallel copy predicted.
// Only what has no later copy is shadowed: the forwarded EX operands, the data
// bus request, and the trap's own pc_wdata.
//
// This module is instantiated only under `RISCV_FORMAL, so it costs nothing in
// a bitstream.  It is package-free on purpose: nothing in it needs rv32i_pkg,
// and staying self-contained keeps it readable next to the standard interface
// it implements.
// ============================================================================
`default_nettype none

module rvntt_rvfi (
    input  wire         clk,
    input  wire         rst_n,

    // ---- EX: the cycle the instruction executes ----------------------------
    // Operands are the FORWARDED ones, because RVFI wants the architectural
    // pre-state value and the register file's own read port may be stale by up
    // to two instructions.  They are gated by uses_rs1/uses_rs2 for the same
    // reason forwarding is: for an instruction that reads no rs1, those bits of
    // the word are part of an immediate, and reporting them as a register read
    // would be a claim about a register the instruction never touched.
    input  wire         ex_valid,
    input  wire         ex_trap,
    input  wire [31:0]  ex_pc,
    input  wire [31:0]  ex_insn,
    input  wire         ex_redirect,
    input  wire [31:0]  ex_redirect_target,
    input  wire         ex_uses_rs1,
    input  wire         ex_uses_rs2,
    input  wire [4:0]   ex_rs1_addr,
    input  wire [4:0]   ex_rs2_addr,
    input  wire [31:0]  ex_rs1_fwd,
    input  wire [31:0]  ex_rs2_fwd,
    input  wire         ex_mem_read,
    // The effective address the ALU just produced.  Its low two bits are
    // dropped on purpose -- see mem_addr below -- so UNUSEDSIGNAL is scoped to
    // this one port rather than waived for the module.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [31:0]  ex_alu_y,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [3:0]   ex_dmem_be,      // already zero on a trap or a bubble
    input  wire [31:0]  ex_dmem_wdata,

    // ---- MEM: the data the RAM returned for the access issued in EX --------
    input  wire [31:0]  mem_dmem_rdata,

    // ---- WB: the real retirement, and the register file's write port -------
    input  wire         wb_valid,
    input  wire [31:0]  wb_pc,
    input  wire [31:0]  wb_insn,
    input  wire         wb_we,           // valid && reg_write && rd != x0
    input  wire [4:0]   wb_rd_addr,
    input  wire [31:0]  wb_rd_data,

    // ---- the interface -----------------------------------------------------
    output wire         rvfi_valid,
    output wire [63:0]  rvfi_order,
    output wire [31:0]  rvfi_insn,
    output wire         rvfi_trap,
    output wire         rvfi_halt,
    output wire         rvfi_intr,
    output wire [1:0]   rvfi_mode,
    output wire [1:0]   rvfi_ixl,
    output wire [4:0]   rvfi_rs1_addr,
    output wire [4:0]   rvfi_rs2_addr,
    output wire [31:0]  rvfi_rs1_rdata,
    output wire [31:0]  rvfi_rs2_rdata,
    output wire [4:0]   rvfi_rd_addr,
    output wire [31:0]  rvfi_rd_wdata,
    output wire [31:0]  rvfi_pc_rdata,
    output wire [31:0]  rvfi_pc_wdata,
    output wire [31:0]  rvfi_mem_addr,
    output wire [3:0]   rvfi_mem_rmask,
    output wire [3:0]   rvfi_mem_wmask,
    output wire [31:0]  rvfi_mem_rdata,
    output wire [31:0]  rvfi_mem_wdata
);

  // The shadow payload.  Only fields with no surviving copy at WB are here.
  typedef struct packed {
    logic        valid;
    logic        trap;
    logic [31:0] pc_rdata;
    logic [31:0] pc_wdata;
    logic [31:0] insn;
    logic [4:0]  rs1_addr;
    logic [4:0]  rs2_addr;
    logic [31:0] rs1_rdata;
    logic [31:0] rs2_rdata;
    logic [31:0] mem_addr;
    logic [3:0]  mem_rmask;
    logic [3:0]  mem_wmask;
    logic [31:0] mem_wdata;
  } shadow_t;

  shadow_t ex_pkt, em_q, mw_q;
  logic [31:0] mw_mem_rdata_q;
  logic [63:0] order_q;

  always_comb begin
    ex_pkt.valid    = ex_valid;
    ex_pkt.trap     = ex_valid && ex_trap;
    ex_pkt.pc_rdata = ex_pc;

    // The next pc, for every shape at once.  A trap redirects to mtvec, an MRET
    // to mepc, a taken branch or jump to its target -- and rvntt_core has
    // already resolved all three into ex_redirect_target, so restating the
    // priority here would be a second place to get it wrong.  RVFI wants the
    // architectural next pc even for a trapping instruction, which is exactly
    // the trap vector.
    ex_pkt.pc_wdata = ex_redirect ? ex_redirect_target : (ex_pc + 32'd4);

    ex_pkt.insn      = ex_insn;
    ex_pkt.rs1_addr  = ex_uses_rs1 ? ex_rs1_addr : 5'd0;
    ex_pkt.rs2_addr  = ex_uses_rs2 ? ex_rs2_addr : 5'd0;
    ex_pkt.rs1_rdata = ex_uses_rs1 ? ex_rs1_fwd  : 32'd0;
    ex_pkt.rs2_rdata = ex_uses_rs2 ? ex_rs2_fwd  : 32'd0;

    // THE FULL COMPUTED ADDRESS, word-aligned -- not the address the RAM
    // actually used.  rvntt_ram deliberately drops everything above its array
    // and aliases rather than faulting; that is the memory's behaviour, not the
    // instruction's, and RVFI describes the instruction.  The low two bits go
    // because RISCV_FORMAL_ALIGNED_MEM is set: this core traps on a misaligned
    // access, so every access it performs is word-aligned by construction and
    // the spec models expect the aligned base with a byte mask.
    ex_pkt.mem_addr  = {ex_alu_y[31:2], 2'b00};

    // rvntt_ram reads the whole word on every access, so all four read-strobe
    // bits are honest for any load width; the spec model shifts the byte it
    // wants out of mem_rdata.  A trapping load performed no architectural
    // access at all, so it reports none.
    ex_pkt.mem_rmask = (ex_valid && ex_mem_read && !ex_trap) ? 4'b1111 : 4'b0000;

    // The store strobes come straight off the data bus, which rvntt_core has
    // already gated on validity and on !ex_trap -- one source, so a suppressed
    // store cannot be reported as having happened.
    ex_pkt.mem_wmask = ex_dmem_be;
    ex_pkt.mem_wdata = ex_dmem_wdata;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      em_q           <= '0;
      mw_q           <= '0;
      mw_mem_rdata_q <= 32'h0;
    end else begin
      em_q           <= ex_pkt;
      mw_q           <= em_q;
      // Sampled on the edge out of MEM, which is the cycle rvntt_ram's output
      // register holds the word for the access em_q issued.
      mw_mem_rdata_q <= mem_dmem_rdata;
    end
  end

  // rvfi_order is the retirement index of the instruction being reported, so it
  // is the count of everything reported BEFORE this one -- incremented after.
  // It counts traps, which is what makes it not minstret.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         order_q <= 64'd0;
    else if (mw_q.valid) order_q <= order_q + 64'd1;
  end

  assign rvfi_valid     = mw_q.valid;
  assign rvfi_order     = order_q;
  assign rvfi_trap      = mw_q.trap;
  assign rvfi_halt      = 1'b0;

  // rvfi_intr marks an instruction whose pc_rdata does not follow the previous
  // instruction's pc_wdata.  This core reports the trap vector as the trapping
  // instruction's pc_wdata, and the handler's first instruction is fetched from
  // exactly there, so the chain is unbroken and intr is always low.  That is the
  // stronger claim: pc_fwd and pc_bwd both stop checking an instruction that
  // sets it.
  assign rvfi_intr      = 1'b0;
  assign rvfi_mode      = 2'b11;                 // machine mode only
  assign rvfi_ixl       = 2'b01;                 // MXL = 1, XLEN = 32

  // pc and insn survive to WB in the core's own registers, so report those and
  // fall back to the shadow only for the trapped instruction, which has none.
  assign rvfi_pc_rdata  = wb_valid ? wb_pc   : mw_q.pc_rdata;
  assign rvfi_insn      = wb_valid ? wb_insn : mw_q.insn;
  assign rvfi_pc_wdata  = mw_q.pc_wdata;

  assign rvfi_rs1_addr  = mw_q.rs1_addr;
  assign rvfi_rs2_addr  = mw_q.rs2_addr;
  assign rvfi_rs1_rdata = mw_q.rs1_rdata;
  assign rvfi_rs2_rdata = mw_q.rs2_rdata;

  // Straight off the register file's write port.  RVFI requires rd_addr to be
  // zero for an instruction that writes no register -- and a store's rd field
  // is part of its immediate, so reporting the decoded field would name a
  // register the instruction never wrote.  A trapped instruction has wb_we low
  // by construction (it was squashed), so the trap case needs no mux of its own.
  assign rvfi_rd_addr   = wb_we ? wb_rd_addr : 5'd0;
  assign rvfi_rd_wdata  = wb_we ? wb_rd_data : 32'd0;

  assign rvfi_mem_addr  = mw_q.mem_addr;
  assign rvfi_mem_rmask = mw_q.mem_rmask;
  assign rvfi_mem_wmask = mw_q.mem_wmask;
  assign rvfi_mem_rdata = mw_mem_rdata_q;
  assign rvfi_mem_wdata = mw_q.mem_wdata;

`ifdef RISCV_FORMAL
  // The three claims this module is built on, asserted inside every riscv-formal
  // check rather than argued in a comment.  If the trap squash and the shadow
  // ever drift apart, these fail directly instead of surfacing as an
  // unexplained insn-check counterexample four stages away.
  //
  // f_started_q needs the declaration initialiser as well as the reset: with an
  // asynchronous reset the flop's value during the reset cycle itself is the
  // init value, not the reset value, and without it the solver is free to
  // invent a first step in which the guard is already true.
  /* verilator lint_off PROCASSINIT */
  logic f_started_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) f_started_q <= 1'b0;
    else        f_started_q <= 1'b1;
  end

  always_ff @(posedge clk) begin
    if (f_started_q) begin
      // 1. A trapped instruction never also retires: the two report paths are
      //    mutually exclusive, which is what makes NRET = 1 sound.
      a_trap_not_retired: assert (!(mw_q.valid && mw_q.trap && wb_valid));
      // 2. ... and every non-trapping instruction does retire in its own slot.
      a_nontrap_retired:  assert (!(mw_q.valid && !mw_q.trap) || wb_valid);
      // 3. The shadow and the pipeline agree about which instruction this is.
      if (mw_q.valid && wb_valid) begin
        a_shadow_pc:   assert (wb_pc   == mw_q.pc_rdata);
        a_shadow_insn: assert (wb_insn == mw_q.insn);
      end
    end
  end
`endif

endmodule

`default_nettype wire
