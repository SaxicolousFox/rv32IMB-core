// ============================================================================
// rvntt_core -- the 5-stage RV32I pipeline.
//
// Plan A4's bring-up strategy was to build the datapath with no forwarding, no
// stalls and no flushes, verify it against hand-scheduled NOP-padded code, and
// only then add each hazard layer.  A6 adds the first of those layers, so the
// remaining omissions are still the design rather than a to-do list:
//
//   * FORWARDING (A6) is present: EX/MEM -> EX and MEM/WB -> EX for both
//     operands, the store-data operand included.  A RAW dependency at any
//     distance now reads the right value.
//   * THE LOAD-USE INTERLOCK (A7) is present: a load's result is deliberately
//     not a forwarding source from MEM (rvntt_forward.sv explains why), so a
//     consumer one slot behind a load stalls for one cycle and then takes the
//     value from FWD_WB.
//   * CONTROL FLOW (A8) is present: branches resolve in EX, and a taken branch
//     or a jump redirects the PC and squashes the two younger instructions
//     already in flight.  The penalty is therefore two cycles, always.
//   * CSRs, TRAPS AND MRET (A9) are present.  See "the trap invariant" below.
//
// The only thing left unimplemented is the Xkntt coprocessor: the decoder
// recognises the extension but no stage executes it, so `dbg_unsupported`
// pulses if one ever retires and the testbench treats that as a failure.  It
// also still watches for a retiring ILLEGAL instruction -- which A9 should make
// impossible, since illegal instructions now trap, and which is therefore no
// longer a footnote but a live check that trapping works.
//
// THE TRAP INVARIANT: EVERY TRAP RESOLVES IN EX, so an instruction that reaches
// MEM is guaranteed to retire.  That is not an accident of the current
// exception set -- the misaligned-address check had to be placed in EX, where
// the address is computed, rather than in MEM where the access happens, to keep
// it true.  Two things depend on it and would break silently without it: the
// CSR file's in-flight `minstret` adjustment (rvntt_csr.sv), and the fact that
// nothing older than EX ever has to be squashed.
//
// MEMORY TIMING.  rvntt_ram registers each port's address, so its output
// register serves as a pipeline register (see that file's header).  Port B's
// address is therefore driven from the COMBINATIONAL ALU result in EX, not from
// the EX/MEM register, which is what makes load data available during MEM
// rather than WB.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
// ============================================================================
`default_nettype none

module rvntt_core #(
    parameter logic [31:0] RESET_PC = 32'h8000_0000
) (
    input  wire         clk,
    input  wire         rst_n,

    // Instruction port (rvntt_ram port A).  Address out, data back next cycle.
    output logic [31:0] imem_addr,
    input  wire  [31:0] imem_rdata,

    // Data port (rvntt_ram port B).
    output logic [31:0] dmem_addr,
    output logic [31:0] dmem_wdata,
    output logic [3:0]  dmem_be,
    input  wire  [31:0] dmem_rdata,

    // Retirement trace.  This is A5's commit-log source and A11's future RVFI
    // port; it is also how the A4 testbench observes architectural state
    // without reaching into the register file.
    output logic        commit_valid,
    output logic [31:0] commit_pc,
    output logic [31:0] commit_insn,
    output logic        commit_reg_write,
    output logic [4:0]  commit_rd,
    output logic [31:0] commit_wdata,

    // Pulses with commit_valid when the retiring instruction is one this core
    // cannot execute faithfully.  See the header.
    output logic        dbg_unsupported
);

  // ==========================================================================
  // Pipeline registers
  // ==========================================================================
  // Declared together and ahead of the stages, rather than each inside the
  // stage that writes it, because A6's forwarding makes EX read ex_mem_q and
  // mem_wb_q -- registers written by stages that appear below EX in this file.
  // Neither Verilator nor Yosys accepts a reference above a declaration.
  //
  // UNUSEDSIGNAL on parts of id_ex_q and ex_mem_q is expected: rs3_addr and
  // rs3_data feed the Xkntt R4 operands, and ex_mem_q carries mem_write and
  // store_data for a store buffer this pipeline does not have.  The fields are
  // in the structs because the pipeline registers are defined once, in the
  // package, for the finished design -- not trimmed to whatever the current
  // step happens to read.  Scoped to the declarations so UNUSEDSIGNAL stays
  // live everywhere else.
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
  logic        stall;                      // driven by rvntt_hazard, in ID
  logic        ex_redirect;                // a control transfer, MRET or a trap
  logic [31:0] ex_redirect_target;
  logic [31:0] ex_jump_target;             // branch/JAL/JALR only
  logic        ex_trap;                    // the instruction in EX faults
  logic [4:0]  ex_trap_cause;
  logic [31:0] ex_trap_val;
  logic        ex_mret;

  // A REDIRECT AND A STALL CANNOT COINCIDE.  Both are properties of the single
  // instruction in EX: `stall` needs it to be a load, `ex_redirect` needs it to
  // be a taken branch, a jump, an MRET or a faulting instruction, and no
  // instruction is more than one of those.  The priority is still written down
  // rather than left to chance, because "these are mutually exclusive" is
  // exactly the kind of reasoning that stops being true when a later step adds
  // another case.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)           pc_q <= RESET_PC;
    else if (ex_redirect) pc_q <= ex_redirect_target;
    else if (stall)       pc_q <= pc_q;
    else                  pc_q <= pc_q + 32'd4;
  end

  assign imem_addr = pc_q;

  // ==========================================================================
  // IF/ID
  // ==========================================================================
  // The instruction itself is NOT flopped here: it arrives from the RAM's own
  // output register, which is the IF/ID insn register.  Flopping imem_rdata
  // again would add a stage.
  //
  // THAT IS WHY A STALL NEEDS A HOLDING REGISTER (A7).  Holding pc_q and
  // if_id_q is not enough: the RAM's output register has already been loaded
  // with the address that was on imem_addr during the stalled cycle, so on the
  // next cycle imem_rdata is the instruction AFTER the one ID is still holding,
  // and ID would carry a pc and an insn that do not belong together.  Rewinding
  // pc_q instead would cost two cycles per stall, not one, because the re-fetch
  // takes a cycle of its own.  So the word is captured on the way into the
  // stall and replayed while it lasts.  Capturing if_id.insn -- the already
  // muxed value -- rather than imem_rdata is what makes a multi-cycle stall
  // work, which A9's traps and the coprocessor's kntt.wait will need.
  logic [31:0] insn_hold_q;
  logic        insn_held_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      insn_hold_q <= 32'h0;
      insn_held_q <= 1'b0;
    end else begin
      insn_hold_q <= if_id.insn;
      // Not replayed across a redirect: the held word belongs to the path that
      // is being discarded.  A stall and a redirect cannot actually coincide
      // (see the PC mux), so this AND is defence rather than function -- but it
      // is one gate against having to re-derive that argument later.
      insn_held_q <= stall && !ex_redirect;
    end
  end

  // The flush kills BOTH younger slots.  A redirect resolved in EX has two
  // instructions behind it -- one in ID, one whose fetch is in flight -- and
  // squashing only ID/EX leaves the second one to execute from the wrong path.
  // The directed test puts a taken branch immediately behind a taken branch for
  // exactly that reason: with a one-slot flush the second one redirects too,
  // and the program ends up somewhere it was never meant to go.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      if_id_q.valid <= 1'b0;
      if_id_q.pc    <= RESET_PC;
      if_id_q.insn  <= 32'h0;
    end else if (ex_redirect) begin
      if_id_q.valid <= 1'b0;
      if_id_q.pc    <= pc_q;
      if_id_q.insn  <= 32'h0;
    end else if (!stall) begin
      if_id_q.valid <= 1'b1;
      if_id_q.pc    <= pc_q;
      if_id_q.insn  <= 32'h0;              // unused; insn comes from the RAM
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
  logic [4:0]  id_rd, id_rs1, id_rs2, id_rs3;
  logic [31:0] id_imm;
  logic [31:0] id_rs1_data, id_rs2_data, id_rs3_data;

  rvntt_decode u_decode (
      .insn     (if_id.insn),
      .ctrl     (id_ctrl),
      .rd_addr  (id_rd),
      .rs1_addr (id_rs1),
      .rs2_addr (id_rs2),
      .rs3_addr (id_rs3)
  );

  rvntt_immgen u_immgen (
      .insn (if_id.insn),
      .fmt  (id_ctrl.imm_fmt),
      .imm  (id_imm)
  );

  // Declared here so the WB stage's write signals can be referenced; the
  // regfile is instantiated below, after WB is defined.
  logic        wb_we;
  logic [4:0]  wb_wa;
  logic [31:0] wb_wd;

  rvntt_regfile u_regfile (
      .clk (clk),
      .ra1 (id_rs1), .ra2 (id_rs2), .ra3 (id_rs3),
      .rd1 (id_rs1_data), .rd2 (id_rs2_data), .rd3 (id_rs3_data),
      .we  (wb_we), .wa (wb_wa), .wd (wb_wd)
  );

  // ---- the load-use interlock (A7) ----------------------------------------
  // In ID, comparing the instruction being decoded against the one already in
  // EX.  Its `stall` output holds IF and ID and turns the ID/EX register into a
  // bubble, which is the whole of the mechanism.
  rvntt_hazard u_hazard (
      .id_valid    (if_id.valid),
      .id_uses_rs1 (id_ctrl.uses_rs1),
      .id_uses_rs2 (id_ctrl.uses_rs2),
      .id_rs1_addr (id_rs1),
      .id_rs2_addr (id_rs2),
      .ex_valid    (id_ex_q.valid),
      .ex_mem_read (id_ex_q.ctrl.mem_read),
      .ex_rd_addr  (id_ex_q.rd_addr),
      .stall       (stall)
  );

  // ==========================================================================
  // ID/EX
  // ==========================================================================
  // The bubble is a WHOLE-STRUCT clear, not just valid <= 0.  Clearing valid
  // alone would leave mem_write set in ctrl, and the store path in EX is gated
  // on `id_ex_q.valid && ctrl.mem_write` today -- one gate away from a bubble
  // writing memory.  Zeroing the struct means the bubble cannot do anything at
  // all whatever a later stage forgets to check.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      id_ex_q <= '0;
    end else if (stall || ex_redirect) begin
      id_ex_q <= '0;
    end else begin
      id_ex_q.valid    <= if_id.valid;
      id_ex_q.pc       <= if_id.pc;
      id_ex_q.insn     <= if_id.insn;
      id_ex_q.ctrl     <= id_ctrl;
      id_ex_q.imm      <= id_imm;
      id_ex_q.rs1_addr <= id_rs1;
      id_ex_q.rs2_addr <= id_rs2;
      id_ex_q.rs3_addr <= id_rs3;
      id_ex_q.rd_addr  <= id_rd;
      id_ex_q.rs1_data <= id_rs1_data;
      id_ex_q.rs2_data <= id_rs2_data;
      id_ex_q.rs3_data <= id_rs3_data;
    end
  end

  // ==========================================================================
  // EX -- forwarding, then the ALU
  // ==========================================================================
  // The forwarding muxes come FIRST, and everything in EX that reads a register
  // operand reads their output: the ALU's two sources, and the store-data path.
  // The store-data operand is the one that gets forgotten -- it does not go
  // through the ALU, so a testbench that only checks arithmetic never notices
  // that `sw` wrote a stale value.  Plan A7 names it explicitly for that
  // reason, and there is exactly one rs2 signal in this stage so it cannot be
  // half-fixed.
  rv32i_pkg::fwd_sel_e ex_fwd_a, ex_fwd_b;
  logic [31:0] ex_rs1_fwd, ex_rs2_fwd;

  // The MEM stage's forwardable value.  NOT `mem_result`, which includes the
  // load path: this mux is only over the two sources that are already
  // registered, so the forwarding network can never put the BRAM output on the
  // ALU's input path.  See rvntt_forward.sv.
  logic [31:0] ex_mem_fwd_data;
  always_comb begin
    ex_mem_fwd_data = (ex_mem_q.result_sel == rv32i_pkg::RES_PC4)
                      ? ex_mem_q.pc_plus4 : ex_mem_q.ex_result;
  end

  rvntt_forward u_forward (
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
      .fwd_a         (ex_fwd_a),
      .fwd_b         (ex_fwd_b)
  );

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

  // ---- data-memory request, issued from EX so the RAM's address register is
  // ---- the EX/MEM address register and rdata is valid during MEM.
  logic [1:0] ex_byte_off;
  assign ex_byte_off = ex_alu_y[1:0];

  always_comb begin
    dmem_wdata = ex_rs2_fwd;
    dmem_be    = 4'b0000;
    // `!ex_trap` is what makes a misaligned store harmless: the check runs on
    // the address the ALU produced THIS cycle, so the write is suppressed
    // before the RAM's address register ever latches it.  A trap detected in
    // MEM instead would be a cycle too late.
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

  assign dmem_addr = ex_alu_y;

  // ---- control transfer (A8) ----------------------------------------------
  // The ALU has already computed the target for all three shapes: pc + imm for
  // branches and JAL (SRCA_PC), rs1 + imm for JALR (SRCA_RS1).  So the only
  // work left here is the condition and JALR's bit-0 rule.
  //
  // The comparator reads the FORWARDED operands, not id_ex_q.rs1_data.  A
  // branch on a value computed by the instruction immediately ahead of it is
  // ordinary code -- `sub` then `beqz` is how every compiler writes a
  // comparison -- and reading the register file there takes the wrong direction
  // silently.
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

  // JALR clears bit 0 of the computed target; JAL and branches do not need it,
  // because the B and J immediates encode bit 0 as zero and the pc is aligned.
  // The rule is applied where the spec states it rather than folded into a
  // blanket `& ~1`, so a target that is misaligned for some other reason stays
  // misaligned -- which is exactly what the A9 misaligned-fetch trap below has
  // to be able to see.
  assign ex_jump_target = id_ex_q.ctrl.jalr ? {ex_alu_y[31:1], 1'b0} : ex_alu_y;

  // ---- Zicsr access (A9) ---------------------------------------------------
  // funct3 comes from the instruction word, as it does for the branch
  // comparator and for the same reason (rvntt_branch.sv's header).  Its two low
  // bits select the operation and its top bit selects the immediate form:
  //   01 = CSRRW/CSRRWI   10 = CSRRS/CSRRSI   11 = CSRRC/CSRRCI
  wire [1:0]  ex_csr_op  = id_ex_q.insn[13:12];
  wire        ex_csr_imm = id_ex_q.insn[14];

  // The immediate form's uimm arrives through the immediate generator as IMM_Z
  // rather than being re-sliced out of the instruction here: immgen is already
  // proved, and one source for a field is one place to be wrong.
  wire [31:0] ex_csr_src = ex_csr_imm ? id_ex_q.imm : ex_rs1_fwd;

  // "Does this instruction WRITE the CSR" is not the same question as "is it a
  // CSR instruction", and the difference is the whole of plan A9's directed
  // test: CSRRS/CSRRC with a zero source must not write at all, which is what
  // makes `csrr rd, <read-only csr>` legal.  CSRRW always writes, even with a
  // zero source.
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

  rvntt_csr u_csr (
      .clk              (clk),
      .rst_n            (rst_n),
      .addr             (id_ex_q.insn[31:20]),
      .wen              (ex_csr_wen),
      .wdata            (ex_csr_wdata),
      .rdata            (ex_csr_rdata),
      .illegal          (ex_csr_illegal),
      // minstret counts instructions that PASS EX, not ones that reach WB.
      // The two are the same set -- every trap resolves in EX -- and counting
      // here is what lets a `csrr minstret` in the very next instruction read a
      // complete value.  See rvntt_csr.sv's header for what goes wrong
      // otherwise; riscv-tests' instret_overflow found it.
      .instret_bump     (id_ex_q.valid && !ex_trap),
      .trap_en          (ex_trap),
      .trap_pc          (id_ex_q.pc),
      .trap_cause       (ex_trap_cause),
      .trap_val         (ex_trap_val),
      .mret_en          (ex_mret),
      .mtvec_o          (csr_mtvec),
      .mepc_o           (csr_mepc)
  );

  // The EX-stage result.  A Zicsr access produces the OLD CSR value here, which
  // is why the pipeline register field is named ex_result rather than
  // alu_result -- and why a CSR read is forwarded to the next instruction for
  // free, through exactly the same path an ALU result takes.
  logic [31:0] ex_result;
  assign ex_result = id_ex_q.ctrl.is_csr ? ex_csr_rdata : ex_alu_y;

  // ---- traps (A9) ----------------------------------------------------------
  // ALL OF THEM RESOLVE HERE.  The misaligned-address check in particular is
  // done on the address the ALU just computed rather than in MEM where the
  // access lands, so that the faulting store can be suppressed before the RAM
  // ever sees it and so that nothing older than EX is ever squashed.
  logic ex_addr_misaligned;
  always_comb begin
    unique case (id_ex_q.ctrl.mem_op)
      rv32i_pkg::F3_LH, rv32i_pkg::F3_LHU: ex_addr_misaligned = ex_alu_y[0];
      rv32i_pkg::F3_LW:                    ex_addr_misaligned = |ex_alu_y[1:0];
      default:                             ex_addr_misaligned = 1'b0;  // byte
    endcase
  end

  // A misaligned target is reported ON THE BRANCH OR JUMP, with mepc pointing
  // at it and mtval at the target -- which is what the privileged spec asks for
  // and, conveniently, the only thing a machine that resolves branches in EX
  // can do.  Bit 0 is already cleared for JALR and is zero by encoding for B
  // and J, so bit 1 is the only one that can be set.
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
        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = ex_alu_y;
      end else if (id_ex_q.ctrl.mem_write && ex_addr_misaligned) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd6;  ex_trap_val = ex_alu_y;
      end
    end
  end

  assign ex_redirect = ex_trap || ex_mret || ex_ctrl_xfer;

  always_comb begin
    if      (ex_trap) ex_redirect_target = csr_mtvec;
    else if (ex_mret) ex_redirect_target = csr_mepc;
    else              ex_redirect_target = ex_jump_target;
  end

  // ==========================================================================
  // EX/MEM
  // ==========================================================================
  // A FAULTING INSTRUCTION NEVER RETIRES.  Squashing it here rather than
  // letting it through with a "trapped" flag is what keeps the commit log
  // comparable with Spike, which prints no line at all for an instruction that
  // traps -- and it is what keeps minstret right for free.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ex_mem_q <= '0;
    end else if (ex_trap) begin
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
      ex_mem_q.pc_plus4   <= id_ex_q.pc + 32'd4;
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
      // A Zicsr access carries the OLD CSR value down in ex_result, so this
      // arm and RES_ALU select the same field.  Both are listed anyway: they
      // are different claims about where the value came from, and collapsing
      // them would make the next producer of ex_result harder to add.
      rv32i_pkg::RES_CSR: mem_result = ex_mem_q.ex_result;
      // RES_XKNTT is the coprocessor's and does not exist yet;
      // dbg_unsupported fires if one ever retires here.
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
  // x0 is suppressed in the register file itself, but the trace must agree with
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

  // ---- the capability guard ------------------------------------------------
  // There is no commit_is_ecall port any more.  Before A9 the ECALL was the
  // testbench's stop marker, retiring like any other instruction; now it TRAPS
  // and is squashed in EX, so the signal could never assert again.  The
  // testbench stops on a store to `tohost`, or on reaching a nominated pc --
  // both of which are what riscv-tests uses and what Spike's own trace shows.
  //
  // Decoded again at WB rather than piped down: this is diagnostic-only logic,
  // and re-decoding the retiring word costs nothing in simulation while keeping
  // three more fields out of every pipeline register.
  // Only the capability-relevant bits of wb_ctrl are read, and the re-decoded
  // register addresses are not read at all -- they come from the pipeline
  // register.  Both are expected here.
  /* verilator lint_off UNUSEDSIGNAL */
  rv32i_pkg::ctrl_t wb_ctrl;
  logic [4:0] wb_rd_unused, wb_rs1_unused, wb_rs2_unused, wb_rs3_unused;
  /* verilator lint_on UNUSEDSIGNAL */

  rvntt_decode u_wb_decode (
      .insn     (mem_wb_q.insn),
      .ctrl     (wb_ctrl),
      .rd_addr  (wb_rd_unused),
      .rs1_addr (wb_rs1_unused),
      .rs2_addr (wb_rs2_unused),
      .rs3_addr (wb_rs3_unused)
  );

  // Two things, and only one of them is a capability statement any more.
  //
  //   is_xkntt -- the decoder recognises the extension, no stage executes it.
  //   is_illegal -- A9 makes this UNREACHABLE, because an illegal instruction
  //     traps in EX and is squashed before MEM.  It stays because that makes it
  //     a live check on the trap path rather than a leftover: if the illegal
  //     trap were ever lost, an illegal instruction would retire and this would
  //     say so, instead of the program quietly computing with a decoded zero.
  assign dbg_unsupported =
      mem_wb_q.valid && (wb_ctrl.is_illegal || wb_ctrl.is_xkntt);

endmodule

`default_nettype wire
