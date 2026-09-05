// ============================================================================
// rvntt_immgen -- RV32I immediate generation (plan A2).
//
// Produces the I, S, B, U and J immediates, plus the Zicsr 5-bit uimm (IMM_Z,
// used by A9's CSRRWI/CSRRSI/CSRRCI).  Purely combinational.
//
// The B and J formats are bit-scrambled, and that scrambling is the entire
// difficulty of this module.  It is not arbitrary: RISC-V places each
// immediate bit at a FIXED position in the instruction word across all
// formats, so that the sign bit is always insn[31] and the immediate muxes in
// hardware are wire permutations rather than shifters.  The cost of that
// property is that the bit order looks scrambled when written out.
//
//   B: imm[12|10:5] = insn[31:25],  imm[4:1|11] = insn[11:7],  imm[0] = 0
//   J: imm[20|10:1|11|19:12] = insn[31:12],                    imm[0] = 0
//
// Both are implicitly multiples of two -- branch and jump targets are
// halfword-aligned -- so bit 0 is hardwired zero and is NOT taken from the
// instruction.  Forgetting that is the classic off-by-2x bug: every branch
// goes twice as far as it should, which looks like wild control-flow
// corruption rather than an immediate bug.
//
// Sign extension is from insn[31] in every signed format (I, S, B, J).  U is
// not sign-extended -- it IS the top 20 bits, with 12 zeros below.
//
// NOTE ON THE PACKAGE REFERENCES BELOW.  Every package name here is written out
// in full as `rv32i_pkg::X`, and there is no `import` statement anywhere in this
// file.  That is a tool constraint, not a style preference: Yosys rejects BOTH
// the module-header form (`module <name> import rv32i_pkg::*; (...)`) and a
// module-body `import`, failing with "syntax error, unexpected TOK_IMPORT", so
// the formal flow could not read the file at all.  Fully-qualified references
// with a package-typed port are the only form Verilator, Yosys and Vivado all
// accept, and they keep the port's enum type rather than degrading it to a
// plain vector.
// ============================================================================
`default_nettype none

module rvntt_immgen
(
    // UNUSEDSIGNAL on insn[6:0] is expected and correct: the opcode selects the
    // FORMAT, and that selection has already happened by the time `fmt` arrives
    // here.  The whole instruction word is passed in rather than the individual
    // immediate fields so that this module has one uniform interface and the
    // scrambling stays in exactly one place.  Narrowly scoped to this port --
    // UNUSEDSIGNAL stays live for everything else in the module.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire  logic [31:0] insn,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire  rv32i_pkg::imm_fmt_e fmt,
    output logic [31:0]       imm
);

  always_comb begin
    unique case (fmt)
      rv32i_pkg::IMM_I: imm = {{20{insn[31]}}, insn[31:20]};
      rv32i_pkg::IMM_S: imm = {{20{insn[31]}}, insn[31:25], insn[11:7]};
      rv32i_pkg::IMM_B: imm = {{19{insn[31]}}, insn[31], insn[7], insn[30:25], insn[11:8], 1'b0};
      rv32i_pkg::IMM_U: imm = {insn[31:12], 12'b0};
      rv32i_pkg::IMM_J: imm = {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0};
      rv32i_pkg::IMM_Z: imm = {27'b0, insn[19:15]};   // Zicsr uimm, zero-extended
      // IMM_NONE and the unused seventh encoding.  A default arm is required
      // or this always_comb infers a latch; `unique` warns in simulation but
      // does not synthesise a value.
      default: imm = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Width and alignment properties, stated structurally rather than by
  // repeating the concatenations above.

  // Every signed format is sign-extended from insn[31], so all bits above the
  // field width must equal insn[31].
  always_comb begin
    if (fmt == rv32i_pkg::IMM_I) assert (imm[31:11] == {21{insn[31]}});
    if (fmt == rv32i_pkg::IMM_S) assert (imm[31:11] == {21{insn[31]}});
    if (fmt == rv32i_pkg::IMM_B) assert (imm[31:12] == {20{insn[31]}});
    if (fmt == rv32i_pkg::IMM_J) assert (imm[31:20] == {12{insn[31]}});
  end

  // Branch and jump targets are halfword-aligned: bit 0 is hardwired, never
  // sourced from the instruction.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_B) assert (imm[0] == 1'b0);
    if (fmt == rv32i_pkg::IMM_J) assert (imm[0] == 1'b0);
  end

  // U places the instruction's top 20 bits at the top and zeros below; it is
  // the one signed-looking format that is NOT sign-extended.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_U) assert (imm[11:0] == 12'b0);
    if (fmt == rv32i_pkg::IMM_U) assert (imm[31:12] == insn[31:12]);
  end

  // Zicsr uimm is zero-extended, never sign-extended.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_Z) assert (imm[31:5] == 27'b0);
    if (fmt == rv32i_pkg::IMM_Z) assert (imm[4:0] == insn[19:15]);
  end

  // Bit-for-bit placement of the UNSCRAMBLED formats too.  These were missing
  // at first, and fault injection is what found it: replacing IMM_S's
  // `insn[11:7]` with `insn[19:15]` -- taking the low five bits of a store
  // offset from rs1's field instead of rd's -- passed the whole proof, because
  // the only IMM_S property was about sign extension above bit 11, which the
  // mutation left intact.  The cocotb comparison against the Python model
  // caught it, so the RTL was never at risk, but the proof was weaker than it
  // looked.  Pin every source bit, not just the extension.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_I) assert (imm[11:0] == insn[31:20]);
    if (fmt == rv32i_pkg::IMM_S) begin
      assert (imm[11:5] == insn[31:25]);
      assert (imm[4:0]  == insn[11:7]);
    end
  end

  // Bit-for-bit placement of the two scrambled formats, written as individual
  // bit equalities rather than as a concatenation.  This is what actually
  // catches a permutation error: a wrong concatenation and a wrong list of
  // single-bit asserts do not fail in the same way.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_B) begin
      assert (imm[12]   == insn[31]);
      assert (imm[11]   == insn[7]);
      assert (imm[10:5] == insn[30:25]);
      assert (imm[4:1]  == insn[11:8]);
    end
    if (fmt == rv32i_pkg::IMM_J) begin
      assert (imm[20]    == insn[31]);
      assert (imm[19:12] == insn[19:12]);
      assert (imm[11]    == insn[20]);
      assert (imm[10:1]  == insn[30:21]);
    end
  end
`endif

endmodule

`default_nettype wire
