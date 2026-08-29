// ============================================================================
// rv32i_pkg -- shared types for the Track A 5-stage RV32I pipeline.
//
// Plan A1 asks for "opcode, funct3, funct7 enums and pipeline register struct
// definitions", with the pipeline registers as `typedef struct packed` so that
// adding a field later is a one-line change rather than a five-file change.
//
// Two conventions that are load-bearing and easy to get wrong later:
//
//   * Raw instruction fields are never CAST to these enums.  `opcode_e'(x)` on
//     an arbitrary word would both hide illegal encodings and trip Verilator's
//     ENUMVALUE check.  Decoders COMPARE against the members instead, so an
//     unlisted encoding falls through to the illegal-instruction path, which is
//     exactly what A3 has to detect.
//
//   * Every enum used as a struct field covers its full bit width, or is only
//     ever assigned a named member.  `mem_op` is deliberately a plain
//     `logic [2:0]` and not an enum: it carries funct3 verbatim, including the
//     reserved load/store widths, and those must stay representable so the LSU
//     can reject them rather than silently aliasing onto a legal one.
//
// The `insn` field is carried the whole length of the pipe on purpose.  A5's
// commit tracer needs (pc, insn, rd, wdata) at WB to match Spike's
// --log-commits format, and A11's RVFI port needs the same.  It costs 32 FFs
// per stage -- 128 of the XC7A100T's 126,800 -- which is not worth a
// conditional-compile knob.
// ============================================================================
`ifndef RV32I_PKG_SV
`define RV32I_PKG_SV

