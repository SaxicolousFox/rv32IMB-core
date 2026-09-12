// ============================================================================
// rvntt_decode -- the RV32IM + B + Zbkb + Zicond + Zicsr instruction decoder.
//
// Combinational.  Produces the ctrl_t bundle plus the three register
// addresses.  Compared bit for bit against model/rv32i_ref.py over 10^6
// random words, so legality is the point: anything outside
// rv32im_zba_zbb_zbs_zbkb_zicond_zkr_zkt_zicsr_zicntr is illegal.
//
//   * OP with funct7 = 0000001 (M) is legal for all eight funct3 values.  B,
//     Zbkb and Zicond are legal only at their exact (funct7, funct3) pairs,
//     and the OP-IMM unary forms only at their exact rs2.  Every other pair
//     is illegal.  No Zifencei, so FENCE.I is illegal.
//   * FENCE's fm/pred/succ/rs1/rd fields are ignored, as the base ISA
//     mandates.  ECALL/EBREAK/MRET/WFI require rd = rs1 = 0.
//   * An illegal instruction produces exactly the reset bundle: the whole
//     ctrl_t is cleared at the bottom, not just the side-effect flags.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_decode
(
    input  wire  logic [31:0] insn,
    output       rv32i_pkg::ctrl_t ctrl,
    output logic [4:0]        rd_addr,
    output logic [4:0]        rs1_addr,
    output logic [4:0]        rs2_addr
);

  wire [6:0]  opcode  = insn[6:0];
  wire [2:0]  funct3  = insn[14:12];
  wire [6:0]  funct7  = insn[31:25];
  wire [11:0] funct12 = insn[31:20];

  assign rd_addr  = insn[11:7];
  assign rs1_addr = insn[19:15];
  assign rs2_addr = insn[24:20];

  // A funct7 of 0000000 or 0100000 -- the only two the base ISA uses.
  wire f7_base = (funct7 == rv32i_pkg::F7_BASE);
  wire f7_alt  = (funct7 == rv32i_pkg::F7_ALT);

  // ---- B (Zba + Zbb + Zbs), Zbkb and Zicond --------------------------------
  // Two flat tables, one per opcode space, yielding BM_NONE when the word is
  // not a bit-manipulation instruction; consulted inside the OP and OP-IMM
  // arms.  In OP-IMM `insn[24:20]` is part of the opcode for the unary forms,
  // not a register.
  wire [4:0] imm_rs2 = insn[24:20];

  rv32i_pkg::bm_op_e bm_op_r;      // the OP (register-register) forms
  always_comb begin
    bm_op_r = rv32i_pkg::BM_NONE;
    unique case ({funct7, funct3})
      {rv32i_pkg::F7_ZBA_SHADD,  3'b010}: bm_op_r = rv32i_pkg::BM_SH1ADD;
      {rv32i_pkg::F7_ZBA_SHADD,  3'b100}: bm_op_r = rv32i_pkg::BM_SH2ADD;
      {rv32i_pkg::F7_ZBA_SHADD,  3'b110}: bm_op_r = rv32i_pkg::BM_SH3ADD;
      // funct7 0100000 is shared with SUB (000) and SRA (101); these three use
      // 100, 110 and 111, so the spaces are disjoint.
      {rv32i_pkg::F7_ALT,        3'b100}: bm_op_r = rv32i_pkg::BM_XNOR;
      {rv32i_pkg::F7_ALT,        3'b110}: bm_op_r = rv32i_pkg::BM_ORN;
      {rv32i_pkg::F7_ALT,        3'b111}: bm_op_r = rv32i_pkg::BM_ANDN;
      {rv32i_pkg::F7_ZBB_MINMAX, 3'b100}: bm_op_r = rv32i_pkg::BM_MIN;
      {rv32i_pkg::F7_ZBB_MINMAX, 3'b101}: bm_op_r = rv32i_pkg::BM_MINU;
      {rv32i_pkg::F7_ZBB_MINMAX, 3'b110}: bm_op_r = rv32i_pkg::BM_MAX;
      {rv32i_pkg::F7_ZBB_MINMAX, 3'b111}: bm_op_r = rv32i_pkg::BM_MAXU;
      {rv32i_pkg::F7_ZBB_ROT,    3'b001}: bm_op_r = rv32i_pkg::BM_ROL;
      {rv32i_pkg::F7_ZBB_ROT,    3'b101}: bm_op_r = rv32i_pkg::BM_ROR;
      {rv32i_pkg::F7_ZBS_BSET,   3'b001}: bm_op_r = rv32i_pkg::BM_BSET;
      {rv32i_pkg::F7_ZBS_BCLR,   3'b001}: bm_op_r = rv32i_pkg::BM_BCLR;
      {rv32i_pkg::F7_ZBS_BCLR,   3'b101}: bm_op_r = rv32i_pkg::BM_BEXT;
      {rv32i_pkg::F7_ZBS_BINV,   3'b001}: bm_op_r = rv32i_pkg::BM_BINV;
      // zext.h is pack with rs2 = x0 -- the same encoding, so no BM_ZEXTH row.
      {rv32i_pkg::F7_ZBKB_PACK,  3'b100}: bm_op_r = rv32i_pkg::BM_PACK;
      {rv32i_pkg::F7_ZBKB_PACK,  3'b111}: bm_op_r = rv32i_pkg::BM_PACKH;
      // Zicond: funct3 000-100 and 110 under this funct7 stay illegal.
      {rv32i_pkg::F7_ZICOND,     3'b101}: bm_op_r = rv32i_pkg::BM_CZEQZ;
      {rv32i_pkg::F7_ZICOND,     3'b111}: bm_op_r = rv32i_pkg::BM_CZNEZ;
      default: bm_op_r = rv32i_pkg::BM_NONE;
    endcase
  end

  rv32i_pkg::bm_op_e bm_op_i;      // the OP-IMM forms
  always_comb begin
    bm_op_i = rv32i_pkg::BM_NONE;
    unique case ({funct7, funct3})
      // The unary group shares opcode, funct3 and imm[11:5] and differs only
      // in the rs2 field; values 3, 6 and 7 fall through to illegal.
      {rv32i_pkg::F7_ZBB_ROT, 3'b001}:
        unique case (imm_rs2)
          rv32i_pkg::RS2_CLZ:   bm_op_i = rv32i_pkg::BM_CLZ;
          rv32i_pkg::RS2_CTZ:   bm_op_i = rv32i_pkg::BM_CTZ;
          rv32i_pkg::RS2_CPOP:  bm_op_i = rv32i_pkg::BM_CPOP;
          rv32i_pkg::RS2_SEXTB: bm_op_i = rv32i_pkg::BM_SEXTB;
          rv32i_pkg::RS2_SEXTH: bm_op_i = rv32i_pkg::BM_SEXTH;
          default:              bm_op_i = rv32i_pkg::BM_NONE;
        endcase
      // rori: the shift amount is the whole rs2 field, so every value is legal.
      {rv32i_pkg::F7_ZBB_ROT, 3'b101}: bm_op_i = rv32i_pkg::BM_ROR;

      {rv32i_pkg::F7_ZBS_BSET, 3'b001}: bm_op_i = rv32i_pkg::BM_BSET;
      {rv32i_pkg::F7_ZBS_BSET, 3'b101}:
        bm_op_i = (imm_rs2 == rv32i_pkg::RS2_ORCB) ? rv32i_pkg::BM_ORCB
                                                   : rv32i_pkg::BM_NONE;
      {rv32i_pkg::F7_ZBS_BCLR, 3'b001}: bm_op_i = rv32i_pkg::BM_BCLR;
      {rv32i_pkg::F7_ZBS_BCLR, 3'b101}: bm_op_i = rv32i_pkg::BM_BEXT;
      {rv32i_pkg::F7_ZBS_BINV, 3'b001}: bm_op_i = rv32i_pkg::BM_BINV;
      // rev8 and brev8 share imm[11:5] and funct3 and differ only in rs2.
      {rv32i_pkg::F7_ZBS_BINV, 3'b101}:
        unique case (imm_rs2)
          rv32i_pkg::RS2_REV8:  bm_op_i = rv32i_pkg::BM_REV8;
          rv32i_pkg::RS2_BREV8: bm_op_i = rv32i_pkg::BM_BREV8;
          default:              bm_op_i = rv32i_pkg::BM_NONE;
        endcase
      // zip and unzip are RV32-only and take one fixed rs2 value each.
      {rv32i_pkg::F7_ZBKB_PACK, 3'b001}:
        bm_op_i = (imm_rs2 == rv32i_pkg::RS2_ZIPUNZ) ? rv32i_pkg::BM_ZIP
                                                     : rv32i_pkg::BM_NONE;
      {rv32i_pkg::F7_ZBKB_PACK, 3'b101}:
        bm_op_i = (imm_rs2 == rv32i_pkg::RS2_ZIPUNZ) ? rv32i_pkg::BM_UNZIP
                                                     : rv32i_pkg::BM_NONE;
      default: bm_op_i = rv32i_pkg::BM_NONE;
    endcase
  end

  always_comb begin
    // Default: illegal, everything else zero.  Every enum in ctrl_t has a
    // member at encoding 0, so '0 is a well-defined reset.  ('0 rather than
    // '{default: '0}, which Yosys rejects.)
    ctrl = '0;
    ctrl.is_illegal = 1'b1;

    unique case (opcode)

      // ------------------------------------------------------------- LUI
      rv32i_pkg::OPC_LUI: begin
        ctrl.reg_write  = 1'b1;
        ctrl.imm_fmt    = rv32i_pkg::IMM_U;
        ctrl.alu_op     = rv32i_pkg::ALU_PASS_B;   // keeps LUI off the adder
        ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
        ctrl.result_sel = rv32i_pkg::RES_ALU;
        ctrl.is_illegal = 1'b0;
      end

      // ----------------------------------------------------------- AUIPC
      rv32i_pkg::OPC_AUIPC: begin
        ctrl.reg_write  = 1'b1;
        ctrl.imm_fmt    = rv32i_pkg::IMM_U;
        ctrl.alu_op     = rv32i_pkg::ALU_ADD;
        ctrl.alu_src_a  = rv32i_pkg::SRCA_PC;
        ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
        ctrl.result_sel = rv32i_pkg::RES_ALU;
        ctrl.is_illegal = 1'b0;
      end

      // ------------------------------------------------------------- JAL
      // The ALU computes the target (pc + imm); the link value comes from
      // result_sel.
      rv32i_pkg::OPC_JAL: begin
        ctrl.reg_write  = 1'b1;
        ctrl.jump       = 1'b1;
        ctrl.imm_fmt    = rv32i_pkg::IMM_J;
        ctrl.alu_op     = rv32i_pkg::ALU_ADD;
        ctrl.alu_src_a  = rv32i_pkg::SRCA_PC;
        ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
        ctrl.result_sel = rv32i_pkg::RES_PC4;
        ctrl.is_illegal = 1'b0;
      end

      // ------------------------------------------------------------ JALR
      rv32i_pkg::OPC_JALR: begin
        if (funct3 == 3'b000) begin
          ctrl.reg_write  = 1'b1;
          ctrl.jump       = 1'b1;
          ctrl.jalr       = 1'b1;    // EX must clear bit 0 of the target
          ctrl.uses_rs1   = 1'b1;
          ctrl.imm_fmt    = rv32i_pkg::IMM_I;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;
          ctrl.alu_src_a  = rv32i_pkg::SRCA_RS1;
          ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
          ctrl.result_sel = rv32i_pkg::RES_PC4;
          ctrl.is_illegal = 1'b0;
        end
      end

      // ---------------------------------------------------------- BRANCH
      // The ALU computes pc + imm; the comparison is rvntt_branch in EX.
      // funct3 010 and 011 are reserved.
      rv32i_pkg::OPC_BRANCH: begin
        if (funct3 != 3'b010 && funct3 != 3'b011) begin
          ctrl.branch     = 1'b1;
          ctrl.uses_rs1   = 1'b1;
          ctrl.uses_rs2   = 1'b1;
          ctrl.imm_fmt    = rv32i_pkg::IMM_B;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;
          ctrl.alu_src_a  = rv32i_pkg::SRCA_PC;
          ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
          ctrl.is_illegal = 1'b0;
        end
      end

      // ------------------------------------------------------------ LOAD
      rv32i_pkg::OPC_LOAD: begin
        if (funct3 == rv32i_pkg::F3_LB  || funct3 == rv32i_pkg::F3_LH ||
            funct3 == rv32i_pkg::F3_LW  || funct3 == rv32i_pkg::F3_LBU ||
            funct3 == rv32i_pkg::F3_LHU) begin
          ctrl.reg_write  = 1'b1;
          ctrl.mem_read   = 1'b1;
          ctrl.mem_op     = funct3;
          ctrl.uses_rs1   = 1'b1;
          ctrl.imm_fmt    = rv32i_pkg::IMM_I;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;
          ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
          ctrl.result_sel = rv32i_pkg::RES_MEM;
          ctrl.is_illegal = 1'b0;
        end
      end

      // ----------------------------------------------------------- STORE
      rv32i_pkg::OPC_STORE: begin
        if (funct3 == rv32i_pkg::F3_LB || funct3 == rv32i_pkg::F3_LH ||
            funct3 == rv32i_pkg::F3_LW) begin
          ctrl.mem_write  = 1'b1;
          ctrl.mem_op     = funct3;
          ctrl.uses_rs1   = 1'b1;
          ctrl.uses_rs2   = 1'b1;    // the store DATA operand
          ctrl.imm_fmt    = rv32i_pkg::IMM_S;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;
          ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
          ctrl.is_illegal = 1'b0;
        end
      end

      // ---------------------------------------------------------- OP-IMM
      rv32i_pkg::OPC_OP_IMM: begin
        ctrl.reg_write  = 1'b1;
        ctrl.uses_rs1   = 1'b1;
        ctrl.imm_fmt    = rv32i_pkg::IMM_I;
        ctrl.alu_src_b  = rv32i_pkg::SRCB_IMM;
        ctrl.result_sel = rv32i_pkg::RES_ALU;
        // The B immediate forms share funct3 001/101 with SLLI/SRLI/SRAI and
        // are separated by imm[11:5], so they are decoded first; anything else
        // falls through to the base funct7 checks.
        if (bm_op_i != rv32i_pkg::BM_NONE) begin
          ctrl.is_bitmanip = 1'b1;
          ctrl.bm_op       = bm_op_i;
          ctrl.alu_op      = rv32i_pkg::ALU_ADD;   // the ALU is not consulted
          ctrl.is_illegal  = 1'b0;
        end else
        unique case (funct3)
          rv32i_pkg::F3_ADD_SUB: begin ctrl.alu_op = rv32i_pkg::ALU_ADD;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_SLT:     begin ctrl.alu_op = rv32i_pkg::ALU_SLT;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_SLTU:    begin ctrl.alu_op = rv32i_pkg::ALU_SLTU; ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_XOR:     begin ctrl.alu_op = rv32i_pkg::ALU_XOR;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_OR:      begin ctrl.alu_op = rv32i_pkg::ALU_OR;   ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_AND:     begin ctrl.alu_op = rv32i_pkg::ALU_AND;  ctrl.is_illegal = 1'b0; end
          // SLLI/SRLI/SRAI: insn[31:25] must be a valid funct7 (insn[25] set
          // is the RV64 shamt[5] case, illegal here).
          rv32i_pkg::F3_SLL: begin
            ctrl.alu_op     = rv32i_pkg::ALU_SLL;
            ctrl.is_illegal = ~f7_base;
          end
          rv32i_pkg::F3_SRL_SRA: begin
            ctrl.alu_op     = f7_alt ? rv32i_pkg::ALU_SRA : rv32i_pkg::ALU_SRL;
            ctrl.is_illegal = ~(f7_base | f7_alt);
          end
          default: ;   // unreachable: all eight funct3 values are listed above
        endcase
      end

      // -------------------------------------------------------------- OP
      // funct7 must be 0000000, except ADD/SUB and SRL/SRA which also take
      // 0100000, M (0000001), and the B/Zbkb/Zicond pairs in bm_op_r.
      rv32i_pkg::OPC_OP: begin
        ctrl.reg_write  = 1'b1;
        ctrl.uses_rs1   = 1'b1;
        ctrl.uses_rs2   = 1'b1;
        ctrl.alu_src_b  = rv32i_pkg::SRCB_RS2;
        ctrl.result_sel = rv32i_pkg::RES_ALU;
        // M is total: all eight funct3 values exist.  result_sel stays RES_ALU
        // because the product or quotient is delivered through ex_result.
        if (funct7 == rv32i_pkg::F7_MULDIV) begin
          ctrl.is_muldiv  = 1'b1;
          ctrl.muldiv_op  = funct3;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;   // the ALU is not consulted
          ctrl.is_illegal = 1'b0;
        end else
        if (bm_op_r != rv32i_pkg::BM_NONE) begin
          ctrl.is_bitmanip = 1'b1;
          ctrl.bm_op       = bm_op_r;
          ctrl.alu_op      = rv32i_pkg::ALU_ADD;   // the ALU is not consulted
          ctrl.is_illegal  = 1'b0;
        end else
        unique case (funct3)
          rv32i_pkg::F3_ADD_SUB: begin
            ctrl.alu_op     = f7_alt ? rv32i_pkg::ALU_SUB : rv32i_pkg::ALU_ADD;
            ctrl.is_illegal = ~(f7_base | f7_alt);
          end
          rv32i_pkg::F3_SRL_SRA: begin
            ctrl.alu_op     = f7_alt ? rv32i_pkg::ALU_SRA : rv32i_pkg::ALU_SRL;
            ctrl.is_illegal = ~(f7_base | f7_alt);
          end
          rv32i_pkg::F3_SLL:  begin ctrl.alu_op = rv32i_pkg::ALU_SLL;  ctrl.is_illegal = ~f7_base; end
          rv32i_pkg::F3_SLT:  begin ctrl.alu_op = rv32i_pkg::ALU_SLT;  ctrl.is_illegal = ~f7_base; end
          rv32i_pkg::F3_SLTU: begin ctrl.alu_op = rv32i_pkg::ALU_SLTU; ctrl.is_illegal = ~f7_base; end
          rv32i_pkg::F3_XOR:  begin ctrl.alu_op = rv32i_pkg::ALU_XOR;  ctrl.is_illegal = ~f7_base; end
          rv32i_pkg::F3_OR:   begin ctrl.alu_op = rv32i_pkg::ALU_OR;   ctrl.is_illegal = ~f7_base; end
          rv32i_pkg::F3_AND:  begin ctrl.alu_op = rv32i_pkg::ALU_AND;  ctrl.is_illegal = ~f7_base; end
          default: ;   // unreachable
        endcase
      end

      // -------------------------------------------------------- MISC-MEM
      // FENCE only; its fields are ignored per the base ISA.  FENCE.I is
      // Zifencei and stays illegal.
      rv32i_pkg::OPC_MISC_MEM: begin
        if (funct3 == 3'b000) ctrl.is_illegal = 1'b0;   // decodes as a NOP
      end

      // ---------------------------------------------------------- SYSTEM
      rv32i_pkg::OPC_SYSTEM: begin
        unique case (funct3)
          // ECALL / EBREAK / MRET / WFI: rd and rs1 must be zero.
          rv32i_pkg::F3_PRIV: begin
            if (rd_addr == 5'd0 && rs1_addr == 5'd0) begin
              unique case (funct12)
                rv32i_pkg::F12_ECALL:  begin ctrl.is_ecall  = 1'b1; ctrl.is_illegal = 1'b0; end
                rv32i_pkg::F12_EBREAK: begin ctrl.is_ebreak = 1'b1; ctrl.is_illegal = 1'b0; end
                rv32i_pkg::F12_MRET:   begin ctrl.is_mret   = 1'b1; ctrl.is_illegal = 1'b0; end
                rv32i_pkg::F12_WFI:    begin ctrl.is_illegal = 1'b0; end   // legal NOP
                default: ;
              endcase
            end
          end
          // Zicsr, register form.  Which CSRs exist is rvntt_csr's concern.
          rv32i_pkg::F3_CSRRW, rv32i_pkg::F3_CSRRS, rv32i_pkg::F3_CSRRC: begin
            ctrl.is_csr     = 1'b1;
            ctrl.reg_write  = 1'b1;
            ctrl.uses_rs1   = 1'b1;
            ctrl.imm_fmt    = rv32i_pkg::IMM_I;
            ctrl.result_sel = rv32i_pkg::RES_CSR;
            ctrl.is_illegal = 1'b0;
          end
          // Zicsr, immediate form.  rs1 is a uimm, not a register, so
          // uses_rs1 stays low (a phantom dependency would only show as IPC).
          rv32i_pkg::F3_CSRRWI, rv32i_pkg::F3_CSRRSI, rv32i_pkg::F3_CSRRCI: begin
            ctrl.is_csr     = 1'b1;
            ctrl.reg_write  = 1'b1;
            ctrl.imm_fmt    = rv32i_pkg::IMM_Z;
            ctrl.result_sel = rv32i_pkg::RES_CSR;
            ctrl.is_illegal = 1'b0;
          end
          default: ;   // funct3 == 100 is reserved
        endcase
      end

      default: ;   // unlisted opcode, including every word with insn[1:0] != 11
    endcase

    // An illegal instruction has no architectural effect: the whole bundle is
    // reset, so "illegal" means the same thing in every opcode and the Python
    // model cannot disagree on a don't-care field.
    if (ctrl.is_illegal) begin
      ctrl = '0;
      ctrl.is_illegal = 1'b1;
    end
  end

`ifdef FORMAL
  // Structural invariants over every 32-bit word.

  // 1. An illegal instruction produces exactly the reset bundle.
  rv32i_pkg::ctrl_t f_reset_ctrl;
  always_comb begin
    f_reset_ctrl = '0;
    f_reset_ctrl.is_illegal = 1'b1;
  end
  always_comb if (ctrl.is_illegal) assert (ctrl == f_reset_ctrl);

  // 2. No compressed extension: insn[1:0] != 11 never decodes.
  always_comb if (insn[1:0] != 2'b11) assert (ctrl.is_illegal);

  // 3. result_sel and the side-effect flags cannot contradict each other.
  always_comb if (!ctrl.is_illegal) begin
    if (ctrl.mem_read)  assert (ctrl.result_sel == rv32i_pkg::RES_MEM);
    if (ctrl.result_sel == rv32i_pkg::RES_MEM) assert (ctrl.mem_read);
    if (ctrl.result_sel == rv32i_pkg::RES_PC4) assert (ctrl.jump);
    if (ctrl.jump)      assert (ctrl.result_sel == rv32i_pkg::RES_PC4);
    // A store never writes a register; a load always does.
    if (ctrl.mem_write) assert (!ctrl.reg_write);
    if (ctrl.mem_read)  assert (ctrl.reg_write);
    // Nothing is both a branch and a jump.
    assert (!(ctrl.branch && ctrl.jump));
    // JALR is a jump.
    if (ctrl.jalr) assert (ctrl.jump);
  end

  // 4. The immediate-form CSR instructions do not claim to read rs1.
  always_comb if (ctrl.is_csr && ctrl.imm_fmt == rv32i_pkg::IMM_Z)
    assert (!ctrl.uses_rs1);

  // 5. A multi-cycle instruction is never a memory operation.  rvntt_core
  //    relies on this to leave its store path ungated by ex_stall (that gate
  //    would sit on the critical path); it also writes through RES_ALU.
  always_comb if (ctrl.is_muldiv) begin
    assert (!ctrl.is_illegal);
    assert (!ctrl.mem_read && !ctrl.mem_write);
    assert (!ctrl.branch && !ctrl.jump && !ctrl.is_csr);
    assert (!ctrl.is_ecall && !ctrl.is_ebreak && !ctrl.is_mret);
    assert (ctrl.reg_write && ctrl.result_sel == rv32i_pkg::RES_ALU);
    assert (ctrl.uses_rs1 && ctrl.uses_rs2);
    assert (opcode == rv32i_pkg::OPC_OP && funct7 == rv32i_pkg::F7_MULDIV);
    assert (ctrl.muldiv_op == funct3);
  end
  //    ... and the converse: the M funct7 in OP is always the multi-cycle unit.
  always_comb if (opcode == rv32i_pkg::OPC_OP && funct7 == rv32i_pkg::F7_MULDIV)
    assert (ctrl.is_muldiv);
`endif

endmodule

`default_nettype wire
