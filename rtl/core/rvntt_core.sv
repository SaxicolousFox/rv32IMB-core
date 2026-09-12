// ============================================================================
// rvntt_core -- the 5-stage RV32IMB pipeline (IF, ID, EX, MEM, WB).
//
//   * Forwarding: EX/MEM -> EX and MEM/WB -> EX for both operands, the
//     store-data operand included; distance 3 is the register file's
//     write-through.
//   * Load-use interlock: a load's result is not forwarded from MEM, so a
//     consumer one slot behind a load stalls one cycle and takes FWD_WB.
//   * Control flow: branches resolve in EX; a mispredict, trap or MRET
//     redirects the PC and squashes the two younger instructions (2 cycles).
//     A branch predictor (BTB + 2-bit counters + return stack) is looked up
//     one address ahead of the fetch.
//   * Multi-cycle EX: an EX-resident unit (the M multiplier/divider) holds the
//     instruction in EX with `req && !done`; the front end and ID/EX hold,
//     EX/MEM takes a bubble per stalled cycle.
//   * Every trap resolves in EX, so an instruction that reaches MEM retires.
//     The misaligned-address check is in EX for that reason, and a faulting
//     store is suppressed before the RAM's address register sees it.
//   * Memory timing: rvntt_ram registers each port's address, so its output
//     register is a pipeline register.  Port B's address comes from the EX
//     address adder, so load data is valid during MEM.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_core #(
    parameter logic [31:0] RESET_PC = 32'h8000_0000,
    // 1 for every simulation and formal build; the SoC top sets 0 so the
    // bitstream gets the real ring oscillator.  See rvntt_entropy.sv.
    parameter bit          ENTROPY_STUB = 1
) (
    input  wire         clk,
    input  wire         rst_n,

    // The stub noise source's raw bit, driven by a testbench; tied off in the
    // SoC.  Present in both builds so the port list does not depend on a
    // parameter.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire         entropy_stub_bit,
    /* verilator lint_on UNUSEDSIGNAL */

    // Instruction port (rvntt_ram port A).  Address out, data back next cycle.
    output logic [31:0] imem_addr,
    input  wire  [31:0] imem_rdata,

    // Data port (rvntt_ram port B).
    output logic [31:0] dmem_addr,
    output logic [31:0] dmem_wdata,
    output logic [3:0]  dmem_be,
    input  wire  [31:0] dmem_rdata,

    // Retirement trace: the commit-log source, and how testbenches observe
    // architectural state without reaching into the register file.
    output logic        commit_valid,
    output logic [31:0] commit_pc,
    output logic [31:0] commit_insn,
    output logic        commit_reg_write,
    output logic [4:0]  commit_rd,
    output logic [31:0] commit_wdata,

    // Pulses with commit_valid if an illegal instruction ever retires (it
    // cannot: illegal instructions trap in EX).  A live check on the trap path.
    output logic        dbg_unsupported

    // The RVFI port, a verification interface with no architectural function;
    // rvntt_rvfi.sv is a second, parallel report path next to the commit trace.
`ifdef RISCV_FORMAL
    ,
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
`endif
);

  // ==========================================================================
  // Pipeline registers
  // ==========================================================================
  // Declared ahead of the stages because EX reads ex_mem_q and mem_wb_q.
  // ex_mem_q carries mem_write and store_data for a store buffer this pipeline
  // does not have; the waiver is scoped to the declarations.
  rv32i_pkg::if_id_t  if_id_q, if_id;
  /* verilator lint_off UNUSEDSIGNAL */
  rv32i_pkg::id_ex_t  id_ex_q;
  rv32i_pkg::ex_mem_t ex_mem_q;
  /* verilator lint_on UNUSEDSIGNAL */
  rv32i_pkg::mem_wb_t mem_wb_q;

  // ==========================================================================
  // IF -- program counter
  // ==========================================================================
  logic [31:0] pc_q;
  // Two stalls that do different things: id_stall (load-use) bubbles ID/EX and
  // holds the front end; ex_stall (multi-cycle EX) holds ID/EX and bubbles
  // EX/MEM.  ex_stall has priority on ID/EX.
  logic        id_stall;                   // driven by rvntt_hazard, in ID
  logic        ex_stall;                   // driven by an EX functional unit
  wire         front_stall = id_stall || ex_stall;
  // ex_redirect clears every pipeline register; the fanout limit is a
  // synthesis directive and cannot change what the design computes.
  (* max_fanout = 48 *)
  logic        ex_redirect;                // a control transfer, MRET or a trap
  logic [31:0] ex_redirect_target;
  logic [31:0] ex_jump_target;             // branch/JAL/JALR only
  logic        ex_trap;                    // the instruction in EX faults
  logic [4:0]  ex_trap_cause;
  logic [31:0] ex_trap_val;
  logic        ex_mret;

  // bp_pred_* come out of the predictor and steer the PC mux; ex_bp_* go into
  // it from the EX stage's resolution.
  logic        bp_pred_taken;
  logic [31:0] bp_pred_target;
  logic        bp_pred_hit;               // observational only
  logic        ex_bp_upd;
  logic [1:0]  ex_bp_kind;

  // A redirect and a stall cannot coincide (both are properties of the single
  // instruction in EX, and ex_redirect is gated on !ex_stall).  The redirect
  // outranks the prediction because EX has seen the instruction and IF only
  // its address.
  logic [31:0] pc_next;
  always_comb begin
    if      (ex_redirect)   pc_next = ex_redirect_target;
    else if (front_stall)   pc_next = pc_q;
    else if (bp_pred_taken) pc_next = bp_pred_target;
    else                    pc_next = pc_q + 32'd4;
  end

  // The predictor is looked up from registered sources only: indexing it with
  // pc_next would put the ALU in the fetch path.  The consequence is that the
  // instruction at a redirect target cannot be predicted (`flush`).  Under a
  // front-end stall the predictor holds its registered answer rather than
  // re-looking-up, which keeps the decoder out of the BTB's index path.
  wire [31:0] bp_lookup_pc = bp_pred_taken ? bp_pred_target
                                           : pc_q + 32'd4;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) pc_q <= RESET_PC;
    else        pc_q <= pc_next;
  end

  rvntt_bpred u_bpred (
      .clk         (clk),
      .rst_n       (rst_n),
      .lookup_pc   (bp_lookup_pc),
      .flush       (ex_redirect),
      .hold        (front_stall),
      .pred_taken  (bp_pred_taken),
      .pred_target (bp_pred_target),
      .pred_hit    (bp_pred_hit),
      .upd_valid   (ex_bp_upd),
      .upd_pc      (id_ex_q.pc),
      .upd_kind    (ex_bp_kind),
      .upd_taken   (ex_ctrl_xfer),
      .upd_target  (ex_jump_target)
  );

  assign imem_addr = pc_q;

  // ==========================================================================
  // IF/ID
  // ==========================================================================
  // The instruction word is not flopped here: it arrives from the RAM's own
  // output register.  A stall therefore needs a holding register -- the RAM
  // has already been loaded with the next word -- captured from if_id.insn so
  // a multi-cycle stall keeps replaying it.
  logic [31:0] insn_hold_q;
  logic        insn_held_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      insn_hold_q <= 32'h0;
      insn_held_q <= 1'b0;
    end else begin
      insn_hold_q <= if_id.insn;
      // Not replayed across a redirect: the held word belongs to the
      // discarded path.
      insn_held_q <= front_stall && !ex_redirect;
    end
  end

  // The flush kills both younger slots: one in ID, one whose fetch is in
  // flight.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      if_id_q.valid <= 1'b0;
      if_id_q.pc    <= RESET_PC;
      if_id_q.insn  <= 32'h0;
      if_id_q.pred_taken  <= 1'b0;
      if_id_q.pred_target <= 32'h0;
      if_id_q.pred_hit    <= 1'b0;
    end else if (ex_redirect) begin
      if_id_q.valid <= 1'b0;
      if_id_q.pc    <= pc_q;
      if_id_q.insn  <= 32'h0;
      if_id_q.pred_taken  <= 1'b0;
      if_id_q.pred_target <= 32'h0;
      if_id_q.pred_hit    <= 1'b0;
    end else if (!front_stall) begin
      if_id_q.valid <= 1'b1;
      if_id_q.pc    <= pc_q;
      if_id_q.insn  <= 32'h0;              // unused; insn comes from the RAM
      // bp_pred_* describe pc_q, the address being fetched this cycle.
      if_id_q.pred_taken  <= bp_pred_taken;
      if_id_q.pred_target <= bp_pred_target;
      if_id_q.pred_hit    <= bp_pred_hit;
    end
  end

  always_comb begin
    if_id       = if_id_q;
    if_id.insn  = insn_held_q ? insn_hold_q : imem_rdata;
  end

  // ==========================================================================
  // ID -- decode, immediate, register read
  // ==========================================================================
  rv32i_pkg::ctrl_t id_ctrl;
  logic [4:0]  id_rd, id_rs1, id_rs2;
  logic [31:0] id_imm;
  logic [31:0] id_rs1_data, id_rs2_data;

  rvntt_decode u_decode (
      .insn     (if_id.insn),
      .ctrl     (id_ctrl),
      .rd_addr  (id_rd),
      .rs1_addr (id_rs1),
      .rs2_addr (id_rs2)
  );

  rvntt_immgen u_immgen (
      .insn (if_id.insn),
      .fmt  (id_ctrl.imm_fmt),
      .imm  (id_imm)
  );

  // Declared here so the WB stage's write signals can be referenced.
  logic        wb_we;
  logic [4:0]  wb_wa;
  logic [31:0] wb_wd;

  rvntt_regfile u_regfile (
      .clk (clk),
      .ra1 (id_rs1), .ra2 (id_rs2),
      .rd1 (id_rs1_data), .rd2 (id_rs2_data),
      .we  (wb_we), .wa (wb_wa), .wd (wb_wd)
  );

  // ---- the load-use interlock ---------------------------------------------
  rvntt_hazard u_hazard (
      .id_valid    (if_id.valid),
      .id_uses_rs1 (id_ctrl.uses_rs1),
      .id_uses_rs2 (id_ctrl.uses_rs2),
      .id_rs1_addr (id_rs1),
      .id_rs2_addr (id_rs2),
      .ex_valid    (id_ex_q.valid),
      .ex_mem_read (id_ex_q.ctrl.mem_read),
      .ex_rd_addr  (id_ex_q.rd_addr),
      .stall       (id_stall)
  );

  // ==========================================================================
  // ID/EX
  // ==========================================================================
  // A bubble is a whole-struct clear, so it cannot carry a live mem_write.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      id_ex_q <= '0;
    end else if (ex_stall) begin
      // Hold: this register is the multi-cycle unit's instruction.  Tested
      // before id_stall; both cannot be true (the EX instruction would have to
      // be a load and multi-cycle).
      id_ex_q <= id_ex_q;

      // The precomputed forwarding selects decay MEM -> WB -> REG, one step
      // per stalled cycle: the producers drain out from under the held
      // instruction, and a held FWD_MEM would read a bubble (zero).  Found by
      // a_fwd_precompute_matches_* under riscv-formal; invisible to
      // cosimulation because the only reader after the start cycle is the
      // multi-cycle unit, which does not re-read.
      id_ex_q.fwd_a <= fwd_decay(id_ex_q.fwd_a);
      id_ex_q.fwd_b <= fwd_decay(id_ex_q.fwd_b);
    end else if (id_stall || ex_redirect) begin
      id_ex_q <= '0;
    end else begin
      id_ex_q.valid    <= if_id.valid;
      id_ex_q.pc       <= if_id.pc;
      id_ex_q.insn     <= if_id.insn;
      id_ex_q.ctrl     <= id_ctrl;
      id_ex_q.imm      <= id_imm;
      id_ex_q.rs1_addr <= id_rs1;
      id_ex_q.rs2_addr <= id_rs2;
      id_ex_q.rd_addr  <= id_rd;
      id_ex_q.rs1_data <= id_rs1_data;
      id_ex_q.rs2_data <= id_rs2_data;
      id_ex_q.pred_taken  <= if_id.pred_taken;
      id_ex_q.pred_target <= if_id.pred_target;
      id_ex_q.pred_hit    <= if_id.pred_hit;
      id_ex_q.fwd_a       <= id_fwd_a;
      id_ex_q.fwd_b       <= id_fwd_b;
    end
  end

  // ==========================================================================
  // EX -- forwarding, then the ALU
  // ==========================================================================
  // One step of the decay: the producer in MEM moves to WB, the producer in
  // WB leaves.  Saturates at FWD_REG.
  function automatic rv32i_pkg::fwd_sel_e fwd_decay(input rv32i_pkg::fwd_sel_e s);
    fwd_decay = (s == rv32i_pkg::FWD_MEM) ? rv32i_pkg::FWD_WB
                                          : rv32i_pkg::FWD_REG;
  endfunction

  // ex_fwd_* is a registered field; id_fwd_* is where it is computed, one
  // stage earlier.
  rv32i_pkg::fwd_sel_e ex_fwd_a, ex_fwd_b;
  rv32i_pkg::fwd_sel_e id_fwd_a, id_fwd_b;
  logic [31:0] ex_rs1_fwd, ex_rs2_fwd;

  // The MEM stage's forwardable value.  Not mem_result: this mux is only over
  // sources that are already registered, so the BRAM output never reaches the
  // ALU input path.  It must agree with mem_result for every result_sel
  // FWD_MEM can select; the two case statements are written the same way
  // round.
  logic [31:0] ex_mem_fwd_data;
  always_comb begin
    unique case (ex_mem_q.result_sel)
      rv32i_pkg::RES_PC4: ex_mem_fwd_data = ex_mem_q.pc_plus4;
      rv32i_pkg::RES_ALU,
      rv32i_pkg::RES_CSR: ex_mem_fwd_data = ex_mem_q.ex_result;
      // The unassigned encodings, and RES_MEM -- unreachable here because
      // rvntt_forward excludes loads from FWD_MEM.
      default:            ex_mem_fwd_data = 32'h0;
    endcase
  end

  // ---- the forwarding decision is made in ID --------------------------------
  // An instruction in EX at cycle T compares its rs against the producers in
  // MEM and WB; the same instruction in ID at T-1 compares against EX and MEM.
  // Those are the same two instructions because ID/EX takes a new instruction
  // only when EX/MEM advances unsquashed (a_pipe_advances_together), and
  // a_fwd_precompute_matches_* asserts the conclusion against a second copy
  // of the EX-stage computation.  The port names describe where each operand
  // WILL BE when the answer is used.
  rvntt_forward u_forward (
      .ex_rs1_addr   (id_rs1),
      .ex_rs2_addr   (id_rs2),
      .ex_uses_rs1   (id_ctrl.uses_rs1),
      .ex_uses_rs2   (id_ctrl.uses_rs2),
      .mem_valid     (id_ex_q.valid),
      .mem_reg_write (id_ex_q.ctrl.reg_write),
      .mem_mem_read  (id_ex_q.ctrl.mem_read),
      .mem_rd_addr   (id_ex_q.rd_addr),
      .wb_valid      (ex_mem_q.valid),
      .wb_reg_write  (ex_mem_q.reg_write),
      .wb_rd_addr    (ex_mem_q.rd_addr),
      .fwd_a         (id_fwd_a),
      .fwd_b         (id_fwd_b)
  );

  assign ex_fwd_a = id_ex_q.fwd_a;
  assign ex_fwd_b = id_ex_q.fwd_b;

  always_comb begin
    unique case (ex_fwd_a)
      rv32i_pkg::FWD_MEM: ex_rs1_fwd = ex_mem_fwd_data;
      rv32i_pkg::FWD_WB:  ex_rs1_fwd = mem_wb_q.wb_data;
      default:            ex_rs1_fwd = id_ex_q.rs1_data;
    endcase
  end

  always_comb begin
    unique case (ex_fwd_b)
      rv32i_pkg::FWD_MEM: ex_rs2_fwd = ex_mem_fwd_data;
      rv32i_pkg::FWD_WB:  ex_rs2_fwd = mem_wb_q.wb_data;
      default:            ex_rs2_fwd = id_ex_q.rs2_data;
    endcase
  end

  wire [31:0] ex_pc_plus4 = id_ex_q.pc + 32'd4;

  logic [31:0] ex_alu_a, ex_alu_b, ex_alu_y;

  always_comb begin
    unique case (id_ex_q.ctrl.alu_src_a)
      rv32i_pkg::SRCA_RS1:  ex_alu_a = ex_rs1_fwd;
      rv32i_pkg::SRCA_PC:   ex_alu_a = id_ex_q.pc;
      rv32i_pkg::SRCA_ZERO: ex_alu_a = 32'h0;
      default:              ex_alu_a = ex_rs1_fwd;
    endcase
  end

  assign ex_alu_b = (id_ex_q.ctrl.alu_src_b == rv32i_pkg::SRCB_IMM)
                    ? id_ex_q.imm : ex_rs2_fwd;

  rvntt_alu u_alu (
      .op (id_ex_q.ctrl.alu_op),
      .a  (ex_alu_a),
      .b  (ex_alu_b),
      .y  (ex_alu_y)
  );

  // ---- the multi-cycle EX unit ---------------------------------------------
  // A generic handshake: `req` for every cycle the instruction is in EX,
  // `done` on the last.  Operands are the forwarded ones; the unit latches
  // them on its first cycle.  The store path is deliberately not gated on
  // ex_stall (it would sit on the critical path); rvntt_decode's property 5
  // proves no multi-cycle instruction is a memory operation.
  logic        ex_md_done;
  logic [31:0] ex_md_result;
  wire         ex_md_req = id_ex_q.valid && id_ex_q.ctrl.is_muldiv;

  rvntt_muldiv u_muldiv (
      .clk    (clk),
      .rst_n  (rst_n),
      .req    (ex_md_req),
      .op     (id_ex_q.ctrl.muldiv_op),
      .a      (ex_rs1_fwd),
      .b      (ex_rs2_fwd),
      .done   (ex_md_done),
      .result (ex_md_result)
  );

  assign ex_stall = ex_md_req && !ex_md_done;

  // ---- the dedicated address adder ------------------------------------------
  // A data address is always rs1 + imm (the decoder enforces it, and
  // a_addr_adder_matches_alu proves it), so the alignment check, the byte
  // enables and the trap value take their address from a second adder rather
  // than from the ALU's operation-select mux.
  wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;

  // ---- data-memory request, issued from EX so the RAM's address register is
  // ---- the EX/MEM address register and rdata is valid during MEM.
  logic [1:0] ex_byte_off;
  assign ex_byte_off = ex_mem_addr[1:0];

  always_comb begin
    dmem_wdata = ex_rs2_fwd;
    dmem_be    = 4'b0000;
    // `!ex_trap` suppresses a misaligned store before the RAM's address
    // register latches it.
    if (id_ex_q.valid && id_ex_q.ctrl.mem_write && !ex_trap) begin
      unique case (id_ex_q.ctrl.mem_op)
        rv32i_pkg::F3_LB: begin                       // SB
          dmem_wdata = {4{ex_rs2_fwd[7:0]}};
          dmem_be    = 4'b0001 << ex_byte_off;
        end
        rv32i_pkg::F3_LH: begin                       // SH
          dmem_wdata = {2{ex_rs2_fwd[15:0]}};
          dmem_be    = ex_byte_off[1] ? 4'b1100 : 4'b0011;
        end
        rv32i_pkg::F3_LW: begin                       // SW
          dmem_wdata = ex_rs2_fwd;
          dmem_be    = 4'b1111;
        end
        default: dmem_be = 4'b0000;   // reserved widths never reach here: the
                                      // decoder rejects them as illegal
      endcase
    end
  end

  assign dmem_addr = ex_mem_addr;

  // ---- control transfer ------------------------------------------------------
  // The comparator reads the forwarded operands (`sub` then `beqz`).
  logic ex_branch_taken;

  rvntt_branch u_branch (
      .funct3 (id_ex_q.insn[14:12]),
      .a      (ex_rs1_fwd),
      .b      (ex_rs2_fwd),
      .taken  (ex_branch_taken)
  );

  wire ex_ctrl_xfer = id_ex_q.valid &&
                      ((id_ex_q.ctrl.branch && ex_branch_taken) ||
                       id_ex_q.ctrl.jump);

  // Branch and JAL targets come from a dedicated pc + imm adder; JALR's from
  // the address adder (rs1 + imm).  Neither uses the ALU result mux, which
  // keeps it off the mispredict path.  JALR clears bit 0 where the spec says,
  // not as a blanket mask, so a target misaligned for another reason still
  // reaches the misaligned-fetch trap.  a_jalr_target_matches_alu and
  // a_pc_target_matches_alu prove both adders agree with the ALU.
  wire [31:0] ex_pc_target = id_ex_q.pc + id_ex_q.imm;

  assign ex_jump_target = id_ex_q.ctrl.jalr ? {ex_mem_addr[31:1], 1'b0}
                                            : ex_pc_target;

  // ---- checking the prediction, and telling the predictor -------------------
  // The predictor's classification: does the target come from the BTB or the
  // return stack?  link(rd) && link(rs1) ("pop then push") is classified CALL
  // and mispredicts its return.
  wire ex_rd_link  = (id_ex_q.rd_addr  == 5'd1) || (id_ex_q.rd_addr  == 5'd5);
  wire ex_rs1_link = (id_ex_q.rs1_addr == 5'd1) || (id_ex_q.rs1_addr == 5'd5);

  always_comb begin
    if      (id_ex_q.ctrl.branch)             ex_bp_kind = rv32i_pkg::BP_BRANCH;
    else if (id_ex_q.ctrl.jalr && ex_rs1_link
             && !ex_rd_link)                  ex_bp_kind = rv32i_pkg::BP_RET;
    else if (ex_rd_link)                      ex_bp_kind = rv32i_pkg::BP_CALL;
    else                                      ex_bp_kind = rv32i_pkg::BP_JUMP;
  end

  // A trapping instruction teaches the predictor nothing.
  assign ex_bp_upd = id_ex_q.valid && !ex_stall && !ex_trap &&
                     (id_ex_q.ctrl.branch || id_ex_q.ctrl.jump);

  // Right when direction and (if taken) address both agree.  A prediction of
  // taken on a non-transfer needs no special case: ex_ctrl_xfer is 0 and the
  // redirect goes to pc + 4.
  wire ex_mispredict = id_ex_q.valid &&
                       ((id_ex_q.pred_taken != ex_ctrl_xfer) ||
                        (ex_ctrl_xfer &&
                         (id_ex_q.pred_target != ex_jump_target)));

  // ---- Zicsr access ----------------------------------------------------------
  // funct3[1:0] selects the operation, funct3[2] the immediate form:
  //   01 = CSRRW/CSRRWI   10 = CSRRS/CSRRSI   11 = CSRRC/CSRRCI
  wire [1:0]  ex_csr_op  = id_ex_q.insn[13:12];
  wire        ex_csr_imm = id_ex_q.insn[14];

  // The uimm arrives through immgen as IMM_Z.
  wire [31:0] ex_csr_src = ex_csr_imm ? id_ex_q.imm : ex_rs1_fwd;

  // CSRRS/CSRRC with a zero source do not write (which makes `csrr rd,
  // <read-only csr>` legal); CSRRW always writes.
  wire ex_csr_src_nz = ex_csr_imm ? (id_ex_q.imm[4:0] != 5'd0)
                                  : (id_ex_q.rs1_addr != 5'd0);
  wire ex_csr_wen = id_ex_q.valid && id_ex_q.ctrl.is_csr &&
                    ((ex_csr_op == 2'b01) || ex_csr_src_nz);

  logic [31:0] ex_csr_rdata, ex_csr_wdata;
  logic        ex_csr_illegal;
  logic [31:0] csr_mtvec, csr_mepc;

  always_comb begin
    unique case (ex_csr_op)
      2'b01:   ex_csr_wdata =  ex_csr_src;                   // CSRRW  / CSRRWI
      2'b10:   ex_csr_wdata =  ex_csr_rdata |  ex_csr_src;    // CSRRS  / CSRRSI
      2'b11:   ex_csr_wdata =  ex_csr_rdata & ~ex_csr_src;    // CSRRC  / CSRRCI
      default: ex_csr_wdata =  ex_csr_src;
    endcase
  end

  // ---- the Zihpm event bus ---------------------------------------------------
  // Bit positions are rv32i_pkg::HPM_EV_* minus one.  The load-use guard
  // `&& !ex_stall` is documentation of disjointness (a_stalls_are_disjoint),
  // not a tie-break.
  wire [rv32i_pkg::HPM_EV_COUNT-1:0] hpm_event_c;
  assign hpm_event_c[rv32i_pkg::HPM_EV_LOADUSE    - 1] = id_stall && !ex_stall;
  assign hpm_event_c[rv32i_pkg::HPM_EV_EXSTALL    - 1] = ex_stall;
  assign hpm_event_c[rv32i_pkg::HPM_EV_REDIRECT   - 1] = ex_redirect;
  // REDIRECT minus MISPREDICT is the trap-and-MRET term.
  assign hpm_event_c[rv32i_pkg::HPM_EV_MISPREDICT - 1] = !ex_stall && ex_mispredict;
  assign hpm_event_c[rv32i_pkg::HPM_EV_BTB_HIT    - 1] = ex_bp_upd && id_ex_q.pred_hit;
  assign hpm_event_c[rv32i_pkg::HPM_EV_XFER_TAKEN - 1] = ex_bp_upd && ex_ctrl_xfer;

  // The event bus is registered (a measured timing fix: two events derive from
  // the end of the critical path and fan out to 64 flops each).  Every counter
  // lags its event by one cycle; totals are unaffected.
  logic [rv32i_pkg::HPM_EV_COUNT-1:0] hpm_event_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) hpm_event_q <= '0;
    else        hpm_event_q <= hpm_event_c;
  end

  // ---- Zkr's entropy source --------------------------------------------------
  // STUB=1 everywhere but the bitstream keeps the combinational ring
  // oscillator out of every simulation and formal build; entropy_stub_bit is
  // a top-level port so the health tests can be driven from a testbench.
  wire [31:0] seed_rdata;
  wire        seed_rd_en;
  rvntt_seed #(.STUB(ENTROPY_STUB)) u_seed (
      .clk      (clk),
      .rst_n    (rst_n),
      .stub_bit (entropy_stub_bit),
      .rd_en    (seed_rd_en && !ex_trap),
      .rdata    (seed_rdata)
  );

  rvntt_csr u_csr (
      .clk              (clk),
      .rst_n            (rst_n),
      .addr             (id_ex_q.insn[31:20]),
      .wen              (ex_csr_wen),
      .wdata            (ex_csr_wdata),
      .rdata            (ex_csr_rdata),
      .illegal          (ex_csr_illegal),
      // minstret counts instructions that pass EX (the same set as those that
      // reach WB, since every trap resolves in EX), so a `csrr minstret` in
      // the very next instruction reads a complete value -- and not on a
      // stalled cycle, or a 34-cycle divide would count 34 times.
      .instret_bump     (id_ex_q.valid && !ex_trap && !ex_stall),
      .hpm_event        (hpm_event_q),
      .seed_rdata       (seed_rdata),
      .seed_rd_en       (seed_rd_en),
      .trap_en          (ex_trap),
      .trap_pc          (id_ex_q.pc),
      .trap_cause       (ex_trap_cause),
      .trap_val         (ex_trap_val),
      .mret_en          (ex_mret),
      .mtvec_o          (csr_mtvec),
      .mepc_o           (csr_mepc)
  );

  // ---- the bit-manipulation unit ---------------------------------------------
  // Same two operands as the ALU (the immediate carries rori's/bseti's
  // amount); joined at ex_result rather than ex_alu_y, which feeds the jump
  // target and the mispredict comparison.
  logic [31:0] ex_bm_result;
  rvntt_bitmanip u_bitmanip (
      .op (id_ex_q.ctrl.bm_op),
      .a  (ex_alu_a),
      .b  (ex_alu_b),
      .y  (ex_bm_result)
  );

  // The EX-stage result, whatever produced it.  A Zicsr access produces the
  // old CSR value here, so a CSR read is forwarded like an ALU result; the
  // M and B results arrive here too, so result_sel stays RES_ALU and neither
  // result mux needs a new arm.  The M result is only correct on the done
  // cycle, which is the only cycle EX/MEM latches.
  logic [31:0] ex_result;
  always_comb begin
    if      (id_ex_q.ctrl.is_muldiv)  ex_result = ex_md_result;
    else if (id_ex_q.ctrl.is_bitmanip) ex_result = ex_bm_result;
    else if (id_ex_q.ctrl.is_csr)     ex_result = ex_csr_rdata;
    else                              ex_result = ex_alu_y;
  end

  // ---- traps -----------------------------------------------------------------
  // All of them resolve here, on the address the adder just computed.
  logic ex_addr_misaligned;
  always_comb begin
    unique case (id_ex_q.ctrl.mem_op)
      rv32i_pkg::F3_LH, rv32i_pkg::F3_LHU: ex_addr_misaligned = ex_mem_addr[0];
      rv32i_pkg::F3_LW:                    ex_addr_misaligned = |ex_mem_addr[1:0];
      default:                             ex_addr_misaligned = 1'b0;  // byte
    endcase
  end

  // A misaligned target is reported on the branch or jump, with mepc at it
  // and mtval at the target.  Bit 0 is already clear, so bit 1 is the only
  // one that can be set.
  wire ex_target_misaligned = ex_ctrl_xfer && ex_jump_target[1];

  assign ex_mret = id_ex_q.valid && id_ex_q.ctrl.is_mret;

  always_comb begin
    ex_trap       = 1'b0;
    ex_trap_cause = 5'd0;
    ex_trap_val   = 32'h0;
    if (id_ex_q.valid) begin
      if (id_ex_q.ctrl.is_illegal ||
          (id_ex_q.ctrl.is_csr && ex_csr_illegal)) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd2;  ex_trap_val = id_ex_q.insn;
      end else if (id_ex_q.ctrl.is_ecall) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd11; ex_trap_val = 32'h0;
      end else if (id_ex_q.ctrl.is_ebreak) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd3;  ex_trap_val = id_ex_q.pc;
      end else if (ex_target_misaligned) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd0;  ex_trap_val = ex_jump_target;
      end else if (id_ex_q.ctrl.mem_read && ex_addr_misaligned) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = ex_mem_addr;
      end else if (id_ex_q.ctrl.mem_write && ex_addr_misaligned) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd6;  ex_trap_val = ex_mem_addr;
      end
    end
  end

  // Gated on !ex_stall: no multi-cycle instruction redirects today, but a
  // redirect fired while EX was held would flush around an unfinished
  // instruction.  A correctly predicted taken transfer does not redirect; a
  // branch predicted taken that resolves not-taken redirects to pc + 4.
  assign ex_redirect = !ex_stall && (ex_trap || ex_mret || ex_mispredict);

  // The architectural next pc in every case, whether or not a redirect is
  // taken -- RVFI reports it as pc_wdata, and a correctly predicted branch
  // does not redirect.
  always_comb begin
    if      (ex_trap)      ex_redirect_target = csr_mtvec;
    else if (ex_mret)      ex_redirect_target = csr_mepc;
    else if (ex_ctrl_xfer) ex_redirect_target = ex_jump_target;
    else                   ex_redirect_target = ex_pc_plus4;
  end

  // ==========================================================================
  // EX/MEM
  // ==========================================================================
  // A faulting instruction never retires: it is squashed here, which keeps
  // the commit log comparable with Spike (no line for a trapping instruction)
  // and minstret right for free.  Each stalled cycle inserts one bubble.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ex_mem_q <= '0;
    end else if (ex_trap || ex_stall) begin
      ex_mem_q <= '0;
    end else begin
      ex_mem_q.valid      <= id_ex_q.valid;
      ex_mem_q.pc         <= id_ex_q.pc;
      ex_mem_q.insn       <= id_ex_q.insn;
      ex_mem_q.reg_write  <= id_ex_q.ctrl.reg_write;
      ex_mem_q.mem_read   <= id_ex_q.ctrl.mem_read;
      ex_mem_q.mem_write  <= id_ex_q.ctrl.mem_write;
      ex_mem_q.mem_op     <= id_ex_q.ctrl.mem_op;
      ex_mem_q.result_sel <= id_ex_q.ctrl.result_sel;
      ex_mem_q.rd_addr    <= id_ex_q.rd_addr;
      ex_mem_q.ex_result  <= ex_result;
      ex_mem_q.store_data <= ex_rs2_fwd;
      ex_mem_q.pc_plus4   <= ex_pc_plus4;
    end
  end

  // ==========================================================================
  // MEM -- load alignment and sign extension
  // ==========================================================================
  logic [1:0]  mem_byte_off;
  logic [7:0]  mem_byte;
  logic [15:0] mem_half;
  logic [31:0] mem_load_data;

  assign mem_byte_off = ex_mem_q.ex_result[1:0];
  assign mem_byte     = dmem_rdata[8*mem_byte_off +: 8];
  assign mem_half     = mem_byte_off[1] ? dmem_rdata[31:16] : dmem_rdata[15:0];

  always_comb begin
    unique case (ex_mem_q.mem_op)
      rv32i_pkg::F3_LB:  mem_load_data = {{24{mem_byte[7]}},   mem_byte};
      rv32i_pkg::F3_LH:  mem_load_data = {{16{mem_half[15]}},  mem_half};
      rv32i_pkg::F3_LW:  mem_load_data = dmem_rdata;
      rv32i_pkg::F3_LBU: mem_load_data = {24'h0, mem_byte};
      rv32i_pkg::F3_LHU: mem_load_data = {16'h0, mem_half};
      default:           mem_load_data = dmem_rdata;
    endcase
  end

  logic [31:0] mem_result;
  always_comb begin
    unique case (ex_mem_q.result_sel)
      rv32i_pkg::RES_ALU: mem_result = ex_mem_q.ex_result;
      rv32i_pkg::RES_MEM: mem_result = mem_load_data;
      rv32i_pkg::RES_PC4: mem_result = ex_mem_q.pc_plus4;
      // A Zicsr access carries the old CSR value down in ex_result; both arms
      // are listed as different claims about where the value came from.
      rv32i_pkg::RES_CSR: mem_result = ex_mem_q.ex_result;
      default:            mem_result = 32'h0;
    endcase
  end

  // ==========================================================================
  // MEM/WB
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mem_wb_q <= '0;
    end else begin
      mem_wb_q.valid     <= ex_mem_q.valid;
      mem_wb_q.pc        <= ex_mem_q.pc;
      mem_wb_q.insn      <= ex_mem_q.insn;
      mem_wb_q.reg_write <= ex_mem_q.reg_write;
      mem_wb_q.rd_addr   <= ex_mem_q.rd_addr;
      mem_wb_q.wb_data   <= mem_result;
    end
  end

  // ==========================================================================
  // WB -- register write and the retirement trace
  // ==========================================================================
  // x0 is suppressed in the register file, but the trace must agree with
  // Spike, which never reports a write to x0, so it is filtered here too.
  assign wb_we = mem_wb_q.valid && mem_wb_q.reg_write && (mem_wb_q.rd_addr != 5'd0);
  assign wb_wa = mem_wb_q.rd_addr;
  assign wb_wd = mem_wb_q.wb_data;

  assign commit_valid     = mem_wb_q.valid;
  assign commit_pc        = mem_wb_q.pc;
  assign commit_insn      = mem_wb_q.insn;
  assign commit_reg_write = wb_we;
  assign commit_rd        = mem_wb_q.rd_addr;
  assign commit_wdata     = mem_wb_q.wb_data;

  // ---- the capability guard --------------------------------------------------
  // Decoded again at WB (diagnostic-only, costs nothing in simulation).  Only
  // is_illegal is read; the re-decoded register addresses are unused.
  /* verilator lint_off UNUSEDSIGNAL */
  rv32i_pkg::ctrl_t wb_ctrl;
  logic [4:0] wb_rd_unused, wb_rs1_unused, wb_rs2_unused;
  /* verilator lint_on UNUSEDSIGNAL */

  rvntt_decode u_wb_decode (
      .insn     (mem_wb_q.insn),
      .ctrl     (wb_ctrl),
      .rd_addr  (wb_rd_unused),
      .rs1_addr (wb_rs1_unused),
      .rs2_addr (wb_rs2_unused)
  );

  // Unreachable, because an illegal instruction traps in EX and is squashed
  // before MEM; it stays as a live check on the trap path.
  assign dbg_unsupported = mem_wb_q.valid && wb_ctrl.is_illegal;

  // ---- the RVFI port ---------------------------------------------------------
`ifdef RISCV_FORMAL
  rvntt_rvfi u_rvfi (
      .clk                (clk),
      .rst_n              (rst_n),

      .ex_valid           (id_ex_q.valid),
      .ex_trap            (ex_trap),
      .ex_stall           (ex_stall),
      .ex_pc              (id_ex_q.pc),
      .ex_insn            (id_ex_q.insn),
      .ex_redirect_target (ex_redirect_target),
      .ex_uses_rs1        (id_ex_q.ctrl.uses_rs1),
      .ex_uses_rs2        (id_ex_q.ctrl.uses_rs2),
      .ex_rs1_addr        (id_ex_q.rs1_addr),
      .ex_rs2_addr        (id_ex_q.rs2_addr),
      .ex_rs1_fwd         (ex_rs1_fwd),
      .ex_rs2_fwd         (ex_rs2_fwd),
      .ex_mem_read        (id_ex_q.ctrl.mem_read),
      .ex_mem_addr        (ex_mem_addr),
      .ex_dmem_be         (dmem_be),
      .ex_dmem_wdata      (dmem_wdata),

      .mem_dmem_rdata     (dmem_rdata),

      .wb_valid           (mem_wb_q.valid),
      .wb_pc              (mem_wb_q.pc),
      .wb_insn            (mem_wb_q.insn),
      .wb_we              (wb_we),
      .wb_rd_addr         (wb_wa),
      .wb_rd_data         (wb_wd),

      .rvfi_valid         (rvfi_valid),
      .rvfi_order         (rvfi_order),
      .rvfi_insn          (rvfi_insn),
      .rvfi_trap          (rvfi_trap),
      .rvfi_halt          (rvfi_halt),
      .rvfi_intr          (rvfi_intr),
      .rvfi_mode          (rvfi_mode),
      .rvfi_ixl           (rvfi_ixl),
      .rvfi_rs1_addr      (rvfi_rs1_addr),
      .rvfi_rs2_addr      (rvfi_rs2_addr),
      .rvfi_rs1_rdata     (rvfi_rs1_rdata),
      .rvfi_rs2_rdata     (rvfi_rs2_rdata),
      .rvfi_rd_addr       (rvfi_rd_addr),
      .rvfi_rd_wdata      (rvfi_rd_wdata),
      .rvfi_pc_rdata      (rvfi_pc_rdata),
      .rvfi_pc_wdata      (rvfi_pc_wdata),
      .rvfi_mem_addr      (rvfi_mem_addr),
      .rvfi_mem_rmask     (rvfi_mem_rmask),
      .rvfi_mem_wmask     (rvfi_mem_wmask),
      .rvfi_mem_rdata     (rvfi_mem_rdata),
      .rvfi_mem_wdata     (rvfi_mem_wdata)
  );
`endif

  // ---- design assertions, proved by every riscv-formal check at depth 14 ----
  // Guarded by RISCV_FORMAL because that is the only harness that reads the
  // core with assertions enabled.
`ifdef RISCV_FORMAL
  always_comb begin
    // The address adder and the ALU agree for every load and store (a
    // property of the decoder, which gets edited).
    if (id_ex_q.valid && (id_ex_q.ctrl.mem_read || id_ex_q.ctrl.mem_write))
      a_addr_adder_matches_alu: assert (ex_mem_addr == ex_alu_y);

    // ... and for JALR's target.
    if (id_ex_q.valid && id_ex_q.ctrl.jalr)
      a_jalr_target_matches_alu: assert (ex_mem_addr == ex_alu_y);

    // The two stalls are mutually exclusive: id_stall needs a load in EX,
    // ex_stall a multiply or divide.  The load-use counter's guard depends on
    // it, and a future multi-cycle unit that overlaps a load fires this.
    a_stalls_are_disjoint: assert (!(id_stall && ex_stall));

    // On every edge where ID/EX takes a new instruction, EX/MEM takes the EX
    // instruction unsquashed -- the structural half of the ID-stage
    // forwarding argument.
    if (!ex_stall && !id_stall && !ex_redirect)
      a_pipe_advances_together: assert (!ex_trap && !ex_stall);

    // The predictor is never updated during a front-end stall, which is what
    // makes holding its registered answer an identity.
    a_no_bp_update_under_front_stall: assert (!(front_stall && ex_bp_upd));

    // Zkt claim A: ex_stall is the only thing that can extend an
    // instruction's stay in EX, and only the multi-cycle unit asserts it, so
    // every other implemented instruction is one cycle whatever its operands.
    // A new multi-cycle unit breaks this here rather than quietly breaking Zkt.
    if (ex_stall) a_zkt_only_muldiv_stalls: assert (id_ex_q.ctrl.is_muldiv);
  end

  // A second copy of the original EX-stage forwarding computation, compared
  // against the registered one: the claim is that moving the decision to ID
  // changed nothing.  This is what found the decay case above.
  rv32i_pkg::fwd_sel_e f_fwd_a_ex, f_fwd_b_ex;
  rvntt_forward u_forward_ref (
      .ex_rs1_addr   (id_ex_q.rs1_addr),
      .ex_rs2_addr   (id_ex_q.rs2_addr),
      .ex_uses_rs1   (id_ex_q.ctrl.uses_rs1),
      .ex_uses_rs2   (id_ex_q.ctrl.uses_rs2),
      .mem_valid     (ex_mem_q.valid),
      .mem_reg_write (ex_mem_q.reg_write),
      .mem_mem_read  (ex_mem_q.mem_read),
      .mem_rd_addr   (ex_mem_q.rd_addr),
      .wb_valid      (mem_wb_q.valid),
      .wb_reg_write  (mem_wb_q.reg_write),
      .wb_rd_addr    (mem_wb_q.rd_addr),
      .fwd_a         (f_fwd_a_ex),
      .fwd_b         (f_fwd_b_ex)
  );

  always_comb begin
    // Only for an instruction that is really in EX; a bubble's fields are '0.
    if (id_ex_q.valid) begin
      a_fwd_precompute_matches_a: assert (ex_fwd_a == f_fwd_a_ex);
      a_fwd_precompute_matches_b: assert (ex_fwd_b == f_fwd_b_ex);
    end
  end

  // The other half of rvntt_bpred's interface contract, which it assumes: a
  // resolving transfer's pc and target are word-aligned (a pc is aligned or
  // the fetch trapped; a target is aligned or the misaligned-target trap
  // gated upd_valid off).
  always_comb begin
    if (ex_bp_upd) begin
      a_bp_upd_pc_aligned:     assert (id_ex_q.pc[1:0]   == 2'b00);
      // The branch/JAL target adder agrees with the ALU.
      if (!id_ex_q.ctrl.jalr)
        a_pc_target_matches_alu: assert (ex_pc_target == ex_alu_y);
      a_bp_upd_target_aligned: assert (ex_jump_target[1:0] == 2'b00 ||
                                       !ex_ctrl_xfer);
    end
  end
`endif

endmodule

`default_nettype wire
