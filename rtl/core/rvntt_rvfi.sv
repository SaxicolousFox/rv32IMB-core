// ============================================================================
// rvntt_rvfi -- the RISC-V Formal Interface port.
//
// riscv-formal requires a trapping instruction to be reported with rvfi_trap
// set, but rvntt_core squashes a faulting instruction in EX (which keeps the
// commit log comparable with Spike and minstret correct).  So this module
// carries a second, parallel report path: two shadow registers loaded on the
// same edges as ex_mem_q and mem_wb_q, so the trap report emerges in the
// bubble the squashed instruction leaves behind.  rvfi_order counts trapped
// instructions (minstret does not) and is its own register.
//
// Everything that survives to WB comes from the pipeline's own registers;
// only what has no later copy (the forwarded EX operands, the data-bus
// request, the trap's pc_wdata) is shadowed.  Instantiated only under
// RISCV_FORMAL.  Package-free.
// ============================================================================
`default_nettype none

module rvntt_rvfi (
    input  wire         clk,
    input  wire         rst_n,

    // ---- EX: the cycle the instruction executes ----------------------------
    // Operands are the forwarded ones (RVFI wants the architectural pre-state
    // value), gated by uses_rs1/uses_rs2.
    input  wire         ex_valid,
    input  wire         ex_trap,
    // EX is holding a multi-cycle instruction that has not finished: the
    // shadow must take the same bubble ex_mem_q takes, and the packet must be
    // sampled on the instruction's first EX cycle (see ex_hold_q).
    input  wire         ex_stall,
    input  wire [31:0]  ex_pc,
    input  wire [31:0]  ex_insn,
    input  wire [31:0]  ex_redirect_target,
    input  wire         ex_uses_rs1,
    input  wire         ex_uses_rs2,
    input  wire [4:0]   ex_rs1_addr,
    input  wire [4:0]   ex_rs2_addr,
    input  wire [31:0]  ex_rs1_fwd,
    input  wire [31:0]  ex_rs2_fwd,
    input  wire         ex_mem_read,
    // The effective address from the dedicated adder; its low two bits are
    // dropped (see mem_addr below).
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [31:0]  ex_mem_addr,
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

  // The shadow payload: only fields with no surviving copy at WB.
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

  shadow_t ex_pkt, ex_pkt_eff, ex_hold_q, em_q, mw_q;
  logic [31:0] mw_mem_rdata_q;
  logic [63:0] order_q;

  always_comb begin
    ex_pkt.valid    = ex_valid;
    ex_pkt.trap     = ex_valid && ex_trap;
    ex_pkt.pc_rdata = ex_pc;

    // The architectural next pc for every shape (trap vector, mepc, target,
    // pc + 4), already resolved by rvntt_core.  Not gated on ex_redirect: a
    // correctly predicted taken branch does not redirect.
    ex_pkt.pc_wdata = ex_redirect_target;

    ex_pkt.insn      = ex_insn;
    ex_pkt.rs1_addr  = ex_uses_rs1 ? ex_rs1_addr : 5'd0;
    ex_pkt.rs2_addr  = ex_uses_rs2 ? ex_rs2_addr : 5'd0;
    ex_pkt.rs1_rdata = ex_uses_rs1 ? ex_rs1_fwd  : 32'd0;
    ex_pkt.rs2_rdata = ex_uses_rs2 ? ex_rs2_fwd  : 32'd0;

    // The full computed address, word-aligned (RISCV_FORMAL_ALIGNED_MEM), not
    // the address rvntt_ram aliased it to.
    ex_pkt.mem_addr  = {ex_mem_addr[31:2], 2'b00};

    // rvntt_ram reads the whole word on every access; a trapping load
    // performed no access.
    ex_pkt.mem_rmask = (ex_valid && ex_mem_read && !ex_trap) ? 4'b1111 : 4'b0000;

    // The store strobes come straight off the data bus, already gated on
    // validity and !ex_trap.
    ex_pkt.mem_wmask = ex_dmem_be;
    ex_pkt.mem_wdata = ex_dmem_wdata;
  end

  // The packet is a snapshot of the FIRST EX cycle.  During a multi-cycle
  // stall the producers behind the instruction drain out of MEM and WB and the
  // forwarding mux falls back to the ID-time register value; the unit itself
  // latched its operands, so only the report would be wrong (riscv-formal's
  // `reg` check found it).  `!ex_stall_q` is "this is the first EX cycle".
  logic ex_stall_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) ex_stall_q <= 1'b0;
    else        ex_stall_q <= ex_stall;
  end
  wire ex_first = !ex_stall_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) ex_hold_q <= '0;
    else if (ex_first) ex_hold_q <= ex_pkt;
  end

  assign ex_pkt_eff = ex_first ? ex_pkt : ex_hold_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      em_q           <= '0;
      mw_q           <= '0;
      mw_mem_rdata_q <= 32'h0;
    end else begin
      em_q           <= ex_stall ? '0 : ex_pkt_eff;
      mw_q           <= em_q;
      // Sampled on the edge out of MEM, when the RAM's output register holds
      // the word for the access em_q issued.
      mw_mem_rdata_q <= mem_dmem_rdata;
    end
  end

  // rvfi_order is the count of everything reported before this instruction.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         order_q <= 64'd0;
    else if (mw_q.valid) order_q <= order_q + 64'd1;
  end

  assign rvfi_valid     = mw_q.valid;
  assign rvfi_order     = order_q;
  assign rvfi_trap      = mw_q.trap;
  assign rvfi_halt      = 1'b0;

  // The trap vector is reported as the trapping instruction's pc_wdata and
  // the handler is fetched from exactly there, so the chain is unbroken.
  assign rvfi_intr      = 1'b0;
  assign rvfi_mode      = 2'b11;                 // machine mode only
  assign rvfi_ixl       = 2'b01;                 // MXL = 1, XLEN = 32

  // pc and insn survive to WB in the core's own registers; the shadow is used
  // only for the trapped instruction.
  assign rvfi_pc_rdata  = wb_valid ? wb_pc   : mw_q.pc_rdata;
  assign rvfi_insn      = wb_valid ? wb_insn : mw_q.insn;
  assign rvfi_pc_wdata  = mw_q.pc_wdata;

  assign rvfi_rs1_addr  = mw_q.rs1_addr;
  assign rvfi_rs2_addr  = mw_q.rs2_addr;
  assign rvfi_rs1_rdata = mw_q.rs1_rdata;
  assign rvfi_rs2_rdata = mw_q.rs2_rdata;

  // Straight off the register file's write port: rd_addr must be zero for an
  // instruction that writes no register (a store's rd field is immediate
  // bits).  A trapped instruction has wb_we low by construction.
  assign rvfi_rd_addr   = wb_we ? wb_rd_addr : 5'd0;
  assign rvfi_rd_wdata  = wb_we ? wb_rd_data : 32'd0;

  assign rvfi_mem_addr  = mw_q.mem_addr;
  assign rvfi_mem_rmask = mw_q.mem_rmask;
  assign rvfi_mem_wmask = mw_q.mem_wmask;
  assign rvfi_mem_rdata = mw_mem_rdata_q;
  assign rvfi_mem_wdata = mw_q.mem_wdata;

`ifdef RISCV_FORMAL
  // The claims this module is built on, asserted inside every riscv-formal
  // check.  f_started_q needs the declaration initialiser as well as the
  // reset (asynchronous reset semantics at step 0).
  /* verilator lint_off PROCASSINIT */
  logic f_started_q = 1'b0;
  /* verilator lint_on PROCASSINIT */
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) f_started_q <= 1'b0;
    else        f_started_q <= 1'b1;
  end

  always_ff @(posedge clk) begin
    if (f_started_q) begin
      // 1. The two report paths are mutually exclusive (NRET = 1 is sound).
      a_trap_not_retired: assert (!(mw_q.valid && mw_q.trap && wb_valid));
      // 2. Every non-trapping instruction retires in its own slot.
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
