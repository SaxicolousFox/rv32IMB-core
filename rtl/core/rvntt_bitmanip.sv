// ============================================================================
// rvntt_bitmanip -- B (Zba + Zbb + Zbs), Zbkb and Zicond.
//
// Purely combinational, one result.  Deliberately not in rvntt_alu: the ALU
// result mux feeds the jump target and the mispredict compare, so widening it
// would lengthen the fetch redirect.  This unit joins at ex_result, which
// terminates at the EX/MEM register.  The shift/rotate/bit-index amount is
// b[4:0]; for the immediate forms the core drives `b` from the I-immediate.
//
// Package references are fully qualified with no `import` (Yosys).
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

  // A rotate is two shifts and an OR.  `32 - shamt` as a 5-bit negation so
  // that shamt == 0 gives a shift of 0 rather than 32 (a >> 32 is zero).
  wire [4:0] rshamt = 5'd0 - shamt;
  wire [31:0] rol_v = (a << shamt) | (a >> rshamt);
  wire [31:0] ror_v = (a >> shamt) | (a << rshamt);

  // Count leading/trailing zeros as last-write-wins priority encoders (no
  // `break`: Yosys rejects it).  clz(0) == ctz(0) == 32 per the spec.
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

  // orc.b: each byte becomes 0xFF if any of its bits is set, else 0x00.
  logic [31:0] orcb_v;
  always_comb
    for (int i = 0; i < 4; i++)
      orcb_v[i*8 +: 8] = {8{|a[i*8 +: 8]}};

  // rev8 reverses bytes; brev8 reverses bits within each byte.
  wire [31:0] rev8_v = {a[7:0], a[15:8], a[23:16], a[31:24]};

  logic [31:0] brev8_v;
  always_comb
    for (int i = 0; i < 4; i++)
      for (int j = 0; j < 8; j++)
        brev8_v[i*8 + j] = a[i*8 + (7 - j)];

  // zip/unzip are RV32-only (Zbkb) and exact inverses.
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

      // Zicond: `b` is rs2 (both forms are R-type), so the zero test is on the
      // whole operand, not b[4:0].
      rv32i_pkg::BM_CZEQZ:  y = (b == 32'd0) ? 32'd0 : a;
      rv32i_pkg::BM_CZNEZ:  y = (b != 32'd0) ? 32'd0 : a;

      // BM_NONE and the unused codes are unreachable; a default arm avoids a latch.
      default:              y = 32'b0;
    endcase
  end

`ifdef FORMAL
  // Every operation checked against a second, independently-written
  // expression, or a structural property where the natural second form is
  // the first form.

  // Rotates as a slice of the doubled word.
  wire [63:0] f_dbl   = {a, a};
  wire [31:0] f_ror   = f_dbl[shamt +: 32];
  wire [31:0] f_rol   = f_dbl[(6'd32 - {1'b0, shamt}) +: 32];

  // ctz via the isolate-lowest-set-bit identity.
  wire [31:0] f_lowbit = a & (~a + 32'd1);

  always_comb begin
    if (op == rv32i_pkg::BM_ANDN)   assert (y == ~((~a) | b));
    if (op == rv32i_pkg::BM_ORN)    assert (y == ~((~a) & b));
    if (op == rv32i_pkg::BM_XNOR)   assert (y == ((a & b) | (~a & ~b)));

    if (op == rv32i_pkg::BM_ROR)    assert (y == f_ror);
    if (op == rv32i_pkg::BM_ROL)    assert (y == f_rol);
    // A rotate by zero is the identity.
    if (op == rv32i_pkg::BM_ROR && shamt == 5'd0) assert (y == a);
    if (op == rv32i_pkg::BM_ROL && shamt == 5'd0) assert (y == a);

    // clz(0) = ctz(0) = 32 by definition.
    if (op == rv32i_pkg::BM_CLZ && a == 32'd0)  assert (y == 32'd32);
    if (op == rv32i_pkg::BM_CTZ && a == 32'd0)  assert (y == 32'd32);
    if (op == rv32i_pkg::BM_CPOP && a == 32'd0) assert (y == 32'd0);
    if (op == rv32i_pkg::BM_CPOP && a == 32'hFFFF_FFFF) assert (y == 32'd32);
    // ctz is the index of the isolated lowest set bit.
    if (op == rv32i_pkg::BM_CTZ && a != 32'd0)
      assert (f_lowbit == (32'd1 << y[4:0]));
    // clz names a bit that is set.
    if (op == rv32i_pkg::BM_CLZ && a != 32'd0)
      assert (a[31 - y[4:0]]);

    if (op == rv32i_pkg::BM_MIN)  assert (y == (($signed(a) < $signed(b)) ? a : b));
    if (op == rv32i_pkg::BM_MAX)  assert (y == (($signed(a) < $signed(b)) ? b : a));
    // Structural: min and max are one of the two operands, and min <= max.
    if (op == rv32i_pkg::BM_MINU) assert (y == a || y == b);
    if (op == rv32i_pkg::BM_MAXU) assert (y == a || y == b);
    if (op == rv32i_pkg::BM_MINU) assert (y <= a && y <= b);
    if (op == rv32i_pkg::BM_MAXU) assert (y >= a && y >= b);

    if (op == rv32i_pkg::BM_SEXTB) assert (y == 32'($signed(a[7:0])));
    if (op == rv32i_pkg::BM_SEXTH) assert (y == 32'($signed(a[15:0])));
    if (op == rv32i_pkg::BM_ZEXTH) assert (y[31:16] == 16'b0 && y[15:0] == a[15:0]);

    // Single-bit ops: exactly one bit differs, and it is bit shamt.
    if (op == rv32i_pkg::BM_BSET) assert (y == (a | (32'd1 << shamt)));
    if (op == rv32i_pkg::BM_BCLR) assert ((y ^ a) == (a & (32'd1 << shamt)));
    if (op == rv32i_pkg::BM_BINV) assert ((y ^ a) == (32'd1 << shamt));
    if (op == rv32i_pkg::BM_BEXT) assert (y == {31'b0, a[shamt]});
    if (op == rv32i_pkg::BM_BSET) assert (y[shamt]);
    if (op == rv32i_pkg::BM_BCLR) assert (!y[shamt]);

    if (op == rv32i_pkg::BM_PACK)  assert (y == {b[15:0], a[15:0]});
    if (op == rv32i_pkg::BM_PACKH) assert (y == {16'b0, b[7:0], a[7:0]});

    // rev8 is an involution: the reverse of the result is the input.
    if (op == rv32i_pkg::BM_REV8)
      assert ({y[7:0], y[15:8], y[23:16], y[31:24]} == a);
    // orc.b: a result byte is all-ones or all-zeros, zero iff the input byte was.
    if (op == rv32i_pkg::BM_ORCB) begin
      for (int i = 0; i < 4; i++) begin
        assert (y[i*8 +: 8] == 8'h00 || y[i*8 +: 8] == 8'hFF);
        assert ((y[i*8 +: 8] == 8'h00) == (a[i*8 +: 8] == 8'h00));
      end
    end
    // zip and unzip, bit by bit.
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
    // brev8 never moves a bit across a byte boundary.
    if (op == rv32i_pkg::BM_BREV8)
      for (int i = 0; i < 4; i++)
        for (int j = 0; j < 8; j++)
          assert (y[i*8 + j] == a[i*8 + 7 - j]);

    // Zicond as a mask rather than a select; the two are exact complements.
    if (op == rv32i_pkg::BM_CZEQZ) assert (y == (a & {32{|b}}));
    if (op == rv32i_pkg::BM_CZNEZ) assert (y == (a & {32{~(|b)}}));
    if (op == rv32i_pkg::BM_CZEQZ) assert (y == a || y == 32'd0);
    if (op == rv32i_pkg::BM_CZNEZ) assert (y == a || y == 32'd0);
    // The zero test is on all 32 bits, not b[4:0].
    if (op == rv32i_pkg::BM_CZEQZ && b[4:0] == 5'd0 && b != 32'd0)
      assert (y == a);

    // Zba as a multiply rather than a shift.
    if (op == rv32i_pkg::BM_SH1ADD) assert (y == (a * 32'd2) + b);
    if (op == rv32i_pkg::BM_SH2ADD) assert (y == (a * 32'd4) + b);
    if (op == rv32i_pkg::BM_SH3ADD) assert (y == (a * 32'd8) + b);
  end
`endif

endmodule

`default_nettype wire
