// ============================================================================
// rvntt_regfile -- the RV32I architectural register file.
//
// 32 x 32 bits, x0 hardwired to zero, two read ports, one write port, with
// write-through on a same-cycle read/write of the same address (which makes a
// WB-stage write visible to an ID-stage read and removes the WB->ID
// forwarding case).  Package-free and parameterised.
//
// No reset port: the array powers up at zero via an explicit initial value
// (the FF/LUTRAM INIT attribute on Xilinx), which matches Spike's state at
// reset and does not block distributed-RAM inference.
// ============================================================================
`default_nettype none

module rvntt_regfile #(
    parameter int XLEN  = 32,
    parameter int NREGS = 32
) (
    input  wire                     clk,

    // Read ports (combinational, with write-through).
    input  wire [$clog2(NREGS)-1:0] ra1,
    input  wire [$clog2(NREGS)-1:0] ra2,
    output wire [XLEN-1:0]          rd1,
    output wire [XLEN-1:0]          rd2,

    // Write port.
    input  wire                     we,
    input  wire [$clog2(NREGS)-1:0] wa,
    input  wire [XLEN-1:0]          wd
);

  localparam int AW = $clog2(NREGS);

  // Power-on state as an `initial` loop: Yosys does not parse the
  // `'{default: '0}` declaration form, and the loop is what Vivado infers RAM
  // initialisation from.
  logic [XLEN-1:0] regs [0:NREGS-1];
  initial begin
    for (int i = 0; i < NREGS; i++) regs[i] = '0;
  end

  // x0 is enforced at the write: entry 0 is never written, so it is a constant
  // zero and the write-through mux cannot fire for address 0 either.
  wire wr_en = we && (wa != {AW{1'b0}});

  always_ff @(posedge clk) begin
    if (wr_en) regs[wa] <= wd;
  end

  assign rd1 = (wr_en && (wa == ra1)) ? wd : regs[ra1];
  assign rd2 = (wr_en && (wa == ra2)) ? wd : regs[ra2];

`ifdef FORMAL
  // 1. x0 always reads as zero, on every port.
  always_comb begin
    if (ra1 == {AW{1'b0}}) assert (rd1 == {XLEN{1'b0}});
    if (ra2 == {AW{1'b0}}) assert (rd2 == {XLEN{1'b0}});
  end

  // 2. Write-through: a read of the address being written returns the new data.
  always_comb begin
    if (wr_en && ra1 == wa) assert (rd1 == wd);
    if (wr_en && ra2 == wa) assert (rd2 == wd);
  end

  // 3. Read ports agree whenever their addresses agree.
  always_comb begin
    if (ra1 == ra2) assert (rd1 == rd2);
  end

  // 4. Storage is stable: an entry may only change on a write to that entry.
  //    Checked on one symbolic address.
  (* anyconst *) logic [AW-1:0] f_addr;
  logic                f_past_valid = 1'b0;
  logic [XLEN-1:0]     f_prev;

  always_ff @(posedge clk) begin
    f_past_valid <= 1'b1;
    f_prev       <= regs[f_addr];
  end

  always_ff @(posedge clk) begin
    if (f_past_valid && !($past(wr_en) && $past(wa) == f_addr))
      assert (regs[f_addr] == f_prev);
  end
`endif

endmodule

`default_nettype wire
