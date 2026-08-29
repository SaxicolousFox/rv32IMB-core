// ============================================================================
// rvntt_branch -- the EX-stage branch comparator (plan A8).
//
// Six conditions on two 32-bit words.  Kept as its own module rather than
// inlined in rvntt_core for the same reason rvntt_forward and rvntt_hazard are:
// it can then be proved on its own, mutated on its own, and the proof does not
// have to reason about a pipeline.
//
// funct3 COMES FROM THE INSTRUCTION WORD, not from a decoder output.  That is a
// deliberate call.  `ctrl_t` has no funct3 field -- `mem_op` carries funct3 but
// only for loads and stores, and widening the decoder's contract would mean
// changing model/isa's frozen ctrl bundle to suit the RTL.  The instruction
// word is already carried down the pipeline for the commit trace, so taking
// three bits from it costs nothing.  What makes it safe is that the decoder has
// already REJECTED the two reserved encodings (funct3 010 and 011 are illegal
// for BRANCH) and `ctrl.branch` gates this module's output in EX, so the only
// values that can matter here are the six the decoder has blessed.  The
// reserved values are still driven to `taken = 0` rather than left as a
// don't-care, so a decoder bug cannot turn into a wild jump.
//
// SIGNED VERSUS UNSIGNED is the whole content of this module, and the
// properties below are written to attack exactly that: BLT and BLTU on the same
// operands must disagree whenever the sign bits differ.  A comparator that uses
// one for both passes every test built from small positive numbers.
//
// Package references are fully qualified with no `import`; Yosys rejects every
// import form (rtl/core/CLAUDE.md).
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
  // Each condition is restated in a DIFFERENT idiom, as rvntt_alu's proof does.
  // Repeating `$signed(a) < $signed(b)` would prove only that it was typed
  // twice; the point of a second expression is that a wrong operator or a
  // wrong sign convention cannot be present in both.
  //
  //   equality      -> a reduction OR over the bitwise difference
  //   signed <      -> an unsigned compare of the sign-bit-inverted words,
  //                    which is the standard bias trick and shares no operator
  //                    with $signed
  //   unsigned <    -> the borrow out of a 33-bit subtraction
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

    // SIGNEDNESS.  When the two operands' sign bits differ, the signed and
    // unsigned answers are always opposite -- so a comparator that uses one
    // for both cannot satisfy this, however it is written.
    a_signedness: assert (!(a[31] ^ b[31]) || (f_lt != f_ltu));

    // Guards on the REFERENCE expressions themselves.  The lesson from A6 was
    // that a proof can quietly check a design against a copy of its own
    // mistake; these are cheap facts about the three relations that a mistyped
    // reference -- `<=` where `<` was meant, or a borrow taken from the wrong
    // bit -- would violate on its own, with no reference to the DUT at all.
    a_ref_lt_ne:  assert (!f_lt  || !f_eq);
    a_ref_ltu_ne: assert (!f_ltu || !f_eq);
    a_ref_eq:     assert (!f_eq  || (!f_lt && !f_ltu));
  end
`endif

endmodule

`default_nettype wire
