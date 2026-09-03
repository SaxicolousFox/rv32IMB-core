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
//   * THE MULTI-CYCLE EX MECHANISM (A14, MODS_A) is present: an EX-resident
//     functional unit holds the instruction in EX for as many cycles as it
//     needs, the front end and ID/EX hold, and EX/MEM takes a bubble per
//     stalled cycle.  The M extension is its first user; plan §8 I1's Xkntt
//     Tier-1 unit is meant to be its second, which is why the handshake is
//     generic and the latency is a property of the unit.
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

    // A11's RVFI port.  Behind an ifdef because it is a verification interface
    // with no architectural function: nothing outside a riscv-formal check ever
    // reads it, and 200-odd flops of shadow pipeline have no business in a
    // bitstream.  The commit trace above stays exactly as it is -- RVFI is a
    // second, parallel report, not a replacement (rvntt_rvfi.sv explains why the
    // two cannot be the same signal).
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
  // TWO STALLS, AND THEY DO DIFFERENT THINGS TO DIFFERENT REGISTERS.
  //
  //   id_stall   -- A7's load-use interlock.  The consumer is in ID and has to
  //                 wait, so ID/EX takes a BUBBLE and the front end holds.
  //   ex_stall   -- A14's multi-cycle EX.  The producer is in EX and has not
  //                 finished, so ID/EX HOLDS ITS CONTENTS and EX/MEM takes the
  //                 bubble instead.  The front end holds for this one too.
  //
  // They are not variants of one signal: id_stall empties EX, ex_stall
  // preserves it, and getting that backwards either loses the multi-cycle
  // instruction or executes it twice.  ex_stall has priority on ID/EX, which
  // is why it is tested first there.
  logic        id_stall;                   // driven by rvntt_hazard, in ID
  logic        ex_stall;                   // driven by an EX functional unit
  wire         front_stall = id_stall || ex_stall;
  // A19 LEVER: ex_redirect drives 244 loads -- every pipeline register's clear --
  // and at 74.0 MHz its net alone was 1.491 ns, the biggest single hop on the
  // critical path.  A17 tried the same attribute on ex_trap and got nothing,
  // because that net's own route was 5% of the path; this one's is 11%.  The
  // attribute is a synthesis directive and cannot change what the design
  // computes.
  (* max_fanout = 48 *)
  logic        ex_redirect;                // a control transfer, MRET or a trap
  logic [31:0] ex_redirect_target;
  logic [31:0] ex_jump_target;             // branch/JAL/JALR only
  // A17 LEVER 2 WAS TRIED HERE AND REJECTED.  `(* max_fanout = 32 *)` on this
  // net -- MODS_A A17's second lever, expecting 0.3-0.5 ns -- moved timing the
  // wrong way by 0.263 ns at the constraint lever 1 had just met.  That is
  // inside the +/-0.4 ns placement spread, so the honest reading is "no
  // measurable gain", not "it hurt"; either way there is nothing to adopt.
  // See docs/a17-fmax.md for the run.  `ex_trap` still drives 142 loads.
  logic        ex_trap;                    // the instruction in EX faults
  logic [4:0]  ex_trap_cause;
  logic [31:0] ex_trap_val;
  logic        ex_mret;

  // A19.  bp_pred_* come OUT of the predictor and steer the PC mux; ex_bp_*
  // go INTO it from the EX stage's resolution.
  logic        bp_pred_taken;
  logic [31:0] bp_pred_target;
  logic        bp_pred_hit;               // A20, observational only
  logic        ex_bp_upd;
  logic [1:0]  ex_bp_kind;

  // A REDIRECT AND A STALL CANNOT COINCIDE.  Both are properties of the single
  // instruction in EX: `id_stall` needs the EX instruction to be a load,
  // `ex_stall` needs it to be a multi-cycle one, `ex_redirect` needs it to be a
  // taken branch, a jump, an MRET or a faulting instruction, and no instruction
  // is more than one of those.  A14 made that argument load-bearing rather than
  // decorative, so ex_redirect is now GATED on !ex_stall below instead of being
  // left to the priority here -- see the redirect assignment.
  // A19 puts the predictor in front of this mux and nothing else changes about
  // it.  `pc_next` is broken out because the PREDICTOR IS LOOKED UP WITH IT,
  // one address ahead of the fetch: the prediction registered from pc_next this
  // cycle describes pc_q next cycle, so all that is left in the PC register's
  // own path is the 2:1 mux below.  See docs/a19-bpred-spec.md section 2 --
  // done the other way round, the array read and the tag compare land in the
  // PC path and A17's margin goes straight back out.
  //
  // The redirect outranks the prediction because EX has seen the instruction
  // and IF has only seen its address.  Under front_stall the address does not
  // move, so the lookup simply repeats and re-registers the same answer.
  logic [31:0] pc_next;
  always_comb begin
    if      (ex_redirect)   pc_next = ex_redirect_target;
    else if (front_stall)   pc_next = pc_q;
    else if (bp_pred_taken) pc_next = bp_pred_target;
    else                    pc_next = pc_q + 32'd4;
  end

  // THE PREDICTOR IS NOT LOOKED UP WITH pc_next, AND THIS IS THE WHOLE OF A19's
  // TIMING STORY.  pc_next contains ex_redirect_target, which is the ALU's own
  // output; indexing a 256-entry RAM with it and comparing a tag put the
  // forwarding mux, the full ALU carry chain and the array read in ONE cycle.
  // Measured: 16.058 ns and 24 logic levels, against A17's 11.562 -- the design
  // failed 80 MHz by 3.886 ns.  Registering the prediction, which the
  // specification called for and which is done, was necessary and nowhere near
  // sufficient.
  //
  // So the lookup reads only REGISTERED sources.  The consequence is that the
  // instruction at a redirect target cannot be predicted -- the predictor was
  // looking somewhere else during the cycle the redirect fired -- and
  // `bp_flush` below tells the predictor to say so rather than answer about the
  // wrong address.  One unpredicted instruction per redirect, and the entire EX
  // datapath leaves the fetch path.
  wire [31:0] bp_lookup_pc = front_stall  ? pc_q
                           : bp_pred_taken ? bp_pred_target
                           :                 pc_q + 32'd4;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) pc_q <= RESET_PC;
    else        pc_q <= pc_next;
  end

  rvntt_bpred u_bpred (
      .clk         (clk),
      .rst_n       (rst_n),
      .lookup_pc   (bp_lookup_pc),
      .flush       (ex_redirect),
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
      insn_held_q <= front_stall && !ex_redirect;
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
      // bp_pred_* describe pc_q, the address being fetched THIS cycle, and
      // if_id_q.pc is loaded with that same pc_q -- so the prediction and the
      // instruction it is about arrive together.
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
      .stall       (id_stall)
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
    end else if (ex_stall) begin
      // A14: the instruction in EX has not finished.  HOLD, do not bubble --
      // this register IS the multi-cycle unit's instruction, and clearing it
      // would drop the operation on the floor while the unit kept running.
      // Tested before id_stall because both can be true only if the EX
      // instruction were a load AND multi-cycle, which no instruction is; the
      // order is written down so that stops being an argument and starts being
      // a rule.
      id_ex_q <= id_ex_q;
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
      id_ex_q.rs3_addr <= id_rs3;
      id_ex_q.rd_addr  <= id_rd;
      id_ex_q.rs1_data <= id_rs1_data;
      id_ex_q.rs2_data <= id_rs2_data;
      id_ex_q.rs3_data <= id_rs3_data;
      id_ex_q.pred_taken  <= if_id.pred_taken;
      id_ex_q.pred_target <= if_id.pred_target;
      id_ex_q.pred_hit    <= if_id.pred_hit;
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
  // load path: this mux is only over sources that are already registered, so
  // the forwarding network can never put the BRAM output on the ALU's input
  // path.  See rvntt_forward.sv.
  //
  // BUT IT MUST AGREE WITH `mem_result` FOR EVERY result_sel FWD_MEM CAN
  // SELECT, and the first version did not.  It was written as "pc_plus4 for
  // RES_PC4, ex_result for everything else", while mem_result's own case sends
  // RES_XKNTT to its `default: 32'h0` arm -- so a legal Xkntt instruction
  // forwarded its ALU output to the next instruction while writing zero to the
  // register file.  No RV32I test can reach it, because the only instruction
  // class that disagrees is the one no stage executes; riscv-formal's `reg`
  // check found it in seven seconds from an unconstrained instruction stream.
  // The two case statements are now written the same way round so the next
  // result_sel cannot be added to one and forgotten in the other.
  logic [31:0] ex_mem_fwd_data;
  always_comb begin
    unique case (ex_mem_q.result_sel)
      rv32i_pkg::RES_PC4: ex_mem_fwd_data = ex_mem_q.pc_plus4;
      rv32i_pkg::RES_ALU,
      rv32i_pkg::RES_CSR: ex_mem_fwd_data = ex_mem_q.ex_result;
      // RES_XKNTT, the unassigned encodings, and RES_MEM -- which is
      // unreachable here, because rvntt_forward excludes loads from FWD_MEM.
      // Zero, because that is what mem_result writes back for all of them.
      default:            ex_mem_fwd_data = 32'h0;
    endcase
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

  // ---- the multi-cycle EX unit (A14) --------------------------------------
  // Deliberately written as a GENERIC handshake and not as "the multiplier":
  // plan §8 I1 needs exactly this mechanism for the Xkntt Tier-1 unit, and the
  // reason M is built first is that it arrives with RISCOF, riscv-formal and
  // rv32um as external references, which a custom extension does not have.
  // The three lines below are the whole of the core's side of it.
  //
  // OPERANDS ARE THE FORWARDED ONES, and the unit latches them on its first
  // cycle rather than reading them continuously -- see rvntt_muldiv.sv, which
  // explains why a unit that re-reads them silently computes with values that
  // are stale by two instructions.
  //
  // NO MULTI-CYCLE UNIT MAY ALSO BE A MEMORY OPERATION.  The store path below
  // is gated on ctrl.mem_write and NOT on !ex_stall, because that gate would
  // sit on the design's critical path (EX/MEM rd_addr -> forwarding mux -> ALU
  // -> byte enables -> BRAM WEA; see rtl/soc/CLAUDE.md).  A decoder that ever
  // set both mem_write and a multi-cycle unit would replay the store once per
  // stall cycle.  a_muldiv_not_memory below asserts it cannot.
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

  // ---- A17: the dedicated address adder ------------------------------------
  // A data address is ALWAYS rs1 + imm.  It is never a shift, an AND or a SUB,
  // and the decoder enforces that: every load and store sets alu_op = ALU_ADD,
  // alu_src_a = SRCA_RS1 and alu_src_b = SRCB_IMM.  So ex_mem_addr below and
  // ex_alu_y are the same number for exactly the instructions that use it --
  // a_addr_adder_matches_alu proves it rather than trusting the sentence.
  //
  // WHY A SECOND ADDER IS WORTH ITS AREA.  Taking the address off ex_alu_y put
  // the ALU's four-level operation-select mux in front of the alignment check,
  // which gates the trap, which gates the byte enables, which reach the BRAM's
  // write enable.  A12 measured that mux at 33% of the critical path.  This
  // adder is not on the ALU's result path at all, and the alignment check now
  // needs only the bottom two bits of a sum -- no carry chain in front of it.
  //
  // It costs a 32-bit adder that is idle for most instructions.  At 3.3%
  // utilisation that is the cheapest thing in the design to spend.
  wire [31:0] ex_mem_addr = ex_rs1_fwd + id_ex_q.imm;

  // ---- data-memory request, issued from EX so the RAM's address register is
  // ---- the EX/MEM address register and rdata is valid during MEM.
  logic [1:0] ex_byte_off;
  assign ex_byte_off = ex_mem_addr[1:0];

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

  assign dmem_addr = ex_mem_addr;

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
  // A19: THE BRANCH AND JAL TARGET DOES NOT NEED THE ALU.  Both its operands
  // are registered -- pc and imm -- so a dedicated adder produces it a full ALU
  // result-mux earlier, and the mispredict comparison that feeds ex_redirect
  // starts that much earlier with it.  Same trick as A17's address adder and
  // for the same reason: the ALU's four-level operation-select mux is in front
  // of something that only ever wanted a sum.
  //
  // JALR is left on the ALU: its target genuinely depends on a register, which
  // is the whole difference between it and the other two shapes.
  wire [31:0] ex_pc_target = id_ex_q.pc + id_ex_q.imm;

  assign ex_jump_target = id_ex_q.ctrl.jalr ? {ex_alu_y[31:1], 1'b0}
                                            : ex_pc_target;

  // ---- A19: checking the prediction, and telling the predictor -------------
  // The predictor's classification is NOT the decoder's.  The decoder knows
  // branch/JAL/JALR; the predictor needs to know whether a target comes from
  // the BTB or from the return stack, which is a question about rd and rs1.
  // docs/a19-bpred-spec.md section 4 has the table, including the one case it
  // knowingly gets wrong -- link(rd) && link(rs1), the spec's "pop then push",
  // which is classified CALL here and mispredicts its return.
  wire ex_rd_link  = (id_ex_q.rd_addr  == 5'd1) || (id_ex_q.rd_addr  == 5'd5);
  wire ex_rs1_link = (id_ex_q.rs1_addr == 5'd1) || (id_ex_q.rs1_addr == 5'd5);

  always_comb begin
    if      (id_ex_q.ctrl.branch)             ex_bp_kind = rv32i_pkg::BP_BRANCH;
    else if (id_ex_q.ctrl.jalr && ex_rs1_link
             && !ex_rd_link)                  ex_bp_kind = rv32i_pkg::BP_RET;
    else if (ex_rd_link)                      ex_bp_kind = rv32i_pkg::BP_CALL;
    else                                      ex_bp_kind = rv32i_pkg::BP_JUMP;
  end

  // A TRAPPING INSTRUCTION TEACHES THE PREDICTOR NOTHING.  It did not transfer
  // control to its target; it transferred to mtvec, and recording that would
  // make the BTB predict the trap vector for a branch that will not trap next
  // time.  Nothing younger than EX can be flushed out from under this, because
  // a flush only reaches IF/ID and ID/EX -- the instruction in EX is always the
  // real one.
  assign ex_bp_upd = id_ex_q.valid && !ex_stall && !ex_trap &&
                     (id_ex_q.ctrl.branch || id_ex_q.ctrl.jump);

  // The prediction was right when it agreed about BOTH the direction and, if
  // taken, the address.  A prediction of taken on an instruction that is not a
  // control transfer at all cannot happen -- index and tag together are the
  // whole word address, so a hit is the same instruction that allocated the
  // entry -- but it needs no special case either: ex_ctrl_xfer is 0, the
  // comparison fails, and the redirect goes to pc + 4, which is correct.
  wire ex_mispredict = id_ex_q.valid &&
                       ((id_ex_q.pred_taken != ex_ctrl_xfer) ||
                        (ex_ctrl_xfer &&
                         (id_ex_q.pred_target != ex_jump_target)));

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

  // ---- A20: the Zihpm event bus -------------------------------------------
  // The bit positions are rv32i_pkg::HPM_EV_* minus one -- event 0 is "count
  // nothing" and has no wire.
  //
  // THESE PREDICATES ARE A18's, DELIBERATELY AND EXACTLY, so that the hardware
  // counters and tb/perf/tb_profile.cpp can be required to agree TO THE COUNT.
  //
  // `&& !ex_stall` on the load-use event mirrors the instrument's `else if`,
  // which charges a cycle where both stalls assert to the multi-cycle unit.
  // MEASURED: THERE IS NO SUCH CYCLE.  id_stall requires a load in EX and
  // ex_stall requires a multiply or divide there, so the two are disjoint by
  // construction -- a_stalls_are_disjoint proves it at depth 14, and dropping
  // the guard was injected and changed no count anywhere.  It is kept as an
  // explicit statement of that disjointness, not as a tie-break that does
  // work.
  wire [rv32i_pkg::HPM_EV_COUNT-1:0] hpm_event;
  assign hpm_event[rv32i_pkg::HPM_EV_LOADUSE    - 1] = id_stall && !ex_stall;
  assign hpm_event[rv32i_pkg::HPM_EV_EXSTALL    - 1] = ex_stall;
  assign hpm_event[rv32i_pkg::HPM_EV_REDIRECT   - 1] = ex_redirect;
  // A redirect has three causes -- trap, MRET and misprediction.  This one
  // isolates the third, so that REDIRECT minus MISPREDICT is the trap-and-MRET
  // term.  A19's second closure said those are zero on both benchmarks; this is
  // the counter that lets that be checked on the board instead of assumed.
  assign hpm_event[rv32i_pkg::HPM_EV_MISPREDICT - 1] = !ex_stall && ex_mispredict;
  // ex_bp_upd is already "a control transfer is resolving in EX, not stalled and
  // not trapping", which is exactly the population these two want to count over.
  assign hpm_event[rv32i_pkg::HPM_EV_BTB_HIT    - 1] = ex_bp_upd && id_ex_q.pred_hit;
  assign hpm_event[rv32i_pkg::HPM_EV_XFER_TAKEN - 1] = ex_bp_upd && ex_ctrl_xfer;

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
      // ... and NOT on a stalled cycle.  A 34-cycle divide would otherwise
      // retire 34 times, which is the one thing minstret exists to be right
      // about.  csr_traps_minstret and riscv-tests' instret_overflow both see
      // this immediately; nothing else would.
      .instret_bump     (id_ex_q.valid && !ex_trap && !ex_stall),
      .hpm_event        (hpm_event),
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
  //
  // A14's product or quotient arrives here TOO, rather than through a new
  // result_sel_e member, and that is a considered choice.  A new member would
  // have to be added to BOTH `ex_mem_fwd_data` and `mem_result` -- two case
  // statements over the same enum that must agree -- and the one time a member
  // was added to those, they disagreed on it and shipped: riscv-formal's `reg`
  // check found an Xkntt instruction forwarding its ALU output while writing
  // zero to the register file.  Delivering the result through `ex_result`
  // means result_sel stays RES_ALU and neither case statement changes at all,
  // so there is nothing for them to disagree about.  It is also honest: this
  // field is defined as the EX stage's result whatever produced it, which is
  // exactly what a 34-cycle quotient is.
  //
  // The value is only correct on the cycle ex_md_done is high -- but that is
  // the only cycle EX/MEM latches anything, because ex_stall bubbles it on
  // every other one.
  logic [31:0] ex_result;
  always_comb begin
    if      (id_ex_q.ctrl.is_muldiv) ex_result = ex_md_result;
    else if (id_ex_q.ctrl.is_csr)    ex_result = ex_csr_rdata;
    else                             ex_result = ex_alu_y;
  end

  // ---- traps (A9) ----------------------------------------------------------
  // ALL OF THEM RESOLVE HERE.  The misaligned-address check in particular is
  // done on the address the ALU just computed rather than in MEM where the
  // access lands, so that the faulting store can be suppressed before the RAM
  // ever sees it and so that nothing older than EX is ever squashed.
  logic ex_addr_misaligned;
  always_comb begin
    unique case (id_ex_q.ctrl.mem_op)
      rv32i_pkg::F3_LH, rv32i_pkg::F3_LHU: ex_addr_misaligned = ex_mem_addr[0];
      rv32i_pkg::F3_LW:                    ex_addr_misaligned = |ex_mem_addr[1:0];
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
        ex_trap = 1'b1; ex_trap_cause = 5'd4;  ex_trap_val = ex_mem_addr;
      end else if (id_ex_q.ctrl.mem_write && ex_addr_misaligned) begin
        ex_trap = 1'b1; ex_trap_cause = 5'd6;  ex_trap_val = ex_mem_addr;
      end
    end
  end

  // GATED ON !ex_stall.  No multi-cycle instruction is a branch, a jump, an
  // MRET or a trapping instruction, so this term can never change the value
  // today -- but the argument for that is a property of the current decoder,
  // and a redirect fired while EX was held would flush the front end around an
  // instruction that had not finished.  One AND gate, off the critical path,
  // to make the invariant structural instead of argued.
  // A19 CHANGES WHAT SETS THIS AND NOTHING ELSE ABOUT IT.  A correctly
  // predicted taken transfer no longer redirects at all -- that is the entire
  // payoff -- and a branch predicted taken that resolves not-taken now
  // redirects to pc + 4, which is a redirect this core has never issued
  // before and is the whole of the downside term in the spec's section 7.
  assign ex_redirect = !ex_stall && (ex_trap || ex_mret || ex_mispredict);

  // ...and this is now the ARCHITECTURAL next pc in every case, whether or not
  // a redirect is taken.  It has to be, because RVFI reports it as pc_wdata and
  // a correctly predicted branch does not redirect: computing it from
  // ex_redirect would have RVFI claim the branch fell through.  riscv-formal's
  // pc_fwd is exactly the check that would have found that, and making the
  // signal unconditional is cheaper than being found by it.
  always_comb begin
    if      (ex_trap)      ex_redirect_target = csr_mtvec;
    else if (ex_mret)      ex_redirect_target = csr_mepc;
    else if (ex_ctrl_xfer) ex_redirect_target = ex_jump_target;
    else                   ex_redirect_target = ex_pc_plus4;
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
    end else if (ex_trap || ex_stall) begin
      // A14 adds the second reason to bubble here, and it is the same whole-
      // struct clear for the same reason: a partly-cleared bubble carrying a
      // live mem_write is one forgotten gate away from writing memory.  Each
      // stalled cycle inserts exactly one bubble, which is the Sum(latency - 1)
      // term tb/cosim/cycle_model.py adds to its span prediction.
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

  // ---- the RVFI port (A11) -------------------------------------------------
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

  // ---- A17: the invariant the dedicated address adder rests on -------------
  // Everything the adder is allowed to do follows from one equality: for the
  // instructions that use an address, the second adder and the ALU agree.  It
  // is stated here rather than argued in a comment because the argument is a
  // property of the DECODER (alu_op/alu_src_a/alu_src_b for loads and stores)
  // and decoders get edited.  Every riscv-formal check proves it at depth 14,
  // for free, because an assert in the design is an obligation on all of them.
  //
  // The RISCV_FORMAL guard is not because the property is formal-only -- it is
  // because that is the only harness in this project that reads the core with
  // assertions enabled, and an unguarded SVA block would be dead text in the
  // nine simulator builds here, none of which are compiled with assertions on.
`ifdef RISCV_FORMAL
  always_comb begin
    if (id_ex_q.valid && (id_ex_q.ctrl.mem_read || id_ex_q.ctrl.mem_write))
      a_addr_adder_matches_alu: assert (ex_mem_addr == ex_alu_y);

    // ---- A20: the two stalls are MUTUALLY EXCLUSIVE, and that is proved
    // ---- here rather than assumed by the counter that depends on it.
    //
    // `id_stall` requires the instruction in EX to be a LOAD (rvntt_hazard's
    // ex_pending_load); `ex_stall` requires it to be a multiply or a divide.
    // One instruction cannot be both, so they can never assert together.
    //
    // THIS WAS WRITTEN AS A COMMENT FIRST AND THE COMMENT WAS WRONG IN THE
    // OTHER DIRECTION.  The counter for load-use cycles was written as
    // `id_stall && !ex_stall` to "break the tie", and A20's own fault injection
    // showed that dropping the guard changes nothing at all -- because there is
    // no tie to break.  A guard against an impossible case is harmless; a
    // guard that is BELIEVED to be load-bearing is not, because it makes the
    // next person reason about an overlap that does not exist.  So the fact is
    // asserted, the guard stays as documentation of the disjointness, and if a
    // future multi-cycle unit ever does overlap with a load this fires at
    // depth 14 instead of silently double-counting.
    a_stalls_are_disjoint: assert (!(id_stall && ex_stall));
  end

  // ---- A19: the other half of rvntt_bpred's interface contract ------------
  // The predictor ASSUMES both of these; asserting them here is what stops
  // that assumption from being a hole.  It was not written down first -- the
  // module's own proof found it, by predicting a misaligned return address
  // from a RAS entry pushed by a CALL at a misaligned pc.  The core cannot do
  // that (a pc is aligned or the fetch trapped, a target is aligned or the
  // misaligned-target trap gated upd_valid off) and now it is proved rather
  // than asserted in prose.
  always_comb begin
    if (ex_bp_upd) begin
      a_bp_upd_pc_aligned:     assert (id_ex_q.pc[1:0]   == 2'b00);
      // The dedicated branch/JAL target adder agrees with the ALU, for the
      // shapes that use it.  Same shape of claim as a_addr_adder_matches_alu,
      // and a property of the DECODER (SRCA_PC, SRCB_IMM, ALU_ADD for B and J)
      // rather than of this file, so it is asserted rather than argued.
      if (!id_ex_q.ctrl.jalr)
        a_pc_target_matches_alu: assert (ex_pc_target == ex_alu_y);
      a_bp_upd_target_aligned: assert (ex_jump_target[1:0] == 2'b00 ||
                                       !ex_ctrl_xfer);
    end
  end
`endif

endmodule

`default_nettype wire
