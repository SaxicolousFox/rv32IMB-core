// ============================================================================
// rvntt_immgen -- RV32I immediate generation.  Purely combinational.
//
// I, S, B, U and J immediates plus the Zicsr 5-bit uimm (IMM_Z).
//   B: imm[12|10:5] = insn[31:25],  imm[4:1|11] = insn[11:7],  imm[0] = 0
//   J: imm[20|10:1|11|19:12] = insn[31:12],                    imm[0] = 0
// B and J bit 0 is hardwired zero (halfword-aligned targets).  I, S, B and J
// sign-extend from insn[31]; U is the top 20 bits with 12 zeros below.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_immgen
(
    // insn[6:0] (the opcode) is unused here: the format is already selected
    // by `fmt`.  The whole word is passed so the scrambling lives in one place.
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
      // IMM_NONE and the unused seventh encoding; a default arm avoids a latch.
      default: imm = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Sign extension from insn[31] for every signed format.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_I) assert (imm[31:11] == {21{insn[31]}});
    if (fmt == rv32i_pkg::IMM_S) assert (imm[31:11] == {21{insn[31]}});
    if (fmt == rv32i_pkg::IMM_B) assert (imm[31:12] == {20{insn[31]}});
    if (fmt == rv32i_pkg::IMM_J) assert (imm[31:20] == {12{insn[31]}});
  end

  // Branch and jump targets are halfword-aligned.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_B) assert (imm[0] == 1'b0);
    if (fmt == rv32i_pkg::IMM_J) assert (imm[0] == 1'b0);
  end

  // U: top 20 bits, zeros below, not sign-extended.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_U) assert (imm[11:0] == 12'b0);
    if (fmt == rv32i_pkg::IMM_U) assert (imm[31:12] == insn[31:12]);
  end

  // Zicsr uimm is zero-extended.
  always_comb begin
    if (fmt == rv32i_pkg::IMM_Z) assert (imm[31:5] == 27'b0);
    if (fmt == rv32i_pkg::IMM_Z) assert (imm[4:0] == insn[19:15]);
  end

  // Bit-for-bit placement of every source bit, unscrambled formats included:
  // a proof that only checked sign extension let a wrong IMM_S source field
  // through (found by fault injection).
  always_comb begin
    if (fmt == rv32i_pkg::IMM_I) assert (imm[11:0] == insn[31:20]);
    if (fmt == rv32i_pkg::IMM_S) begin
      assert (imm[11:5] == insn[31:25]);
      assert (imm[4:0]  == insn[11:7]);
    end
  end

  // The scrambled formats as individual bit equalities, not a concatenation.
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
