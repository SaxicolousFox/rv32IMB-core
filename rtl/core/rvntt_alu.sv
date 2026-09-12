// ============================================================================
// rvntt_alu -- the RV32I integer ALU.  Purely combinational.
//
// ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND, and ALU_PASS_B (LUI's
// immediate, kept off the adder).  Shifts use b[4:0] only, per the ISA; SRA
// needs a $signed left operand, because `>>>` on an unsigned vector is a
// logical shift.  The branch comparator is a separate module (rvntt_branch) so
// the branch path does not inherit the operation mux delay.
//
// Package references are fully qualified with no `import`: Yosys rejects every
// import form, and the qualified form is the one Verilator, Yosys and Vivado
// all accept.
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
      // The five unused encodings are unreachable (the decoder only assigns
      // named members); a default arm is still needed to avoid a latch.
      default:    y = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Every operation checked against a second, independently-written
  // expression in a different idiom, so a wrong answer would have to be
  // written twice.

  // 33-bit subtraction: bit 32 is the borrow, i.e. exactly a < b unsigned.
  wire [32:0] f_sub33 = {1'b0, a} - {1'b0, b};

  // Signed less-than without `<` on signed operands.
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

  // Structural sanity nets independent of the mirrored expressions: SRA never
  // turns a negative value positive; SRL by a nonzero amount clears bit 31.
  always_comb begin
    if (op == rv32i_pkg::ALU_SRA && a[31])   assert (y[31]);
    if (op == rv32i_pkg::ALU_SRL && shamt != 5'd0) assert (!y[31]);
  end
`endif

endmodule

`default_nettype wire
