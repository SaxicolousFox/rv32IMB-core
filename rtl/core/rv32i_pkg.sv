// ============================================================================
// rv32i_pkg -- shared types for the 5-stage RV32IMB pipeline.
//
// Two conventions:
//   * Raw instruction fields are never cast to these enums.  Decoders compare
//     against members, so an unlisted encoding falls through to the illegal
//     path instead of aliasing onto a legal one.
//   * Every enum used as a struct field covers its full width or is only ever
//     assigned a named member.  `mem_op` and `muldiv_op` carry funct3 verbatim
//     (the reserved load/store widths must stay representable).
//
// `insn` is carried the whole length of the pipe: the commit tracer and the
// RVFI port both need (pc, insn, rd, wdata) at WB.
// ============================================================================
`ifndef RV32I_PKG_SV
`define RV32I_PKG_SV

package rv32i_pkg;

  // ------------------------------------------------------------------ sizes
  // A package is a library; a single compilation reports the constants it does
  // not use as unused.  The waiver is scoped here so UNUSEDPARAM stays live in
  // every module.
  /* verilator lint_off UNUSEDPARAM */
  localparam int XLEN    = 32;   // RV32
  localparam int REG_N   = 32;   // architectural registers
  localparam int REG_AW  = 5;    // register address width

  // ---------------------------------------------------------------- opcodes
  // RV32I base opcodes.
  typedef enum logic [6:0] {
    OPC_LOAD     = 7'h03,
    OPC_MISC_MEM = 7'h0F,   // FENCE / FENCE.I
    OPC_OP_IMM   = 7'h13,
    OPC_AUIPC    = 7'h17,
    OPC_STORE    = 7'h23,
    OPC_OP       = 7'h33,
    OPC_LUI      = 7'h37,
    OPC_BRANCH   = 7'h63,
    OPC_JALR     = 7'h67,
    OPC_JAL      = 7'h6F,
    OPC_SYSTEM   = 7'h73
  } opcode_e;

  // ----------------------------------------------------------------- funct3
  // OP / OP-IMM; funct7 disambiguates ADD/SUB and SRL/SRA.
  localparam logic [2:0] F3_ADD_SUB = 3'b000;
  localparam logic [2:0] F3_SLL     = 3'b001;
  localparam logic [2:0] F3_SLT     = 3'b010;
  localparam logic [2:0] F3_SLTU    = 3'b011;
  localparam logic [2:0] F3_XOR     = 3'b100;
  localparam logic [2:0] F3_SRL_SRA = 3'b101;
  localparam logic [2:0] F3_OR      = 3'b110;
  localparam logic [2:0] F3_AND     = 3'b111;

  // BRANCH.  010 and 011 are reserved and must trap.
  localparam logic [2:0] F3_BEQ  = 3'b000;
  localparam logic [2:0] F3_BNE  = 3'b001;
  localparam logic [2:0] F3_BLT  = 3'b100;
  localparam logic [2:0] F3_BGE  = 3'b101;
  localparam logic [2:0] F3_BLTU = 3'b110;
  localparam logic [2:0] F3_BGEU = 3'b111;

  // LOAD / STORE width+sign.  Reserved: 011, 110, 111 for loads; 011..111
  // for stores.
  localparam logic [2:0] F3_LB  = 3'b000;
  localparam logic [2:0] F3_LH  = 3'b001;
  localparam logic [2:0] F3_LW  = 3'b010;
  localparam logic [2:0] F3_LBU = 3'b100;
  localparam logic [2:0] F3_LHU = 3'b101;

  // SYSTEM.  funct3 == 000 is the ECALL/EBREAK/MRET family; the rest are Zicsr.
  localparam logic [2:0] F3_PRIV   = 3'b000;
  localparam logic [2:0] F3_CSRRW  = 3'b001;
  localparam logic [2:0] F3_CSRRS  = 3'b010;
  localparam logic [2:0] F3_CSRRC  = 3'b011;
  localparam logic [2:0] F3_CSRRWI = 3'b101;
  localparam logic [2:0] F3_CSRRSI = 3'b110;
  localparam logic [2:0] F3_CSRRCI = 3'b111;

  // ----------------------------------------------------------------- funct7
  localparam logic [6:0] F7_BASE   = 7'b0000000;   // ADD, SRL, SLLI, SRLI, ...
  localparam logic [6:0] F7_ALT    = 7'b0100000;   // SUB, SRA, SRAI
  localparam logic [6:0] F7_MULDIV = 7'b0000001;   // M

  // M funct3.  All eight values are legal under F7_MULDIV.
  localparam logic [2:0] F3_MUL    = 3'b000;
  localparam logic [2:0] F3_MULH   = 3'b001;
  localparam logic [2:0] F3_MULHSU = 3'b010;
  localparam logic [2:0] F3_MULHU  = 3'b011;
  localparam logic [2:0] F3_DIV    = 3'b100;
  localparam logic [2:0] F3_DIVU   = 3'b101;
  localparam logic [2:0] F3_REM    = 3'b110;
  localparam logic [2:0] F3_REMU   = 3'b111;

  // ------------------------------------------------------- multi-cycle EX
  // The latency contract, in cycles of EX occupancy: an instruction with
  // occupancy N sits in EX for N cycles and inserts N-1 bubbles behind it.
  // model/rv32i_ref.py duplicates both numbers and check_pkg_agreement()
  // compares them, so a retune here without retuning the cycle model fails a
  // test.  MUL is 2: an operand register, then a combinational 33x33 (4 DSPs).
  // DIV is 34 (one load, 32 iterations, one fixup) and data-independent.
  localparam int MULDIV_MUL_CYCLES = 2;
  localparam int MULDIV_DIV_CYCLES = 34;

  // SYSTEM funct12 (the whole 31:20 field).
  localparam logic [11:0] F12_ECALL  = 12'h000;
  localparam logic [11:0] F12_EBREAK = 12'h001;
  localparam logic [11:0] F12_MRET   = 12'h302;
  localparam logic [11:0] F12_WFI    = 12'h105;

  /* verilator lint_on UNUSEDPARAM */

  // ------------------------------------------------------------ control ops
  // ALU operation.  ALU_PASS_B carries LUI's immediate through, off the adder.
  typedef enum logic [3:0] {
    ALU_ADD    = 4'd0,
    ALU_SUB    = 4'd1,
    ALU_SLL    = 4'd2,
    ALU_SLT    = 4'd3,
    ALU_SLTU   = 4'd4,
    ALU_XOR    = 4'd5,
    ALU_SRL    = 4'd6,
    ALU_SRA    = 4'd7,
    ALU_OR     = 4'd8,
    ALU_AND    = 4'd9,
    ALU_PASS_B = 4'd10
  } alu_op_e;

  // Immediate format.  IMM_Z is the Zicsr 5-bit zero-extended uimm.
  typedef enum logic [2:0] {
    IMM_NONE = 3'd0,
    IMM_I    = 3'd1,
    IMM_S    = 3'd2,
    IMM_B    = 3'd3,
    IMM_U    = 3'd4,
    IMM_J    = 3'd5,
    IMM_Z    = 3'd6
  } imm_fmt_e;

  typedef enum logic [1:0] {
    SRCA_RS1  = 2'd0,
    SRCA_PC   = 2'd1,   // AUIPC, and the branch/jump target adder
    SRCA_ZERO = 2'd2    // LUI, when not using ALU_PASS_B
  } alu_src_a_e;

  typedef enum logic [0:0] {
    SRCB_RS2 = 1'd0,
    SRCB_IMM = 1'd1
  } alu_src_b_e;

  // B/Zbkb/Zicond funct7 values in OP, and imm[11:5] values in OP-IMM.
  // Consumed by rvntt_decode only; waived the same scoped way as above.
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [6:0] F7_ZBA_SHADD = 7'b0010000;  // sh1add/sh2add/sh3add
  localparam logic [6:0] F7_ZBB_MINMAX= 7'b0000101;  // min/minu/max/maxu
  localparam logic [6:0] F7_ZBB_ROT   = 7'b0110000;  // rol/ror; also rori, clz...
  localparam logic [6:0] F7_ZBKB_PACK = 7'b0000100;  // pack/packh/zext.h; zip/unzip
  localparam logic [6:0] F7_ZBS_BSET  = 7'b0010100;  // bset/bseti; also orc.b
  localparam logic [6:0] F7_ZBS_BCLR  = 7'b0100100;  // bclr/bext and immediates
  localparam logic [6:0] F7_ZBS_BINV  = 7'b0110100;  // binv/binvi; also rev8/brev8

  // The rs2 field of the OP-IMM unary group, which is the only thing that
  // separates clz/ctz/cpop/sext.b/sext.h.  Values 3, 6 and 7 are reserved and
  // must trap.
  localparam logic [4:0] RS2_CLZ    = 5'b00000;
  localparam logic [4:0] RS2_CTZ    = 5'b00001;
  localparam logic [4:0] RS2_CPOP   = 5'b00010;
  localparam logic [4:0] RS2_SEXTB  = 5'b00100;
  localparam logic [4:0] RS2_SEXTH  = 5'b00101;
  localparam logic [4:0] RS2_ORCB   = 5'b00111;  // with F7_ZBS_BSET
  localparam logic [4:0] RS2_REV8   = 5'b11000;  // with F7_ZBS_BINV
  localparam logic [4:0] RS2_BREV8  = 5'b00111;  // with F7_ZBS_BINV
  localparam logic [4:0] RS2_ZIPUNZ = 5'b01111;  // with F7_ZBKB_PACK

  // Zicond.  One funct7 in OP, two funct3 values.
  localparam logic [6:0] F7_ZICOND    = 7'b0000111;
  /* verilator lint_on UNUSEDPARAM */

  // ------------------------------------------------- B (Zba+Zbb+Zbs), Zbkb
  // One member per operation, not per encoding: rori is BM_ROR with the
  // immediate operand, and bseti/bclri/binvi/bexti are their register forms.
  // Six bits so the case statement's default arm stays reachable.  Zicond
  // lives here too; none of this is in alu_op_e because ex_alu_y feeds the
  // jump target and widening the ALU mux would lengthen the fetch redirect.
  typedef enum logic [5:0] {
    BM_NONE   = 6'd0,
    // Zba
    BM_SH1ADD = 6'd1,
    BM_SH2ADD = 6'd2,
    BM_SH3ADD = 6'd3,
    // Zbb / Zbkb logic-with-negate
    BM_ANDN   = 6'd4,
    BM_ORN    = 6'd5,
    BM_XNOR   = 6'd6,
    // Zbb counts
    BM_CLZ    = 6'd7,
    BM_CTZ    = 6'd8,
    BM_CPOP   = 6'd9,
    // Zbb min/max
    BM_MIN    = 6'd10,
    BM_MINU   = 6'd11,
    BM_MAX    = 6'd12,
    BM_MAXU   = 6'd13,
    // Zbb extends
    BM_SEXTB  = 6'd14,
    BM_SEXTH  = 6'd15,
    BM_ZEXTH  = 6'd16,
    // Zbb / Zbkb permutes
    BM_ORCB   = 6'd17,
    BM_REV8   = 6'd18,
    // Zbb / Zbkb rotates.  rori is BM_ROR with SRCB_IMM.
    BM_ROL    = 6'd19,
    BM_ROR    = 6'd20,
    // Zbs single-bit.  The immediate forms are these with SRCB_IMM.
    BM_BSET   = 6'd21,
    BM_BCLR   = 6'd22,
    BM_BINV   = 6'd23,
    BM_BEXT   = 6'd24,
    // Zbkb
    BM_PACK   = 6'd25,
    BM_PACKH  = 6'd26,
    BM_BREV8  = 6'd27,
    BM_ZIP    = 6'd28,
    BM_UNZIP  = 6'd29,
    // Zicond.  czero.eqz rd,rs1,rs2 = (rs2 == 0) ? 0 : rs1; czero.nez is its
    // complement.
    BM_CZEQZ  = 6'd30,
    BM_CZNEZ  = 6'd31
  } bm_op_e;

  // ------------------------------------------------------------ Zihpm events
  // This core's event numbering (the privileged spec leaves it to the
  // implementation).  Event 0 counts nothing and is the reset value.  The six
  // close the identity  mcycle = minstret + load-use + multi-cycle EX +
  // 2 x redirects.
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [3:0] HPM_EV_NONE       = 4'd0;
  localparam logic [3:0] HPM_EV_LOADUSE    = 4'd1;  // load-use interlock cycles
  localparam logic [3:0] HPM_EV_EXSTALL    = 4'd2;  // multi-cycle EX stall cycles
  localparam logic [3:0] HPM_EV_REDIRECT   = 4'd3;  // fetch redirects, all causes
  localparam logic [3:0] HPM_EV_MISPREDICT = 4'd4;  // redirects caused by a misprediction
  localparam logic [3:0] HPM_EV_BTB_HIT    = 4'd5;  // control transfers that hit in the BTB
  localparam logic [3:0] HPM_EV_XFER_TAKEN = 4'd6;  // taken control transfers retired
  localparam logic [3:0] HPM_EV_MAX        = HPM_EV_XFER_TAKEN;
  localparam int         HPM_EV_COUNT      = 6;     // width of the event bus
  /* verilator lint_on UNUSEDPARAM */

  // What the WB stage writes back.  M, B and CSR results all arrive through
  // ex_result under RES_ALU/RES_CSR, so the two result muxes in rvntt_core
  // never need a new arm.
  typedef enum logic [2:0] {
    RES_ALU   = 3'd0,
    RES_MEM   = 3'd1,
    RES_PC4   = 3'd2,   // JAL / JALR link value
    RES_CSR   = 3'd3
  } result_sel_e;

  // ------------------------------------------------------- forwarding select
  // Priority order: FWD_MEM (the younger producer) wins over FWD_WB.  There is
  // no source for a load in MEM; rvntt_hazard stalls that case for one cycle.
  typedef enum logic [1:0] {
    FWD_REG = 2'd0,   // the register file read port (distance >= 3, or none)
    FWD_MEM = 2'd1,   // the EX/MEM stage result   (distance 1)
    FWD_WB  = 2'd2    // the MEM/WB stage result   (distance 2)
  } fwd_sel_e;

  // ------------------------------------------------------------ control set
  // The decoder's output bundle.  `uses_rs1/2` are separate from the register
  // addresses so forwarding and the interlock never fire on a register field
  // the instruction does not read (LUI's rs1 field is part of its immediate).
  typedef struct packed {
    logic        reg_write;
    logic        mem_read;
    logic        mem_write;
    logic [2:0]  mem_op;      // funct3 verbatim, reserved widths included
    logic        branch;
    logic        jump;        // JAL or JALR
    logic        jalr;        // target is rs1+imm, and bit 0 must be cleared
    alu_op_e     alu_op;
    alu_src_a_e  alu_src_a;
    alu_src_b_e  alu_src_b;
    result_sel_e result_sel;
    imm_fmt_e    imm_fmt;
    logic        uses_rs1;
    logic        uses_rs2;
    logic        is_ecall;
    logic        is_ebreak;
    logic        is_mret;
    logic        is_csr;
    // M: selects the multi-cycle EX unit; muldiv_op is funct3 verbatim.
    logic        is_muldiv;
    logic [2:0]  muldiv_op;
    // B / Zbkb / Zicond: the separate bit-manipulation unit.
    logic        is_bitmanip;
    bm_op_e      bm_op;
    logic        is_illegal;
  } ctrl_t;

  // ---------------------------------------------------- branch prediction
  // The predictor's classification of a control transfer, computed in EX
  // where rd and rs1 are known: does the target come from the BTB or the
  // return stack?
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [1:0] BP_BRANCH = 2'd0;
  localparam logic [1:0] BP_JUMP   = 2'd1;
  localparam logic [1:0] BP_CALL   = 2'd2;
  localparam logic [1:0] BP_RET    = 2'd3;
  /* verilator lint_on UNUSEDPARAM */

  // -------------------------------------------------------- pipeline regs
  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
    // The prediction made in IF for this address, carried down so EX can
    // check it.
    logic        pred_taken;
    logic [31:0] pred_target;
    // Observational only: the BTB held an entry for this address.
    logic        pred_hit;
  } if_id_t;

  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
    ctrl_t       ctrl;
    logic [31:0] imm;
    logic [4:0]  rs1_addr;
    logic [4:0]  rs2_addr;
    logic [4:0]  rd_addr;
    logic [31:0] rs1_data;
    logic [31:0] rs2_data;
    logic        pred_taken;
    logic [31:0] pred_target;
    logic        pred_hit;
    // The forwarding decision, made a stage early in ID (against producers in
    // EX and MEM, which are in MEM and WB when the answer is used).  Takes the
    // rd comparators and select encoder off the EX critical path.
    fwd_sel_e    fwd_a;
    fwd_sel_e    fwd_b;
  } id_ex_t;

  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
    logic        reg_write;
    logic        mem_read;
    logic        mem_write;
    logic [2:0]  mem_op;
    result_sel_e result_sel;
    logic [4:0]  rd_addr;
    // The EX stage's result, whatever produced it: ALU, effective address,
    // multiplier/divider, bit-manipulation unit, or the old CSR value.
    logic [31:0] ex_result;
    logic [31:0] store_data;
    logic [31:0] pc_plus4;
  } ex_mem_t;

  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
    logic        reg_write;
    logic [4:0]  rd_addr;
    logic [31:0] wb_data;
  } mem_wb_t;

endpackage : rv32i_pkg

`endif  // RV32I_PKG_SV