package rv32i_pkg;

  // ------------------------------------------------------------------ sizes
  localparam int XLEN    = 32;   // RV32
  localparam int REG_N   = 32;   // architectural registers
  localparam int REG_AW  = 5;    // register address width

  // ---------------------------------------------------------------- opcodes
  // RV32I base, plus the two Xkntt opcodes from model/isa/xkntt.py.
  // custom-2 (0x5B) is deliberately absent: it is reserved for RV128.
  typedef enum logic [6:0] {
    OPC_LOAD     = 7'h03,
    OPC_CUSTOM_0 = 7'h0B,   // Xkntt tier 1: tightly-coupled ALU-class
    OPC_MISC_MEM = 7'h0F,   // FENCE / FENCE.I
    OPC_OP_IMM   = 7'h13,
    OPC_AUIPC    = 7'h17,
    OPC_STORE    = 7'h23,
    OPC_CUSTOM_1 = 7'h2B,   // Xkntt tier 2: block coprocessor control
    OPC_OP       = 7'h33,
    OPC_LUI      = 7'h37,
    OPC_BRANCH   = 7'h63,
    OPC_JALR     = 7'h67,
    OPC_JAL      = 7'h6F,
    OPC_SYSTEM   = 7'h73
  } opcode_e;

  // ----------------------------------------------------------------- funct3
  // OP / OP-IMM.  The same three bits also select the shift direction, with
  // funct7 disambiguating ADD/SUB and SRL/SRA.
  localparam logic [2:0] F3_ADD_SUB = 3'b000;
  localparam logic [2:0] F3_SLL     = 3'b001;
  localparam logic [2:0] F3_SLT     = 3'b010;
  localparam logic [2:0] F3_SLTU    = 3'b011;
  localparam logic [2:0] F3_XOR     = 3'b100;
  localparam logic [2:0] F3_SRL_SRA = 3'b101;
  localparam logic [2:0] F3_OR      = 3'b110;
  localparam logic [2:0] F3_AND     = 3'b111;

  // BRANCH.  001 and 010/011 are reserved and must trap.
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

  // Xkntt custom-0 funct3 (model/isa/xkntt.py ISA table).  funct3 selects the
  // FORMAT as well as the operation: 3 and 4 are R4-type, the rest R-type.
  localparam logic [2:0] F3_KMM    = 3'd0;
  localparam logic [2:0] F3_KBFCT  = 3'd1;
  localparam logic [2:0] F3_KBFGS  = 3'd2;
  localparam logic [2:0] F3_KBMUL0 = 3'd3;   // R4
  localparam logic [2:0] F3_KMAC   = 3'd4;   // R4
  localparam logic [2:0] F3_KBMUL1 = 3'd5;

  // Xkntt custom-1 funct3.
  localparam logic [2:0] F3_KNTT_CFG   = 3'd0;
  localparam logic [2:0] F3_KNTT_START = 3'd1;
  localparam logic [2:0] F3_KNTT_WAIT  = 3'd2;
  localparam logic [2:0] F3_KNTT_STAT  = 3'd3;

  // ----------------------------------------------------------------- funct7
  localparam logic [6:0] F7_BASE = 7'b0000000;   // ADD, SRL, SLLI, SRLI, ...
  localparam logic [6:0] F7_ALT  = 7'b0100000;   // SUB, SRA, SRAI

  // SYSTEM funct12 (the whole 31:20 field, not funct7).
  localparam logic [11:0] F12_ECALL  = 12'h000;
  localparam logic [11:0] F12_EBREAK = 12'h001;
  localparam logic [11:0] F12_MRET   = 12'h302;
  localparam logic [11:0] F12_WFI    = 12'h105;

  // ------------------------------------------------------------ control ops
  // ALU operation.  ALU_PASS_B carries LUI's immediate straight through, which
  // keeps LUI off the adder and out of the carry-chain critical path.
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

  // Immediate format.  IMM_Z is the Zicsr 5-bit zero-extended uimm; it is here
  // from the start so that A9 does not have to widen this enum and re-verify
  // every downstream case statement.
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

  // What the WB stage writes back.
  typedef enum logic [2:0] {
    RES_ALU   = 3'd0,
    RES_MEM   = 3'd1,
    RES_PC4   = 3'd2,   // JAL / JALR link value
    RES_CSR   = 3'd3,   // A9
    RES_XKNTT = 3'd4    // tier-1 custom functional unit
  } result_sel_e;

  // Tier-1 / tier-2 Xkntt operation.  Only ever assigned a named member, never
  // cast from funct3, so an unlisted custom encoding lands on XK_NONE plus
  // is_illegal rather than aliasing onto a real operation.
  typedef enum logic [3:0] {
    XK_NONE       = 4'd0,
    XK_KMM        = 4'd1,
    XK_KBFCT      = 4'd2,
    XK_KBFGS      = 4'd3,
    XK_KBMUL0     = 4'd4,
    XK_KMAC       = 4'd5,
    XK_KBMUL1     = 4'd6,
    XK_NTT_CFG    = 4'd7,
    XK_NTT_START  = 4'd8,
    XK_NTT_WAIT   = 4'd9,
    XK_NTT_STAT   = 4'd10
  } xkntt_op_e;

  // ------------------------------------------------------------ control set
  // The decoder's output bundle (A3).  `uses_rs1/2/3` are separate from the
  // register addresses on purpose: the forwarding unit (A6) and the load-use
  // interlock (A7) must not fire on a register field that the instruction does
  // not actually read.  LUI's rs1 field, for instance, is part of the
  // immediate, and treating it as a source would produce phantom stalls that
  // are invisible in a functional test and only show up as an IPC discrepancy.
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
    logic        uses_rs3;
    logic        is_ecall;
    logic        is_ebreak;
    logic        is_mret;
    logic        is_csr;
    logic        is_xkntt;
    xkntt_op_e   xkntt_op;
    logic        is_illegal;
  } ctrl_t;

  // -------------------------------------------------------- pipeline regs
  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
  } if_id_t;

  typedef struct packed {
    logic        valid;
    logic [31:0] pc;
    logic [31:0] insn;
    ctrl_t       ctrl;
    logic [31:0] imm;
    logic [4:0]  rs1_addr;
    logic [4:0]  rs2_addr;
    logic [4:0]  rs3_addr;
    logic [4:0]  rd_addr;
    logic [31:0] rs1_data;
    logic [31:0] rs2_data;
    logic [31:0] rs3_data;
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
    logic [31:0] alu_result;
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
