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
  // A package is a library: these constants are consumed by OTHER files, so any
  // single compilation reports the ones it does not happen to use as unused.
  // That is a property of a library, not a defect -- and the alternative,
  // waiving UNUSEDPARAM on the command line for every file that imports this
  // package, would switch the check off for those modules too.  Scoped here
  // instead, so UNUSEDPARAM stays live everywhere else.
  /* verilator lint_off UNUSEDPARAM */
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
  localparam logic [6:0] F7_BASE   = 7'b0000000;   // ADD, SRL, SLLI, SRLI, ...
  localparam logic [6:0] F7_ALT    = 7'b0100000;   // SUB, SRA, SRAI
  localparam logic [6:0] F7_MULDIV = 7'b0000001;   // M (A14)

  // M funct3.  All eight values are legal under F7_MULDIV, which is why
  // ctrl_t.muldiv_op carries funct3 verbatim rather than being an enum: there
  // is no reserved encoding to keep representable, but there is also nothing to
  // gain from a second name for a field that is already total.
  localparam logic [2:0] F3_MUL    = 3'b000;
  localparam logic [2:0] F3_MULH   = 3'b001;
  localparam logic [2:0] F3_MULHSU = 3'b010;
  localparam logic [2:0] F3_MULHU  = 3'b011;
  localparam logic [2:0] F3_DIV    = 3'b100;
  localparam logic [2:0] F3_DIVU   = 3'b101;
  localparam logic [2:0] F3_REM    = 3'b110;
  localparam logic [2:0] F3_REMU   = 3'b111;

  // ------------------------------------------------------- multi-cycle EX
  // THE LATENCY CONTRACT, in cycles of EX OCCUPANCY -- not in pipeline stages.
  // An instruction with occupancy N sits in EX for N cycles and therefore
  // inserts N-1 bubbles behind it, which is exactly the term tb/cosim/
  // cycle_model.py adds to its span prediction.  model/rv32i_ref.py duplicates
  // both numbers and check_pkg_agreement() parses this file to compare them, so
  // retuning the divider here without retuning the model fails a test rather
  // than silently making the independent cycle model agree by construction.
  //
  // MUL is 4 because the multiplier carries three register stages (operand,
  // product, output) so that Vivado can pack AREG/BREG, MREG and PREG into the
  // DSP48E1 -- plan B1's advice, for plan B1's reason.  DIV is 34 because the
  // radix-2 restoring loop is one load cycle, 32 iterations and one fixup
  // cycle, and it is DATA-INDEPENDENT: an early-out on a small dividend would
  // make the cycle model unbuildable (MODS_A A14).
  localparam int MULDIV_MUL_CYCLES = 4;
  localparam int MULDIV_DIV_CYCLES = 34;

  // SYSTEM funct12 (the whole 31:20 field, not funct7).
  localparam logic [11:0] F12_ECALL  = 12'h000;
  localparam logic [11:0] F12_EBREAK = 12'h001;
  localparam logic [11:0] F12_MRET   = 12'h302;
  localparam logic [11:0] F12_WFI    = 12'h105;

  /* verilator lint_on UNUSEDPARAM */

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

  // A21 funct7 values in OP, and imm[11:5] values in OP-IMM.  Every one of
  // these was read off the table in docs/RISC-V_NTT_MODS_A2.txt Appendix A,
  // which was generated by riscv-none-elf-as and objdump rather than typed --
  // writing this set by hand was tried first and got rev8/brev8/zip/unzip into
  // the wrong opcode.
  // Consumed by rvntt_decode, so every other compilation reports them as
  // unused -- the same library property the block at the top of this file
  // documents, and waived the same scoped way.
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [6:0] F7_ZBA_SHADD = 7'b0010000;  // sh1add/sh2add/sh3add
  localparam logic [6:0] F7_ZBB_MINMAX= 7'b0000101;  // min/minu/max/maxu
  localparam logic [6:0] F7_ZBB_ROT   = 7'b0110000;  // rol/ror; also rori, clz...
  localparam logic [6:0] F7_ZBKB_PACK = 7'b0000100;  // pack/packh/zext.h; zip/unzip
  localparam logic [6:0] F7_ZBS_BSET  = 7'b0010100;  // bset/bseti; also orc.b
  localparam logic [6:0] F7_ZBS_BCLR  = 7'b0100100;  // bclr/bext and immediates
  localparam logic [6:0] F7_ZBS_BINV  = 7'b0110100;  // binv/binvi; also rev8/brev8

  // The rs2 field of the OP-IMM unary group, which is the ONLY thing that
  // separates clz/ctz/cpop/sext.b/sext.h -- they share opcode, funct3 and
  // imm[11:5] entirely.  Values 3, 6 and 7 are reserved and must trap; a
  // decoder that treats this field as a don't-care accepts three illegal
  // encodings while passing every functional test, because no functional test
  // ever emits them.  That is exactly the class A3's sweep exists to catch.
  localparam logic [4:0] RS2_CLZ    = 5'b00000;
  localparam logic [4:0] RS2_CTZ    = 5'b00001;
  localparam logic [4:0] RS2_CPOP   = 5'b00010;
  localparam logic [4:0] RS2_SEXTB  = 5'b00100;
  localparam logic [4:0] RS2_SEXTH  = 5'b00101;
  localparam logic [4:0] RS2_ORCB   = 5'b00111;  // with F7_ZBS_BSET
  localparam logic [4:0] RS2_REV8   = 5'b11000;  // with F7_ZBS_BINV
  localparam logic [4:0] RS2_BREV8  = 5'b00111;  // with F7_ZBS_BINV
  localparam logic [4:0] RS2_ZIPUNZ = 5'b01111;  // with F7_ZBKB_PACK
  /* verilator lint_on UNUSEDPARAM */

  // ------------------------------------------- A21: B (Zba+Zbb+Zbs) and Zbkb
  // ONE MEMBER PER OPERATION, NOT PER ENCODING.  rori is ROR with the shift
  // amount from the immediate, and bseti/bclri/binvi/bexti are their register
  // forms with the bit index from the immediate -- the operand mux already
  // chooses immediate or rs2, so a separate member for each would be 5 extra
  // encodings that all behave identically.
  //
  // 29 members and BM_NONE, so five bits.  Deliberately NOT folded into
  // alu_op_e: MODS_A2 section 3.4 measures the ALU result mux at a third of the
  // critical path, and ex_alu_y feeds ex_jump_target and the mispredict
  // comparison.  A wider alu_op_e widens that mux; a separate unit joined at
  // ex_result -- which terminates at a pipeline register -- does not.
  typedef enum logic [4:0] {
    BM_NONE   = 5'd0,
    // Zba
    BM_SH1ADD = 5'd1,
    BM_SH2ADD = 5'd2,
    BM_SH3ADD = 5'd3,
    // Zbb / Zbkb logic-with-negate
    BM_ANDN   = 5'd4,
    BM_ORN    = 5'd5,
    BM_XNOR   = 5'd6,
    // Zbb counts
    BM_CLZ    = 5'd7,
    BM_CTZ    = 5'd8,
    BM_CPOP   = 5'd9,
    // Zbb min/max
    BM_MIN    = 5'd10,
    BM_MINU   = 5'd11,
    BM_MAX    = 5'd12,
    BM_MAXU   = 5'd13,
    // Zbb extends
    BM_SEXTB  = 5'd14,
    BM_SEXTH  = 5'd15,
    BM_ZEXTH  = 5'd16,
    // Zbb / Zbkb permutes
    BM_ORCB   = 5'd17,
    BM_REV8   = 5'd18,
    // Zbb / Zbkb rotates.  rori is BM_ROR with SRCB_IMM.
    BM_ROL    = 5'd19,
    BM_ROR    = 5'd20,
    // Zbs single-bit.  The immediate forms are these with SRCB_IMM.
    BM_BSET   = 5'd21,
    BM_BCLR   = 5'd22,
    BM_BINV   = 5'd23,
    BM_BEXT   = 5'd24,
    // Zbkb
    BM_PACK   = 5'd25,
    BM_PACKH  = 5'd26,
    BM_BREV8  = 5'd27,
    BM_ZIP    = 5'd28,
    BM_UNZIP  = 5'd29
  } bm_op_e;

  // ------------------------------------------------------- A20: Zihpm events
  // THIS CORE'S event numbering, not an architectural one -- the privileged
  // spec leaves mhpmevent's encoding entirely to the implementation.  It lives
  // in the package rather than in rvntt_csr so that the core, the counter block
  // and the testbenches all name the same constant instead of keeping three
  // copies of the same magic number.
  //
  // Event 0 counts nothing and is the reset value, so a counter software never
  // programmed reads zero forever rather than accumulating something arbitrary.
  // The six were chosen to close A18's identity on hardware:
  //     mcycle = minstret + load-use + multi-cycle EX + 2 x redirects
  // Same reason as the block at the top of this file: rvntt_csr consumes the
  // numbering and rvntt_core consumes the bus width, so any compilation of a
  // module that is neither reports all seven as unused.
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

  // ------------------------------------------------------- forwarding select
  // Which of the three possible sources supplies an EX operand (A6).  The
  // ordering is the PRIORITY order -- FWD_MEM is the younger producer and must
  // win over FWD_WB when both match -- but nothing depends on the numeric
  // values, so this is documentation rather than a trick.
  //
  // There is no fourth source for "the load currently in MEM".  Load data is
  // valid during MEM, so forwarding it would be possible, but it would put the
  // BRAM output register on the path BRAM -> sign-extend -> forward mux -> ALU
  // -> BRAM address, which is the worst path in the design.  A7 stalls that
  // case for one cycle instead and forwards from FWD_WB, which is a register
  // output.  See rvntt_hazard.sv.
  typedef enum logic [1:0] {
    FWD_REG = 2'd0,   // the register file read port (distance >= 3, or none)
    FWD_MEM = 2'd1,   // the EX/MEM stage result   (distance 1)
    FWD_WB  = 2'd2    // the MEM/WB stage result   (distance 2)
  } fwd_sel_e;

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
    // M (A14).  `is_muldiv` selects the multi-cycle EX unit; `muldiv_op` is
    // funct3 verbatim, all eight values of which are legal.  The result comes
    // back through `ex_result` and therefore through result_sel RES_ALU -- see
    // rvntt_core.sv, which explains at length why this is NOT a new
    // result_sel_e member.
    logic        is_muldiv;
    logic [2:0]  muldiv_op;
    // B / Zbkb (A21).  Same shape as is_muldiv above and for the same reason:
    // the result comes back through `ex_result`, so result_sel stays RES_ALU
    // and neither of the two case statements that must agree about it changes.
    // MODS_A2 section 3.4 has the timing argument -- ex_alu_y feeds
    // ex_jump_target, and widening its mux lengthens the fetch redirect.
    logic        is_bitmanip;
    bm_op_e      bm_op;
    logic        is_xkntt;
    xkntt_op_e   xkntt_op;
    logic        is_illegal;
  } ctrl_t;

  // ---------------------------------------------------- branch prediction
  // The four shapes a control transfer can take, as far as the PREDICTOR is
  // concerned.  It is not the decoder's classification: the decoder cares
  // whether an instruction is a branch, a JAL or a JALR, and the predictor
  // cares whether the target comes from the BTB or from the return stack.
  // docs/a19-bpred-spec.md section 4 defines the mapping; it is computed in EX,
  // where rd and rs1 are known, and never in IF, where only the address is.
  //
  // Inside a scoped waiver for the same reason as the constant block above:
  // a package linted standalone reports every localparam as UNUSEDPARAM, and a
  // command-line waiver would switch the check off for every file that imports
  // the package too.
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
    // What the predictor said about THIS instruction's address, carried down
    // so EX can check it.  A19: the prediction is made in IF from an address
    // and verified in EX against an outcome, and these two fields are the only
    // thing connecting the two.
    logic        pred_taken;
    logic [31:0] pred_target;
    // A20, observational only.  "The BTB had an entry for this address" is a
    // different fact from "the predictor said taken", and separating them is
    // what lets a mispredict be attributed to a cold/evicted entry rather than
    // to a wrong direction.  Nothing in the datapath reads it.
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
    logic [4:0]  rs3_addr;
    logic [4:0]  rd_addr;
    logic [31:0] rs1_data;
    logic [31:0] rs2_data;
    logic [31:0] rs3_data;
    logic        pred_taken;
    logic [31:0] pred_target;
    logic        pred_hit;      // A20, observational only
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
    // The EX stage's result, whatever produced it: the ALU for arithmetic, the
    // effective address for a load or store, and the OLD CSR VALUE for a Zicsr
    // access.  Named for the stage rather than for the ALU because A9 gave it a
    // second producer, and `alu_result` holding a CSR value is the kind of
    // half-truth that costs someone an hour.
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
