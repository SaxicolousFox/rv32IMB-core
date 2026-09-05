// ============================================================================
// rvntt_alu -- the RV32I integer ALU (plan A2).
//
// Purely combinational.  ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND,
// plus ALU_PASS_B, which carries LUI's immediate straight through instead of
// forcing it onto the adder.
//
// Two things here are the usual sources of silent wrongness:
//
//   * The shift amount is b[4:0], NOT all of b.  RV32I defines shifts to use
//     only the low five bits of the source, so `sll x1, x2, x3` with x3 = 32
//     is a no-op, not a zeroing.  Using the full operand would also infer a
//     32-bit-wide shifter and cost fabric for behaviour the ISA forbids.
//
//   * SRA needs a SIGNED left operand.  `a >>> b` on an unsigned `logic`
//     vector is an ordinary logical shift in SystemVerilog -- the `>>>`
//     operator does not by itself make the shift arithmetic.  This is the bug
//     plan A5 suggests injecting to prove the cosim differ works, which is a
//     fair hint about how often it is written wrong.
//
// No branch comparator here.  Plan §1.6 resolves branches in EX and A8 owns
// that logic; keeping it out of the ALU means the branch path does not
// inherit the operation mux delay.
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

module rvntt_alu
(
    input  wire  rv32i_pkg::alu_op_e op,
    input  wire  logic [31:0] a,
    input  wire  logic [31:0] b,
    output logic [31:0]       y
);

  // Only the low five bits, per the ISA.
  wire [4:0] shamt = b[4:0];

  always_comb begin
    unique case (op)
      rv32i_pkg::ALU_ADD:    y = a + b;
      rv32i_pkg::ALU_SUB:    y = a - b;
      rv32i_pkg::ALU_SLL:    y = a << shamt;
      rv32i_pkg::ALU_SLT:    y = {31'b0, ($signed(a) < $signed(b))};
      rv32i_pkg::ALU_SLTU:   y = {31'b0, (a < b)};
      rv32i_pkg::ALU_XOR:    y = a ^ b;
      rv32i_pkg::ALU_SRL:    y = a >> shamt;
      rv32i_pkg::ALU_SRA:    y = $unsigned($signed(a) >>> shamt);
      rv32i_pkg::ALU_OR:     y = a | b;
      rv32i_pkg::ALU_AND:    y = a & b;
      rv32i_pkg::ALU_PASS_B: y = b;
      // alu_op_e is 4 bits wide but has 11 members, so the remaining five
      // encodings are unreachable by construction -- the decoder only ever
      // assigns a named member.  A default arm is still required: without one
      // this always_comb would infer a latch on those codes, and `unique`
      // only warns at simulation time, it does not synthesise a value.
      default:    y = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Every operation is checked against a SECOND, independently-written
  // expression.  These are deliberately not copies of the always_comb above:
  // SUB is stated as two's-complement addition, SLT as a sign-aware case split
  // on the operand signs, SLTU as the borrow-out of a 33-bit subtraction, and
  // SRA as a right shift of a 64-bit sign-extended value.  A wrong answer would
  // have to be written twice, in two different idioms, to survive.

  // 33-bit subtraction: bit 32 is the borrow, i.e. exactly a < b unsigned.
  wire [32:0] f_sub33 = {1'b0, a} - {1'b0, b};

  // Signed less-than without using the `<` operator on signed operands: when
  // the sign bits differ the negative one is smaller, otherwise the unsigned
  // comparison already gives the right answer.
  wire f_slt = (a[31] != b[31]) ? a[31] : f_sub33[32];

  // Arithmetic right shift as a logical shift of a sign-extended 64-bit value.
  wire [63:0] f_sext64 = {{32{a[31]}}, a};
  wire [63:0] f_sra64  = f_sext64 >> shamt;

  always_comb begin
    if (op == rv32i_pkg::ALU_ADD)  assert (y == a + b);
    if (op == rv32i_pkg::ALU_SUB)  assert (y == a + (~b) + 32'd1);
    if (op == rv32i_pkg::ALU_SLT)  assert (y == {31'b0, f_slt});
    if (op == rv32i_pkg::ALU_SLTU) assert (y == {31'b0, f_sub33[32]});
    if (op == rv32i_pkg::ALU_XOR)  assert (y == ((a | b) & ~(a & b)));
    if (op == rv32i_pkg::ALU_OR)   assert (y == ~((~a) & (~b)));
    if (op == rv32i_pkg::ALU_AND)  assert (y == ~((~a) | (~b)));
    if (op == rv32i_pkg::ALU_SRA)  assert (y == f_sra64[31:0]);
    // Shifts must ignore b[31:5] entirely.
    if (op == rv32i_pkg::ALU_SLL)  assert (y == (a << b[4:0]));
    if (op == rv32i_pkg::ALU_SRL)  assert (y == (a >> b[4:0]));
  end

  // Structural sanity nets, independent of the mirrored expressions above: an
  // arithmetic right shift can never turn a negative value positive, and a
  // logical right shift by a nonzero amount can never leave bit 31 set.  These
  // catch a signedness slip even in the case where both formulations were
  // written wrong the same way.
  always_comb begin
    if (op == rv32i_pkg::ALU_SRA && a[31])   assert (y[31]);
    if (op == rv32i_pkg::ALU_SRL && shamt != 5'd0) assert (!y[31]);
  end
`endif

endmodule

`default_nettype wire
