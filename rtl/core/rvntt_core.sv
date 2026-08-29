// ============================================================================
// rvntt_core -- the 5-stage RV32I pipeline, with hazard handling DELIBERATELY
// ABSENT (plan A4).
//
// Plan A4's bring-up strategy is explicit: build the datapath with no
// forwarding, no stalls and no flushes, verify it against hand-scheduled
// NOP-padded code, and only then add each hazard layer.  Every bug then has a
// small suspect list.  So the omissions below are the design, not a to-do list:
//
//   * NO FORWARDING (A6).  A RAW dependency must be separated by >= 3
//     instructions or the reader sees a stale register.
//   * NO LOAD-USE INTERLOCK (A7).  A load's result must not be used within 2
//     instructions.
//   * NO CONTROL FLOW (A8).  The PC is pc+4, always.  Branches and jumps do not
//     redirect, so a program that needs them will silently compute nonsense.
//
// That last one is the dangerous one, so it is not left to a comment:
// `dbg_unsupported` pulses whenever an instruction retires that this core
// cannot execute faithfully, and the testbench treats it as a failure.  A guard
// beats a footnote -- without it, A5's commit-log differ would report a
// mismatch somewhere downstream of the real cause.
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
    output logic        commit_is_ecall,

    // Pulses with commit_valid when the retiring instruction is one this A4
    // core cannot execute faithfully.  See the header.
    output logic        dbg_unsupported
);

  // ==========================================================================
  // IF -- program counter
  // ==========================================================================
  logic [31:0] pc_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) pc_q <= RESET_PC;
    else        pc_q <= pc_q + 32'd4;      // A8 replaces this with a redirect
  end

  assign imem_addr = pc_q;

  // ==========================================================================
  // IF/ID
  // ==========================================================================
  // The instruction itself is NOT flopped here: it arrives from the RAM's own
  // output register, which is the IF/ID insn register.  Flopping imem_rdata
  // again would add a stage.
  rv32i_pkg::if_id_t if_id_q, if_id;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      if_id_q.valid <= 1'b0;
      if_id_q.pc    <= RESET_PC;
      if_id_q.insn  <= 32'h0;
    end else begin
      if_id_q.valid <= 1'b1;
      if_id_q.pc    <= pc_q;
      if_id_q.insn  <= 32'h0;              // unused; insn comes from the RAM
    end
  end

  always_comb begin
    if_id       = if_id_q;
    if_id.insn  = imem_rdata;
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

  // ==========================================================================
  // ID/EX
  // ==========================================================================
  // UNUSEDSIGNAL on parts of id_ex_q is expected at A4: rs3_addr/rs3_data feed
  // the Xkntt R4 operands and ex_mem_q carries mem_read/mem_write/store_data
  // for a store-buffer this stage does not have yet.  The fields are in the
  // struct because the pipeline registers are defined once, in the package, for
  // the finished design -- not trimmed to whatever the current step happens to
  // read.  Scoped to the declaration so UNUSEDSIGNAL stays live elsewhere.
  /* verilator lint_off UNUSEDSIGNAL */
  rv32i_pkg::id_ex_t id_ex_q;
  /* verilator lint_on UNUSEDSIGNAL */

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
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
  // EX -- ALU
  // ==========================================================================
  logic [31:0] ex_alu_a, ex_alu_b, ex_alu_y;

  always_comb begin
    unique case (id_ex_q.ctrl.alu_src_a)
      rv32i_pkg::SRCA_RS1:  ex_alu_a = id_ex_q.rs1_data;
      rv32i_pkg::SRCA_PC:   ex_alu_a = id_ex_q.pc;
      rv32i_pkg::SRCA_ZERO: ex_alu_a = 32'h0;
      default:              ex_alu_a = id_ex_q.rs1_data;
    endcase
  end

  assign ex_alu_b = (id_ex_q.ctrl.alu_src_b == rv32i_pkg::SRCB_IMM)
                    ? id_ex_q.imm : id_ex_q.rs2_data;

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
    dmem_wdata = id_ex_q.rs2_data;
    dmem_be    = 4'b0000;
    if (id_ex_q.valid && id_ex_q.ctrl.mem_write) begin
      unique case (id_ex_q.ctrl.mem_op)
        rv32i_pkg::F3_LB: begin                       // SB
          dmem_wdata = {4{id_ex_q.rs2_data[7:0]}};
          dmem_be    = 4'b0001 << ex_byte_off;
        end
        rv32i_pkg::F3_LH: begin                       // SH
          dmem_wdata = {2{id_ex_q.rs2_data[15:0]}};
          dmem_be    = ex_byte_off[1] ? 4'b1100 : 4'b0011;
        end
        rv32i_pkg::F3_LW: begin                       // SW
          dmem_wdata = id_ex_q.rs2_data;
          dmem_be    = 4'b1111;
        end
        default: dmem_be = 4'b0000;   // reserved widths never reach here: the
                                      // decoder rejects them as illegal
      endcase
    end
  end

  assign dmem_addr = ex_alu_y;

  // ==========================================================================
  // EX/MEM
  // ==========================================================================
  /* verilator lint_off UNUSEDSIGNAL */
  rv32i_pkg::ex_mem_t ex_mem_q;
  /* verilator lint_on UNUSEDSIGNAL */

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
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
      ex_mem_q.alu_result <= ex_alu_y;
      ex_mem_q.store_data <= id_ex_q.rs2_data;
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

  assign mem_byte_off = ex_mem_q.alu_result[1:0];
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
      rv32i_pkg::RES_ALU: mem_result = ex_mem_q.alu_result;
      rv32i_pkg::RES_MEM: mem_result = mem_load_data;
      rv32i_pkg::RES_PC4: mem_result = ex_mem_q.pc_plus4;
      // RES_CSR is A9's and RES_XKNTT is the coprocessor's; neither exists
      // yet, and dbg_unsupported fires if one ever retires here.
      default:            mem_result = 32'h0;
    endcase
  end

  // ==========================================================================
  // MEM/WB
  // ==========================================================================
  rv32i_pkg::mem_wb_t mem_wb_q;

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

  // ---- the A4 capability guard --------------------------------------------
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

  assign commit_is_ecall = mem_wb_q.valid && wb_ctrl.is_ecall;

  // ECALL is excluded: it is the testbench's stop marker, not an instruction
  // this core pretends to execute.  A CSR access with rd == x0 is also
  // excluded -- `csrw mtvec, t0` has no register-file effect, so the core and
  // Spike agree on architectural state even though no CSR file exists yet, and
  // that is exactly what the A4 test program needs to arm Spike's trap handler.
  assign dbg_unsupported =
      mem_wb_q.valid && !wb_ctrl.is_ecall &&
      (wb_ctrl.is_illegal || wb_ctrl.is_xkntt ||
       wb_ctrl.branch     || wb_ctrl.jump     ||
       (wb_ctrl.is_csr && mem_wb_q.rd_addr != 5'd0));

endmodule

`default_nettype wire
