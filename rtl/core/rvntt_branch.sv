// ============================================================================
// rvntt_branch -- the EX-stage branch comparator.
//
// funct3 comes from the instruction word (ctrl_t carries no funct3); it is
// safe because the decoder rejects the two reserved encodings and ctrl.branch
// gates the output.  The reserved values are still driven to not-taken.
//
// Package references are fully qualified with no `import` (Yosys).
// ============================================================================
`default_nettype none

module rvntt_branch (
    input  wire  [2:0]  funct3,
    input  wire  [31:0] a,
    input  wire  [31:0] b,
    output logic        taken
);

  wire eq  = (a == b);
  wire lt  = ($signed(a) < $signed(b));
  wire ltu = (a < b);

  always_comb begin
    unique case (funct3)
      rv32i_pkg::F3_BEQ:  taken =  eq;
      rv32i_pkg::F3_BNE:  taken = !eq;
      rv32i_pkg::F3_BLT:  taken =  lt;
      rv32i_pkg::F3_BGE:  taken = !lt;
      rv32i_pkg::F3_BLTU: taken =  ltu;
      rv32i_pkg::F3_BGEU: taken = !ltu;
      default:            taken = 1'b0;   // 010 and 011: reserved, and illegal
    endcase
  end

`ifdef FORMAL
  // Each condition restated in a different idiom: equality as a reduction OR
  // over the XOR, signed < as an unsigned compare of sign-bit-inverted words,
  // unsigned < as the borrow of a 33-bit subtraction.
  wire        f_eq  = ~(|(a ^ b));
  wire        f_lt  = ({~a[31], a[30:0]} < {~b[31], b[30:0]});
  wire [32:0] f_dif = {1'b0, a} - {1'b0, b};
  wire        f_ltu = f_dif[32];

  always_comb begin
    a_beq:  assert (funct3 != rv32i_pkg::F3_BEQ  || taken ==  f_eq);
    a_bne:  assert (funct3 != rv32i_pkg::F3_BNE  || taken == !f_eq);
    a_blt:  assert (funct3 != rv32i_pkg::F3_BLT  || taken ==  f_lt);
    a_bge:  assert (funct3 != rv32i_pkg::F3_BGE  || taken == !f_lt);
    a_bltu: assert (funct3 != rv32i_pkg::F3_BLTU || taken ==  f_ltu);
    a_bgeu: assert (funct3 != rv32i_pkg::F3_BGEU || taken == !f_ltu);

    // The reserved encodings never take a branch.
    a_reserved: assert ((funct3 != 3'b010 && funct3 != 3'b011) || !taken);

    // When the sign bits differ, signed and unsigned answers are opposite.
    a_signedness: assert (!(a[31] ^ b[31]) || (f_lt != f_ltu));

    // Guards on the reference expressions themselves.
    a_ref_lt_ne:  assert (!f_lt  || !f_eq);
    a_ref_ltu_ne: assert (!f_ltu || !f_eq);
    a_ref_eq:     assert (!f_eq  || (!f_lt && !f_ltu));
  end
`endif

endmodule

`default_nettype wire
