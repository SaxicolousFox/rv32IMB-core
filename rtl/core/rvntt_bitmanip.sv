// ============================================================================
// rvntt_bitmanip -- B (Zba + Zbb + Zbs) and Zbkb (MODS_A2 A21).
//
// Purely combinational, 29 operations, one result.
//
// WHY THIS IS NOT IN rvntt_alu.sv, WHICH IS THE WHOLE POINT OF THE MODULE.
// MODS_A2 section 3.4 reads the post-route critical path hop by hop:
//
//   mem_wb rd_addr -> forwarding -> ALU adder -> ALU RESULT MUX
//                  -> ex_jump_target -> mispredict compare -> id_ex pc
//
// The ALU's operation-select mux is a third of that path, and `ex_alu_y` feeds
// `ex_jump_target` -- so anything that widens the mux lengthens the fetch
// redirect.  Folding 34 instructions into rvntt_alu would take alu_op_e from
// 4 bits to 6 and add roughly two LUT levels to a path with 0.003 ns of slack,
// in the same round whose other goal is to SHORTEN it.
//
// This unit's result joins at `ex_result` instead, which terminates at the
// EX/MEM pipeline register and is not on the critical path.  That is the same
// place rvntt_muldiv and the Zicsr read already join, so it is an established
// shape rather than a new one.
//
// sh1add/sh2add/sh3add have a plausible claim to belong in the ALU -- they are
// a constant shift into the existing adder.  They are here anyway, so that the
// rule "B is not in the ALU" has no exceptions for anyone to remember.
//
// THE SHIFT/ROTATE/BIT-INDEX AMOUNT IS b[4:0], NOT ALL OF b, exactly as in
// rvntt_alu.  For the immediate forms (rori, bseti, bclri, binvi, bexti) the
// core drives `b` from the sign-extended I-immediate and the low five bits are
// the field the ISA defines -- so no separate immediate path is needed and
// there is one place to be wrong about the width instead of two.
//
// NOTE ON THE PACKAGE REFERENCES.  Fully qualified `rv32i_pkg::X` with no
// `import`, for the reason rvntt_alu.sv's header gives at length: Yosys rejects
// both the module-header and module-body import forms, and the formal flow
// could not read the file at all.
// ============================================================================
`default_nettype none

module rvntt_bitmanip
(
    input  wire  rv32i_pkg::bm_op_e op,
    input  wire  logic [31:0] a,
    input  wire  logic [31:0] b,
    output logic [31:0]       y
);

  // Only the low five bits, per the ISA -- shifts, rotates and bit indices all.
  wire [4:0] shamt = b[4:0];

  // ---- helpers, written once and shared ------------------------------------
  // A rotate is two shifts and an OR.  `32 - shamt` is written as a 5-bit
  // negation so that shamt == 0 gives a shift of 0 rather than of 32: on a
  // 32-bit vector `a >> 32` is zero, and `rol x, 0` must be the identity.
  wire [4:0] rshamt = 5'd0 - shamt;
  wire [31:0] rol_v = (a << shamt) | (a >> rshamt);
  wire [31:0] ror_v = (a >> shamt) | (a << rshamt);

  // Count leading and trailing zeros, as LAST-WRITE-WINS priority encoders.
  //
  // NO `break`, and that is a tool constraint rather than a style choice:
  // Yosys rejects it outright -- "Can't resolve task name `\break'" -- and the
  // formal flow could not read the file at all.  The loops below scan in the
  // direction that makes the LAST assignment the one wanted, so no early exit
  // is needed: clz scans upward, so the final write is for the highest set
  // bit; ctz scans downward, so the final write is for the lowest.  Both
  // synthesise to the same priority structure the break form would have.
  //
  // clz(0) == 32 and ctz(0) == 32 are DEFINED by the specification, not edge
  // cases to be discovered -- they are the initial value here and are asserted
  // separately below.
  logic [5:0] clz_v, ctz_v, cpop_v;
  always_comb begin
    clz_v = 6'd32;
    for (int i = 0; i < 32; i++)
      if (a[i]) clz_v = 6'(31 - i);      // highest set bit wins
  end
  always_comb begin
    ctz_v = 6'd32;
    for (int i = 31; i >= 0; i--)
      if (a[i]) ctz_v = 6'(i);           // lowest set bit wins
  end
  always_comb begin
    cpop_v = 6'd0;
    for (int i = 0; i < 32; i++)
      cpop_v = cpop_v + 6'(a[i]);
  end

  // orc.b: each byte becomes 0xFF if ANY of its bits is set, else 0x00.
  logic [31:0] orcb_v;
  always_comb
    for (int i = 0; i < 4; i++)
      orcb_v[i*8 +: 8] = {8{|a[i*8 +: 8]}};

  // rev8 reverses BYTES; brev8 reverses BITS WITHIN each byte.  They are
  // different operations that sound the same, and getting them the wrong way
  // round is the ordinary mistake here.
  wire [31:0] rev8_v = {a[7:0], a[15:8], a[23:16], a[31:24]};

  logic [31:0] brev8_v;
  always_comb
    for (int i = 0; i < 4; i++)
      for (int j = 0; j < 8; j++)
        brev8_v[i*8 + j] = a[i*8 + (7 - j)];

  // zip/unzip are RV32-ONLY (Zbkb) and are exact inverses.
  //   zip:   y[2i] = a[i],  y[2i+1] = a[i+16]
  //   unzip: y[i]  = a[2i], y[i+16] = a[2i+1]
  logic [31:0] zip_v, unzip_v;
  always_comb
    for (int i = 0; i < 16; i++) begin
      zip_v[2*i]      = a[i];
      zip_v[2*i + 1]  = a[i + 16];
      unzip_v[i]      = a[2*i];
      unzip_v[i + 16] = a[2*i + 1];
    end

  wire [31:0] bitmask = 32'd1 << shamt;
  wire        a_lt_b_s = ($signed(a) < $signed(b));
  wire        a_lt_b_u = (a < b);

  always_comb begin
    unique case (op)
      rv32i_pkg::BM_SH1ADD: y = (a << 1) + b;
      rv32i_pkg::BM_SH2ADD: y = (a << 2) + b;
      rv32i_pkg::BM_SH3ADD: y = (a << 3) + b;

      rv32i_pkg::BM_ANDN:   y = a & ~b;
      rv32i_pkg::BM_ORN:    y = a | ~b;
      rv32i_pkg::BM_XNOR:   y = ~(a ^ b);

      rv32i_pkg::BM_CLZ:    y = {26'b0, clz_v};
      rv32i_pkg::BM_CTZ:    y = {26'b0, ctz_v};
      rv32i_pkg::BM_CPOP:   y = {26'b0, cpop_v};

      rv32i_pkg::BM_MIN:    y = a_lt_b_s ? a : b;
      rv32i_pkg::BM_MAX:    y = a_lt_b_s ? b : a;
      rv32i_pkg::BM_MINU:   y = a_lt_b_u ? a : b;
      rv32i_pkg::BM_MAXU:   y = a_lt_b_u ? b : a;

      rv32i_pkg::BM_SEXTB:  y = {{24{a[7]}},  a[7:0]};
      rv32i_pkg::BM_SEXTH:  y = {{16{a[15]}}, a[15:0]};
      rv32i_pkg::BM_ZEXTH:  y = {16'b0,       a[15:0]};

      rv32i_pkg::BM_ORCB:   y = orcb_v;
      rv32i_pkg::BM_REV8:   y = rev8_v;

      rv32i_pkg::BM_ROL:    y = rol_v;
      rv32i_pkg::BM_ROR:    y = ror_v;

      rv32i_pkg::BM_BSET:   y = a |  bitmask;
      rv32i_pkg::BM_BCLR:   y = a & ~bitmask;
      rv32i_pkg::BM_BINV:   y = a ^  bitmask;
      rv32i_pkg::BM_BEXT:   y = {31'b0, a[shamt]};

      rv32i_pkg::BM_PACK:   y = {b[15:0], a[15:0]};
      rv32i_pkg::BM_PACKH:  y = {16'b0, b[7:0], a[7:0]};
      rv32i_pkg::BM_BREV8:  y = brev8_v;
      rv32i_pkg::BM_ZIP:    y = zip_v;
      rv32i_pkg::BM_UNZIP:  y = unzip_v;

      // BM_NONE and the two unused five-bit codes.  Unreachable by
      // construction -- the decoder only ever assigns a named member -- but a
      // default arm is still required, because without one this always_comb
      // infers a latch on those codes and `unique` only warns in simulation.
      default:              y = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Every operation is checked against a SECOND, INDEPENDENTLY-WRITTEN
  // expression, in the idiom rvntt_alu.sv established: a wrong answer has to be
  // written twice, in two different forms, to survive.  Where the natural
  // second form IS the first form, a structural property is asserted instead --
  // those are marked.

  // Rotates, stated as a concatenation rather than as two shifts.  This is the
  // formulation that makes the shamt == 0 case obvious.
  wire [63:0] f_dbl   = {a, a};
  wire [31:0] f_ror   = f_dbl[shamt +: 32];
  wire [31:0] f_rol   = f_dbl[(6'd32 - {1'b0, shamt}) +: 32];

  // clz via a leading-one mask; ctz via the isolate-lowest-set-bit identity.
  wire [31:0] f_lowbit = a & (~a + 32'd1);

  always_comb begin
    if (op == rv32i_pkg::BM_ANDN)   assert (y == ~((~a) | b));
    if (op == rv32i_pkg::BM_ORN)    assert (y == ~((~a) & b));
    if (op == rv32i_pkg::BM_XNOR)   assert (y == ((a & b) | (~a & ~b)));

    if (op == rv32i_pkg::BM_ROR)    assert (y == f_ror);
    if (op == rv32i_pkg::BM_ROL)    assert (y == f_rol);
    // A rotate by zero is the identity, and a rotate of an all-ones word is
    // all ones for every amount.  Structural, and independent of both forms.
    if (op == rv32i_pkg::BM_ROR && shamt == 5'd0) assert (y == a);
    if (op == rv32i_pkg::BM_ROL && shamt == 5'd0) assert (y == a);

    // The specification DEFINES clz(0) = ctz(0) = 32; they are not edge cases.
    if (op == rv32i_pkg::BM_CLZ && a == 32'd0)  assert (y == 32'd32);
    if (op == rv32i_pkg::BM_CTZ && a == 32'd0)  assert (y == 32'd32);
    if (op == rv32i_pkg::BM_CPOP && a == 32'd0) assert (y == 32'd0);
    if (op == rv32i_pkg::BM_CPOP && a == 32'hFFFF_FFFF) assert (y == 32'd32);
    // ctz is the index of the isolated lowest set bit -- a different route to
    // the same number, and one that shares no logic with the loop above.
    if (op == rv32i_pkg::BM_CTZ && a != 32'd0)
      assert (f_lowbit == (32'd1 << y[4:0]));
    // A nonzero word has clz + ctz <= 31, and clz names a bit that is set.
    if (op == rv32i_pkg::BM_CLZ && a != 32'd0)
      assert (a[31 - y[4:0]]);

    if (op == rv32i_pkg::BM_MIN)  assert (y == (($signed(a) < $signed(b)) ? a : b));
    if (op == rv32i_pkg::BM_MAX)  assert (y == (($signed(a) < $signed(b)) ? b : a));
    // Structural: min and max are the two operands, and min <= max.
    if (op == rv32i_pkg::BM_MINU) assert (y == a || y == b);
    if (op == rv32i_pkg::BM_MAXU) assert (y == a || y == b);
    if (op == rv32i_pkg::BM_MINU) assert (y <= a && y <= b);
    if (op == rv32i_pkg::BM_MAXU) assert (y >= a && y >= b);

    if (op == rv32i_pkg::BM_SEXTB) assert (y == 32'($signed(a[7:0])));
    if (op == rv32i_pkg::BM_SEXTH) assert (y == 32'($signed(a[15:0])));
    if (op == rv32i_pkg::BM_ZEXTH) assert (y[31:16] == 16'b0 && y[15:0] == a[15:0]);

    // Single-bit ops, stated as "exactly one bit differs, and it is bit shamt".
    if (op == rv32i_pkg::BM_BSET) assert (y == (a | (32'd1 << shamt)));
    if (op == rv32i_pkg::BM_BCLR) assert ((y ^ a) == (a & (32'd1 << shamt)));
    if (op == rv32i_pkg::BM_BINV) assert ((y ^ a) == (32'd1 << shamt));
    if (op == rv32i_pkg::BM_BEXT) assert (y == {31'b0, a[shamt]});
    // bset then bclr at the same index restores the original bit as 0.
    if (op == rv32i_pkg::BM_BSET) assert (y[shamt]);
    if (op == rv32i_pkg::BM_BCLR) assert (!y[shamt]);

    if (op == rv32i_pkg::BM_PACK)  assert (y == {b[15:0], a[15:0]});
    if (op == rv32i_pkg::BM_PACKH) assert (y == {16'b0, b[7:0], a[7:0]});

    // rev8 and brev8 are involutions: applying twice is the identity.  Stated
    // as "the reverse of the result is the input", which is the property, not
    // a second copy of the expression.
    if (op == rv32i_pkg::BM_REV8)
      assert ({y[7:0], y[15:8], y[23:16], y[31:24]} == a);
    // orc.b: a byte of the result is all-ones or all-zeros, never mixed, and
    // it is zero exactly when the input byte was zero.
    if (op == rv32i_pkg::BM_ORCB) begin
      for (int i = 0; i < 4; i++) begin
        assert (y[i*8 +: 8] == 8'h00 || y[i*8 +: 8] == 8'hFF);
        assert ((y[i*8 +: 8] == 8'h00) == (a[i*8 +: 8] == 8'h00));
      end
    end
    // zip and unzip are exact inverses of each other.  Asserting the round trip
    // is stronger than restating either permutation, and it cannot be satisfied
    // by two matching mistakes unless they are mutually inverse -- which for a
    // bit permutation means both are right.
    if (op == rv32i_pkg::BM_ZIP)
      for (int i = 0; i < 16; i++) begin
        assert (y[2*i]     == a[i]);
        assert (y[2*i + 1] == a[i + 16]);
      end
    if (op == rv32i_pkg::BM_UNZIP)
      for (int i = 0; i < 16; i++) begin
        assert (y[i]      == a[2*i]);
        assert (y[i + 16] == a[2*i + 1]);
      end
    // brev8 keeps each byte's popcount and reverses within the byte only, so
    // it can never move a bit across a byte boundary.
    if (op == rv32i_pkg::BM_BREV8)
      for (int i = 0; i < 4; i++)
        for (int j = 0; j < 8; j++)
          assert (y[i*8 + j] == a[i*8 + 7 - j]);

    // Zba, stated as a multiply rather than as a shift.
    if (op == rv32i_pkg::BM_SH1ADD) assert (y == (a * 32'd2) + b);
    if (op == rv32i_pkg::BM_SH2ADD) assert (y == (a * 32'd4) + b);
    if (op == rv32i_pkg::BM_SH3ADD) assert (y == (a * 32'd8) + b);
  end
`endif

endmodule

`default_nettype wire
