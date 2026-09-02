// ============================================================================
// rvntt_decode -- the RV32I + Zicsr + Xkntt instruction decoder (plan A3).
//
// Combinational.  Produces the ctrl_t bundle plus the four register addresses.
//
// LEGALITY IS THE POINT OF THIS MODULE.  A3 compares it against a Python
// decoder over 10^6 random 32-bit words, and at that scale nearly every word is
// illegal, so the interesting question is not "does ADD decode" but "does this
// module agree, bit for bit, on exactly which words are rejected".  Three rules
// govern that, and they are not the same rule:
//
//   1. Xkntt reserved fields are STRICT.  docs/isa-spec.md "Decode rules" 3:
//      a register field an instruction does not use is reserved, and a nonzero
//      value is an illegal instruction, not a don't-care.  `kntt.wait rd` with
//      a nonzero rs1 is illegal.  This is what model/isa/xkntt.py enforces, and
//      the four-way agreement (RTL, Python, Spike, LLVM) is defined on it.
//
//   2. Base RV32I FENCE fields are NOT strict.  The base ISA says the fm, pred,
//      succ, rs1 and rd fields of FENCE are reserved for future finer-grain
//      fences and that base implementations SHALL IGNORE them.  Ignoring is
//      spec-mandated, so a nonzero value there is legal.  Copying rule 1 onto
//      FENCE would diverge from Spike, which is A5's reference.
//
//   3. Anything outside the ISA string is illegal.  The core is
//      rv32im_zicsr_zicntr_xkntt0p1 (tb/cosim/spike_asm.py).  M IS in it as of
//      A14, so OP with funct7 = 0000001 is legal for all eight funct3 values;
//      every other funct7 in OP remains illegal.  No Zifencei, so FENCE.I is
//      still illegal.
//
// An illegal instruction produces EXACTLY the reset bundle.  The whole ctrl_t
// is cleared at the bottom of the always_comb, not just the side-effect flags:
// several arms set result_sel or imm_fmt before legality is known, and leaving
// those at whatever the arm assigned makes "illegal" mean something slightly
// different in every opcode.  Stating it once, totally, is both easier to prove
// and impossible for the Python model to disagree with on a don't-care field --
// which it did, before this was total.
//
// Package references are fully qualified with no `import`: see the note in
// rvntt_alu.sv.  Yosys rejects every import form.
// ============================================================================
`default_nettype none

module rvntt_decode
(
    input  wire  logic [31:0] insn,
    output       rv32i_pkg::ctrl_t ctrl,
    output logic [4:0]        rd_addr,
    output logic [4:0]        rs1_addr,
    output logic [4:0]        rs2_addr,
    output logic [4:0]        rs3_addr
);

  wire [6:0]  opcode  = insn[6:0];
  wire [2:0]  funct3  = insn[14:12];
  wire [6:0]  funct7  = insn[31:25];
  wire [1:0]  funct2  = insn[26:25];
  wire [11:0] funct12 = insn[31:20];

  assign rd_addr  = insn[11:7];
  assign rs1_addr = insn[19:15];
  assign rs2_addr = insn[24:20];
  assign rs3_addr = insn[31:27];

  // A funct7 of 0000000 or 0100000 -- the only two the base ISA uses.
  wire f7_base = (funct7 == rv32i_pkg::F7_BASE);
  wire f7_alt  = (funct7 == rv32i_pkg::F7_ALT);

  always_comb begin
    // Default: illegal, everything else zero.  Every enum in ctrl_t has a
    // member at encoding 0 (ALU_ADD, IMM_NONE, SRCA_RS1, SRCB_RS2, RES_ALU,
    // XK_NONE), so '0 is a well-defined, meaningful reset rather than an
    // arbitrary bit pattern.  Written as '0 and not '{default: '0}: Yosys
    // rejects the latter (see rtl/core/CLAUDE.md).
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
      // The ALU computes the TARGET (pc + imm); the link value pc+4 comes from
      // result_sel, not from the ALU.  A8 consumes both.
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
          ctrl.jalr       = 1'b1;    // A8 must clear bit 0 of the target
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
      // The ALU computes pc + imm; the comparison is a separate comparator in
      // EX (plan §1.6, built in A8), which is why alu_op is ADD here and not a
      // compare.  funct3 010 and 011 are reserved.
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
        unique case (funct3)
          rv32i_pkg::F3_ADD_SUB: begin ctrl.alu_op = rv32i_pkg::ALU_ADD;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_SLT:     begin ctrl.alu_op = rv32i_pkg::ALU_SLT;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_SLTU:    begin ctrl.alu_op = rv32i_pkg::ALU_SLTU; ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_XOR:     begin ctrl.alu_op = rv32i_pkg::ALU_XOR;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_OR:      begin ctrl.alu_op = rv32i_pkg::ALU_OR;   ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_AND:     begin ctrl.alu_op = rv32i_pkg::ALU_AND;  ctrl.is_illegal = 1'b0; end
          // SLLI/SRLI/SRAI: the shift amount is insn[24:20], so insn[31:25]
          // must be a valid funct7.  On RV32 insn[25] being set is exactly the
          // RV64 shamt[5] case, and it is illegal here -- the f7 check covers
          // it without a separate test.
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
      // 0100000, and the M extension (A14) which is 0000001.  Every OTHER
      // funct7 still lands on the illegal path.
      rv32i_pkg::OPC_OP: begin
        ctrl.reg_write  = 1'b1;
        ctrl.uses_rs1   = 1'b1;
        ctrl.uses_rs2   = 1'b1;
        ctrl.alu_src_b  = rv32i_pkg::SRCB_RS2;
        ctrl.result_sel = rv32i_pkg::RES_ALU;
        // M (A14) is a THIRD legal funct7 in OP, and it is total: all eight
        // funct3 values exist, so this arm has no illegal case of its own.
        // Rule 3 in the header changes here and nowhere else -- OP with
        // funct7 = 0000001 used to be "outside the ISA string"; it is now
        // inside it.  Every other funct7 stays illegal, which is what keeps
        // the strict-reserved-field claim intact.
        //
        // result_sel stays RES_ALU: the product or quotient is delivered
        // through rvntt_core's `ex_result`, the same field a Zicsr read uses,
        // rather than through a new result_sel_e member.  rvntt_core.sv says
        // why at length -- in short, a new member has to be added to TWO case
        // statements that must agree, and the one time that was done the two
        // disagreed and riscv-formal found it.
        if (funct7 == rv32i_pkg::F7_MULDIV) begin
          ctrl.is_muldiv  = 1'b1;
          ctrl.muldiv_op  = funct3;
          ctrl.alu_op     = rv32i_pkg::ALU_ADD;   // the ALU is not consulted
          ctrl.is_illegal = 1'b0;
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
      // FENCE only.  Its fm/pred/succ/rs1/rd fields are ignored, per the base
      // ISA -- see rule 2 in the header.  FENCE.I is Zifencei, not in this
      // core's ISA string, so it stays illegal.
      rv32i_pkg::OPC_MISC_MEM: begin
        if (funct3 == 3'b000) ctrl.is_illegal = 1'b0;   // decodes as a NOP
      end

      // ---------------------------------------------------------- SYSTEM
      rv32i_pkg::OPC_SYSTEM: begin
        unique case (funct3)
          // ECALL / EBREAK / MRET / WFI.  rd and rs1 are reserved here and must
          // be zero -- unlike FENCE, the privileged spec gives these fixed
          // encodings rather than ignorable fields.
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
          // Zicsr, register form.  Which CSR numbers actually exist is A9's
          // problem; the decoder only says "this is a CSR access".
          rv32i_pkg::F3_CSRRW, rv32i_pkg::F3_CSRRS, rv32i_pkg::F3_CSRRC: begin
            ctrl.is_csr     = 1'b1;
            ctrl.reg_write  = 1'b1;
            ctrl.uses_rs1   = 1'b1;
            ctrl.imm_fmt    = rv32i_pkg::IMM_I;
            ctrl.result_sel = rv32i_pkg::RES_CSR;
            ctrl.is_illegal = 1'b0;
          end
          // Zicsr, immediate form.  rs1 is a 5-bit uimm, NOT a register, so
          // uses_rs1 stays low -- otherwise the forwarding unit would stall on
          // a phantom dependency that no test would ever show as wrong, only
          // as a slightly worse IPC.
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

      // -------------------------------------------- Xkntt tier 1: custom-0
      // funct3 selects the FORMAT as well as the operation: 3 and 4 are
      // R4-type with funct2 at 26:25 and rs3 at 31:27, everything else is
      // R-type with funct7 at 31:25.  That rule must be applied BEFORE bits
      // 31:25 are interpreted (docs/isa-spec.md decode rule 1).
      rv32i_pkg::OPC_CUSTOM_0: begin
        ctrl.is_xkntt   = 1'b1;
        ctrl.reg_write  = 1'b1;
        ctrl.uses_rs1   = 1'b1;
        ctrl.uses_rs2   = 1'b1;
        ctrl.result_sel = rv32i_pkg::RES_XKNTT;
        unique case (funct3)
          rv32i_pkg::F3_KMM:    if (f7_base) begin ctrl.xkntt_op = rv32i_pkg::XK_KMM;    ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_KBFCT:  if (f7_base) begin ctrl.xkntt_op = rv32i_pkg::XK_KBFCT;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_KBFGS:  if (f7_base) begin ctrl.xkntt_op = rv32i_pkg::XK_KBFGS;  ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_KBMUL1: if (f7_base) begin ctrl.xkntt_op = rv32i_pkg::XK_KBMUL1; ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_KBMUL0: if (funct2 == 2'b00) begin
            ctrl.xkntt_op = rv32i_pkg::XK_KBMUL0; ctrl.uses_rs3 = 1'b1; ctrl.is_illegal = 1'b0; end
          rv32i_pkg::F3_KMAC:   if (funct2 == 2'b00) begin
            ctrl.xkntt_op = rv32i_pkg::XK_KMAC;   ctrl.uses_rs3 = 1'b1; ctrl.is_illegal = 1'b0; end
          default: ;   // funct3 6 and 7 are unassigned in custom-0
        endcase
      end

      // -------------------------------------------- Xkntt tier 2: custom-1
      // All R-type.  The register fields an instruction does not use are
      // RESERVED and must be zero -- rule 1 in the header.  This is the block
      // that makes a strict and a lax decoder disagree on random words.
      rv32i_pkg::OPC_CUSTOM_1: begin
        ctrl.is_xkntt = 1'b1;
        unique case (funct3)
          // kntt.cfg rs1, rs2 -- writes no register, so rd is reserved.
          rv32i_pkg::F3_KNTT_CFG: if (f7_base && rd_addr == 5'd0) begin
            ctrl.xkntt_op = rv32i_pkg::XK_NTT_CFG;
            ctrl.uses_rs1 = 1'b1;
            ctrl.uses_rs2 = 1'b1;
            ctrl.is_illegal = 1'b0;
          end
          // kntt.start rd, rs1 -- rs2 is reserved.
          rv32i_pkg::F3_KNTT_START: if (f7_base && rs2_addr == 5'd0) begin
            ctrl.xkntt_op = rv32i_pkg::XK_NTT_START;
            ctrl.uses_rs1 = 1'b1;
            ctrl.reg_write = 1'b1;
            ctrl.result_sel = rv32i_pkg::RES_XKNTT;
            ctrl.is_illegal = 1'b0;
          end
          // kntt.wait rd / kntt.stat rd -- both rs1 and rs2 are reserved.
          rv32i_pkg::F3_KNTT_WAIT: if (f7_base && rs1_addr == 5'd0 && rs2_addr == 5'd0) begin
            ctrl.xkntt_op = rv32i_pkg::XK_NTT_WAIT;
            ctrl.reg_write = 1'b1;
            ctrl.result_sel = rv32i_pkg::RES_XKNTT;
            ctrl.is_illegal = 1'b0;
          end
          rv32i_pkg::F3_KNTT_STAT: if (f7_base && rs1_addr == 5'd0 && rs2_addr == 5'd0) begin
            ctrl.xkntt_op = rv32i_pkg::XK_NTT_STAT;
            ctrl.reg_write = 1'b1;
            ctrl.result_sel = rv32i_pkg::RES_XKNTT;
            ctrl.is_illegal = 1'b0;
          end
          default: ;   // funct3 4..7 are unassigned in custom-1
        endcase
      end

      default: ;   // unlisted opcode, including every word with insn[1:0] != 11
    endcase

    // An illegal instruction must have NO architectural effect.  The whole
    // bundle is reset, not just the side-effect flags: several arms above set
    // result_sel, imm_fmt or alu_op before legality is known, and leaving those
    // at whatever the arm happened to assign makes "illegal" mean something
    // slightly different in every opcode.  A total reset makes the rule total,
    // which is both easier to state and easier to prove -- and it is what the
    // Python model does, so the two cannot disagree on a don't-care field.
    // (They did: an illegal custom-0 word left result_sel = RES_XKNTT in the
    // RTL and RES_ALU in the model, which the 10^6-word comparison found.)
    if (ctrl.is_illegal) begin
      ctrl = '0;
      ctrl.is_illegal = 1'b1;
    end
  end

`ifdef FORMAL
  // Structural invariants.  These hold for EVERY 32-bit word, which is a
  // stronger statement than the 10^6-word random comparison can make, and they
  // are the properties whose violation would be hardest to spot in a diff.

  // 1. An illegal instruction produces EXACTLY the reset bundle -- every field,
  //    not just the side-effect flags.  Comparing against a constructed value
  //    makes this total, so adding a ctrl_t field cannot quietly fall outside
  //    the property the way a hand-listed set of fields would.
  rv32i_pkg::ctrl_t f_reset_ctrl;
  always_comb begin
    f_reset_ctrl = '0;
    f_reset_ctrl.is_illegal = 1'b1;
  end
  always_comb if (ctrl.is_illegal) assert (ctrl == f_reset_ctrl);

  // 2. Anything that is not a 32-bit instruction encoding is illegal.  The
  //    core implements no compressed extension, so insn[1:0] != 11 must never
  //    decode to anything.
  always_comb if (insn[1:0] != 2'b11) assert (ctrl.is_illegal);

  // 3. A legal instruction always names its operand sources consistently: rs3
  //    is only ever read by the two R4-type custom-0 operations, and only those
  //    two ever set uses_rs3.
  always_comb if (ctrl.uses_rs3) begin
    assert (ctrl.xkntt_op == rv32i_pkg::XK_KBMUL0 ||
            ctrl.xkntt_op == rv32i_pkg::XK_KMAC);
    assert (opcode == rv32i_pkg::OPC_CUSTOM_0);
  end

  // 4. Only an Xkntt instruction may carry an Xkntt operation, and every legal
  //    Xkntt instruction must name one.  A custom opcode that decoded legal
  //    with XK_NONE would silently execute as a no-op.
  always_comb begin
    if (ctrl.xkntt_op != rv32i_pkg::XK_NONE) assert (ctrl.is_xkntt);
    if (ctrl.is_xkntt && !ctrl.is_illegal)   assert (ctrl.xkntt_op != rv32i_pkg::XK_NONE);
    if (ctrl.is_xkntt) assert (opcode == rv32i_pkg::OPC_CUSTOM_0 ||
                               opcode == rv32i_pkg::OPC_CUSTOM_1);
  end

  // 5. result_sel and the side-effect flags cannot contradict each other.
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

  // 6. The immediate-form CSR instructions must not claim to read rs1: that
  //    field is a uimm.  A phantom dependency here would never show up as a
  //    wrong answer, only as unexplained stalls and a worse IPC number.
  always_comb if (ctrl.is_csr && ctrl.imm_fmt == rv32i_pkg::IMM_Z)
    assert (!ctrl.uses_rs1);

  // 7. A MULTI-CYCLE INSTRUCTION IS NEVER A MEMORY OPERATION (A14), and this
  //    is the property rvntt_core relies on to leave its store path ungated by
  //    ex_stall.  That gate would sit on the design's critical path, so the
  //    invariant is proved here instead of paid for there -- and if it ever
  //    stops holding, a store would be replayed once per stall cycle.
  //    Also stated: a multi-cycle instruction writes a register through
  //    RES_ALU, which is what lets the two result muxes in rvntt_core stay
  //    untouched by A14.
  always_comb if (ctrl.is_muldiv) begin
    assert (!ctrl.is_illegal);
    assert (!ctrl.mem_read && !ctrl.mem_write);
    assert (!ctrl.branch && !ctrl.jump && !ctrl.is_csr && !ctrl.is_xkntt);
    assert (!ctrl.is_ecall && !ctrl.is_ebreak && !ctrl.is_mret);
    assert (ctrl.reg_write && ctrl.result_sel == rv32i_pkg::RES_ALU);
    assert (ctrl.uses_rs1 && ctrl.uses_rs2 && !ctrl.uses_rs3);
    assert (opcode == rv32i_pkg::OPC_OP && funct7 == rv32i_pkg::F7_MULDIV);
    assert (ctrl.muldiv_op == funct3);
  end
  //    ... and the converse: the M funct7 in OP is ALWAYS the multi-cycle unit,
  //    so no M encoding can slip through as something else.
  always_comb if (opcode == rv32i_pkg::OPC_OP && funct7 == rv32i_pkg::F7_MULDIV)
    assert (ctrl.is_muldiv);
`endif

endmodule

`default_nettype wire
